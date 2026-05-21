"""
Order flow analysis for MEXC perpetual contracts.
Pure functions only — no Flask, no app.py imports.
All public functions return dicts and never raise.

Data sources:
  GET /contract/deals/{symbol}?limit=N   — recent tape (T:1=buy, T:2=sell)
  GET /contract/depth/{symbol}?limit=N   — order book snapshot [price, qty, count]

Confirmation thresholds (tunable via flow_confirm() kwargs):
  delta_threshold   — minimum |delta_pct| to count as directional (default 10%)
  imbalance_threshold — minimum bid/ask ratio skew to count as directional (default 0.55)
  min_score         — minimum combined score (0–100) to confirm entry (default 50)
"""
from __future__ import annotations

import sys
import requests

_MEXC_BASE = "https://contract.mexc.com/api/v1"
_TIMEOUT   = 8


# ---------------------------------------------------------------------------
# Raw data fetchers
# ---------------------------------------------------------------------------

def fetch_recent_trades(symbol: str, limit: int = 500) -> list[dict]:
    """
    Fetch recent tape from MEXC /contract/deals.
    Returns list of {price, volume, side, ts} or [] on failure.
    side: "buy" (taker aggressor buying) | "sell" (taker aggressor selling)
    """
    try:
        resp = requests.get(
            f"{_MEXC_BASE}/contract/deals/{symbol}",
            params={"limit": limit},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            return []
        raw = body.get("data") or []
        trades = []
        for t in raw:
            p = t.get("p")
            v = t.get("v")
            trade_type = t.get("T")  # 1 = buy, 2 = sell
            ts = t.get("t", 0)
            if p is None or v is None or trade_type is None:
                continue
            trades.append({
                "price":  float(p),
                "volume": float(v),
                "side":   "buy" if trade_type == 1 else "sell",
                "ts":     int(ts),
            })
        return trades
    except Exception as e:
        print(f"[order_flow] fetch_recent_trades({symbol}) error: {e}", file=sys.stderr)
        return []


def fetch_depth_snapshot(symbol: str, levels: int = 20) -> dict:
    """
    Fetch order book snapshot from MEXC /contract/depth.
    Returns {bids: [[px, qty]], asks: [[px, qty]]} or empty lists on failure.
    """
    try:
        resp = requests.get(
            f"{_MEXC_BASE}/contract/depth/{symbol}",
            params={"limit": levels},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            return {"bids": [], "asks": []}
        data = body.get("data") or {}
        # Each level: [price, qty, order_count] — we only need price and qty
        bids = [[lvl[0], lvl[1]] for lvl in (data.get("bids") or []) if len(lvl) >= 2]
        asks = [[lvl[0], lvl[1]] for lvl in (data.get("asks") or []) if len(lvl) >= 2]
        return {"bids": bids, "asks": asks}
    except Exception as e:
        print(f"[order_flow] fetch_depth_snapshot({symbol}) error: {e}", file=sys.stderr)
        return {"bids": [], "asks": []}


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------

def compute_delta(trades: list[dict]) -> dict:
    """
    Compute buy/sell delta from a tape list.
    Returns:
      buy_vol, sell_vol   — total volume by side
      delta               — buy_vol - sell_vol (positive = net buying)
      delta_pct           — delta as % of total volume (-100 to +100)
      total_vol           — buy_vol + sell_vol
      trade_count         — number of trades analysed
    """
    buy_vol  = sum(t["volume"] for t in trades if t["side"] == "buy")
    sell_vol = sum(t["volume"] for t in trades if t["side"] == "sell")
    total    = buy_vol + sell_vol
    delta    = buy_vol - sell_vol
    delta_pct = (delta / total * 100) if total > 0 else 0.0
    return {
        "buy_vol":     buy_vol,
        "sell_vol":    sell_vol,
        "delta":       delta,
        "delta_pct":   round(delta_pct, 2),
        "total_vol":   total,
        "trade_count": len(trades),
    }


def compute_depth_walls(depth: dict, wall_pct_threshold: float = 0.05) -> dict:
    """
    Analyse order book for significant walls and overall imbalance.

    wall_pct_threshold: a level is a "wall" if its qty >= this fraction
                        of total side volume (default 5%).

    Returns:
      bid_total, ask_total   — total volume on each side
      imbalance_ratio        — bid_total / (bid_total + ask_total), 0–1
                               >0.5 = bid-heavy (bullish), <0.5 = ask-heavy (bearish)
      bid_walls              — list of {price, qty} for large bid levels
      ask_walls              — list of {price, qty} for large ask levels
      largest_bid_wall       — single largest bid level or None
      largest_ask_wall       — single largest ask level or None
    """
    bids = depth.get("bids") or []
    asks = depth.get("asks") or []

    bid_total = sum(lvl[1] for lvl in bids)
    ask_total = sum(lvl[1] for lvl in asks)
    total     = bid_total + ask_total

    imbalance_ratio = (bid_total / total) if total > 0 else 0.5

    def _walls(levels, side_total):
        if not levels or side_total == 0:
            return []
        threshold = side_total * wall_pct_threshold
        return [{"price": lvl[0], "qty": lvl[1]}
                for lvl in levels if lvl[1] >= threshold]

    bid_walls = _walls(bids, bid_total)
    ask_walls = _walls(asks, ask_total)

    largest_bid = max(bid_walls, key=lambda w: w["qty"]) if bid_walls else None
    largest_ask = max(ask_walls, key=lambda w: w["qty"]) if ask_walls else None

    return {
        "bid_total":         bid_total,
        "ask_total":         ask_total,
        "imbalance_ratio":   round(imbalance_ratio, 4),
        "bid_walls":         bid_walls,
        "ask_walls":         ask_walls,
        "largest_bid_wall":  largest_bid,
        "largest_ask_wall":  largest_ask,
    }


# ---------------------------------------------------------------------------
# Entry confirmation
# ---------------------------------------------------------------------------

def flow_confirm(
    symbol:               str,
    direction:            str,
    price:                float,
    trade_limit:          int   = 500,
    depth_levels:         int   = 20,
    delta_threshold:      float = 10.0,
    imbalance_threshold:  float = 0.55,
    min_score:            float = 50.0,
) -> dict:
    """
    Decide whether current order flow supports entry in `direction`.

    Scoring (0–100):
      Delta score  (0–50): based on delta_pct magnitude and direction
      Depth score  (0–50): based on book imbalance favouring direction

    Returns:
      confirmed    — bool, True if score >= min_score AND direction-aligned
      score        — float 0–100
      delta        — compute_delta() result
      depth        — compute_depth_walls() result
      reasons      — list[str] explaining the decision
      symbol       — echoed back
      direction    — echoed back
    """
    try:
        trades = fetch_recent_trades(symbol, limit=trade_limit)
        depth  = fetch_depth_snapshot(symbol, levels=depth_levels)

        delta_data = compute_delta(trades)
        depth_data = compute_depth_walls(depth)

        reasons: list[str] = []
        delta_score = 0.0
        depth_score = 0.0

        d_pct  = delta_data["delta_pct"]
        imbal  = depth_data["imbalance_ratio"]
        is_long = direction.upper() == "LONG"

        # ── Delta scoring ──────────────────────────────────────────────
        if delta_data["trade_count"] == 0:
            reasons.append("no tape data — delta neutral")
        else:
            aligned = (is_long and d_pct > 0) or (not is_long and d_pct < 0)
            magnitude = abs(d_pct)

            if not aligned:
                reasons.append(f"delta opposes direction ({d_pct:+.1f}%)")
            elif magnitude >= delta_threshold * 2:
                delta_score = 50.0
                reasons.append(f"strong delta alignment ({d_pct:+.1f}%)")
            elif magnitude >= delta_threshold:
                delta_score = 30.0
                reasons.append(f"moderate delta alignment ({d_pct:+.1f}%)")
            else:
                delta_score = 10.0
                reasons.append(f"weak delta alignment ({d_pct:+.1f}%)")

        # ── Depth scoring ──────────────────────────────────────────────
        if not depth_data["bid_total"] and not depth_data["ask_total"]:
            reasons.append("no depth data — book neutral")
        else:
            bid_heavy = imbal >= imbalance_threshold
            ask_heavy = imbal <= (1 - imbalance_threshold)

            if is_long and bid_heavy:
                depth_score = 50.0 * ((imbal - 0.5) / 0.5)
                reasons.append(f"bid-heavy book ({imbal:.2f} ratio) supports LONG")
            elif not is_long and ask_heavy:
                ask_ratio = 1 - imbal
                depth_score = 50.0 * ((ask_ratio - 0.5) / 0.5)
                reasons.append(f"ask-heavy book ({imbal:.2f} ratio) supports SHORT")
            elif is_long and ask_heavy:
                reasons.append(f"ask-heavy book ({imbal:.2f}) opposes LONG")
            elif not is_long and bid_heavy:
                reasons.append(f"bid-heavy book ({imbal:.2f}) opposes SHORT")
            else:
                reasons.append(f"book neutral ({imbal:.2f} ratio)")

            # Bonus: wall on the right side
            if is_long and depth_data["largest_bid_wall"]:
                w = depth_data["largest_bid_wall"]
                reasons.append(f"bid wall at {w['price']} ({w['qty']:,.0f} contracts)")
            elif not is_long and depth_data["largest_ask_wall"]:
                w = depth_data["largest_ask_wall"]
                reasons.append(f"ask wall at {w['price']} ({w['qty']:,.0f} contracts)")

        no_data = delta_data["trade_count"] == 0 and not depth_data.get("bid_total") and not depth_data.get("ask_total")
        if no_data:
            score = 50.0
            reasons.append("no tape or depth data — treating as neutral (50/100)")
        else:
            score = min(delta_score + depth_score, 100.0)
        confirmed = score >= min_score

        if confirmed:
            reasons.append(f"CONFIRMED — score {score:.0f}/100")
        else:
            reasons.append(f"NOT CONFIRMED — score {score:.0f}/100 (need {min_score:.0f})")

        return {
            "confirmed":  confirmed,
            "score":      round(score, 1),
            "delta":      delta_data,
            "depth":      depth_data,
            "reasons":    reasons,
            "symbol":     symbol,
            "direction":  direction.upper(),
        }

    except Exception as e:
        print(f"[order_flow] flow_confirm({symbol}) error: {e}", file=sys.stderr)
        return {
            "confirmed": False,
            "score":     0.0,
            "delta":     {},
            "depth":     {},
            "reasons":   [f"error: {e}"],
            "symbol":    symbol,
            "direction": direction.upper(),
        }
