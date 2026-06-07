"""
Exchange-aware liquidation price estimates for linear perpetuals.

These functions intentionally model isolated-margin positions. Cross margin
requires account equity, other positions, open-order margin, and funding state,
so callers must pass full account context before treating cross-margin values as
exact.
"""
from __future__ import annotations

import math
import sys
import time
from typing import Any

import requests

from lib.bybit_client import bybit_symbol_to_raw

MEXC_CONTRACT_BASE = "https://contract.mexc.com/api/v1"
BYBIT_BASE = "https://api.bybit.com"
HL_BASE = "https://api.hyperliquid.xyz"

_TIMEOUT = 10
_TTL = 300
_cache: dict[tuple[str, str], dict[str, Any]] = {}


def _cached(key: tuple[str, str]) -> dict[str, Any] | None:
    entry = _cache.get(key)
    if entry and time.time() - float(entry.get("ts", 0)) < _TTL:
        return entry.get("data")
    return None


def _store(key: tuple[str, str], data: dict[str, Any]) -> dict[str, Any]:
    _cache[key] = {"ts": time.time(), "data": data}
    return data


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except Exception:
        return False


def _mexc_detail(symbol: str) -> dict[str, Any] | None:
    key = ("mexc", symbol)
    cached = _cached(key)
    if cached:
        return cached
    try:
        resp = requests.get(
            f"{MEXC_CONTRACT_BASE}/contract/detail",
            params={"symbol": symbol},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            return None
        data = body.get("data") or {}
        return _store(key, data) if isinstance(data, dict) else None
    except Exception as e:
        print(f"[liq] mexc detail {symbol}: {e}", file=sys.stderr)
        return None


def _bybit_risk_limits(symbol: str) -> list[dict[str, Any]]:
    raw_symbol = bybit_symbol_to_raw(symbol)
    key = ("bybit-risk", raw_symbol)
    cached = _cached(key)
    if cached:
        return cached.get("list", [])
    try:
        resp = requests.get(
            f"{BYBIT_BASE}/v5/market/risk-limit",
            params={"category": "linear", "symbol": raw_symbol},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("retCode") != 0:
            return []
        rows = ((body.get("result") or {}).get("list") or [])
        return _store(key, {"list": rows}).get("list", [])
    except Exception as e:
        print(f"[liq] bybit risk-limit {raw_symbol}: {e}", file=sys.stderr)
        return []


def _hl_meta() -> dict[str, Any] | None:
    key = ("hyperliquid", "meta")
    cached = _cached(key)
    if cached:
        return cached
    try:
        resp = requests.post(
            f"{HL_BASE}/info",
            json={"type": "meta"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return _store(key, data) if isinstance(data, dict) else None
    except Exception as e:
        print(f"[liq] hyperliquid meta: {e}", file=sys.stderr)
        return None


def _mexc_tier(detail: dict[str, Any], contracts: float) -> dict[str, Any]:
    custom = detail.get("riskLimitCustom")
    if isinstance(custom, list) and custom:
        rows = sorted(custom, key=lambda r: _num(r.get("maxVol"), math.inf))
        for row in rows:
            if contracts <= _num(row.get("maxVol"), math.inf):
                return {
                    "level": int(_num(row.get("level"), 1)),
                    "mmr": _num(row.get("mmr"), _num(detail.get("maintenanceMarginRate"), 0.0)),
                    "imr": _num(row.get("imr"), _num(detail.get("initialMarginRate"), 0.0)),
                    "max_leverage": _num(row.get("maxLeverage"), _num(detail.get("maxLeverage"), 0.0)),
                    "max_contracts": _num(row.get("maxVol"), contracts),
                }
        row = rows[-1]
        return {
            "level": int(_num(row.get("level"), len(rows))),
            "mmr": _num(row.get("mmr"), _num(detail.get("maintenanceMarginRate"), 0.0)),
            "imr": _num(row.get("imr"), _num(detail.get("initialMarginRate"), 0.0)),
            "max_leverage": _num(row.get("maxLeverage"), _num(detail.get("maxLeverage"), 0.0)),
            "max_contracts": _num(row.get("maxVol"), contracts),
        }

    base_vol = _num(detail.get("riskBaseVol"), _num(detail.get("maxVol"), contracts))
    incr_vol = _num(detail.get("riskIncrVol"), 0.0)
    level = 1
    if incr_vol > 0 and contracts > base_vol:
        level = int(math.ceil((contracts - base_vol) / incr_vol)) + 1
    return {
        "level": level,
        "mmr": _num(detail.get("maintenanceMarginRate"), 0.0) + _num(detail.get("riskIncrMmr"), 0.0) * (level - 1),
        "imr": _num(detail.get("initialMarginRate"), 0.0) + _num(detail.get("riskIncrImr"), 0.0) * (level - 1),
        "max_leverage": _num(detail.get("maxLeverage"), 0.0),
        "max_contracts": base_vol + max(0, level - 1) * incr_vol if incr_vol > 0 else base_vol,
    }


def _bybit_tier(rows: list[dict[str, Any]], notional_usd: float) -> dict[str, Any] | None:
    if not rows:
        return None
    sorted_rows = sorted(rows, key=lambda r: _num(r.get("riskLimitValue"), math.inf))
    for row in sorted_rows:
        if notional_usd <= _num(row.get("riskLimitValue"), math.inf):
            return row
    return sorted_rows[-1]


def _hl_asset_and_tables(symbol: str) -> tuple[dict[str, Any] | None, dict[int, dict[str, Any]]]:
    meta = _hl_meta() or {}
    coin = symbol.upper().replace("_USDC", "").replace("_USDT", "")
    asset = next((a for a in meta.get("universe", []) if str(a.get("name", "")).upper() == coin), None)
    tables: dict[int, dict[str, Any]] = {}
    for pair in meta.get("marginTables", []) or []:
        try:
            tables[int(pair[0])] = pair[1]
        except Exception:
            continue
    return asset, tables


def _hl_tier(asset: dict[str, Any], tables: dict[int, dict[str, Any]], notional_usd: float) -> dict[str, Any]:
    table = tables.get(int(_num(asset.get("marginTableId"), 0)), {})
    tiers = table.get("marginTiers") or []
    chosen = {"lowerBound": "0", "maxLeverage": asset.get("maxLeverage", 3)}
    for tier in tiers:
        if notional_usd >= _num(tier.get("lowerBound"), 0.0):
            chosen = tier
    return {
        "lower_bound": _num(chosen.get("lowerBound"), 0.0),
        "max_leverage": _num(chosen.get("maxLeverage"), _num(asset.get("maxLeverage"), 3.0)),
        "margin_table_id": asset.get("marginTableId"),
    }


def _distance_pct(direction: str, entry_price: float, liquidation_price: float | None) -> float | None:
    if not _positive(entry_price) or liquidation_price is None or liquidation_price <= 0:
        return None
    if direction == "LONG":
        return round((entry_price - liquidation_price) / entry_price * 100, 4)
    return round((liquidation_price - entry_price) / entry_price * 100, 4)


def estimate_liquidation_price(
    *,
    exchange: str,
    symbol: str,
    direction: str,
    entry_price: float,
    leverage: float,
    notional_usd: float,
    margin_mode: str = "isolated",
    extra_margin_usd: float = 0.0,
) -> dict[str, Any]:
    """
    Estimate liquidation for linear perp positions using exchange formulas.

    Returns a dict with success=false when required exchange metadata is not
    available. The result includes metadata so the UI can distinguish exact
    isolated-margin formulas from unsupported cross-margin approximations.
    """
    exchange_key = (exchange or "MEXC").upper()
    direction_key = (direction or "").upper()
    margin_mode = (margin_mode or "isolated").lower()
    entry = float(entry_price or 0.0)
    lev = float(leverage or 0.0)
    notional = float(notional_usd or 0.0)
    extra = max(0.0, float(extra_margin_usd or 0.0))
    if direction_key not in {"LONG", "SHORT"} or entry <= 0 or lev <= 0 or notional <= 0:
        return {"success": False, "error": "missing direction, entry, leverage, or notional"}
    if margin_mode != "isolated":
        return {
            "success": False,
            "error": "exact cross-margin liquidation requires account equity and all open positions",
            "margin_mode": margin_mode,
        }

    side = 1 if direction_key == "LONG" else -1
    qty = notional / entry

    if exchange_key == "MEXC":
        detail = _mexc_detail(symbol)
        if not detail:
            return {"success": False, "error": "MEXC contract metadata unavailable"}
        contract_size = _num(detail.get("contractSize"), 1.0) or 1.0
        contracts = notional / (entry * contract_size)
        tier = _mexc_tier(detail, contracts)
        mmr = tier["mmr"]
        liq_fee_rate = _num(detail.get("liquidationFeeRate"), 0.0)
        maintenance_margin = notional * mmr
        liquidation_fee = notional * liq_fee_rate
        position_margin = notional / lev + extra
        if direction_key == "LONG":
            liq = (maintenance_margin + liquidation_fee - position_margin + entry * qty) / qty
        else:
            liq = (entry * qty - maintenance_margin - liquidation_fee + position_margin) / qty
        formula = "mexc_usdt_isolated_with_liquidation_fee_v1"
        return {
            "success": True,
            "exchange": exchange_key,
            "symbol": symbol,
            "margin_mode": margin_mode,
            "liquidation_price": round(max(liq, 0.0), 12),
            "distance_pct": _distance_pct(direction_key, entry, liq),
            "maintenance_margin_rate": mmr,
            "liquidation_fee_rate": liq_fee_rate,
            "maintenance_margin_usd": round(maintenance_margin, 8),
            "liquidation_fee_usd": round(liquidation_fee, 8),
            "position_margin_usd": round(position_margin, 8),
            "risk_tier": tier,
            "formula": formula,
            "exact": True,
            "notes": "MEXC isolated formula using contract metadata, selected risk tier, and liquidation fee.",
        }

    if exchange_key == "BYBIT":
        rows = _bybit_risk_limits(symbol)
        tier = _bybit_tier(rows, notional)
        if not tier:
            return {"success": False, "error": "Bybit risk-limit metadata unavailable"}
        mmr = _num(tier.get("maintenanceMargin"), 0.0)
        deduction = _num(tier.get("mmDeduction"), 0.0)
        maintenance_margin = max(0.0, notional * mmr - deduction)
        initial_margin = notional / lev
        if direction_key == "LONG":
            liq = entry - ((initial_margin - maintenance_margin) / qty) - (extra / qty)
        else:
            liq = entry + ((initial_margin - maintenance_margin) / qty) + (extra / qty)
        formula = "bybit_usdt_isolated_v1"
        return {
            "success": True,
            "exchange": exchange_key,
            "symbol": symbol,
            "margin_mode": margin_mode,
            "liquidation_price": round(max(liq, 0.0), 12),
            "distance_pct": _distance_pct(direction_key, entry, liq),
            "maintenance_margin_rate": mmr,
            "maintenance_margin_deduction_usd": deduction,
            "maintenance_margin_usd": round(maintenance_margin, 8),
            "position_margin_usd": round(initial_margin + extra, 8),
            "risk_tier": {
                "id": tier.get("id"),
                "risk_limit_value": _num(tier.get("riskLimitValue"), 0.0),
                "max_leverage": _num(tier.get("maxLeverage"), 0.0),
            },
            "formula": formula,
            "exact": True,
            "notes": "Bybit isolated formula using public risk-limit MMR and maintenance deduction; closing-fee drift may remain.",
        }

    if exchange_key == "HYPERLIQUID":
        asset, tables = _hl_asset_and_tables(symbol)
        if not asset:
            return {"success": False, "error": "Hyperliquid asset metadata unavailable"}
        isolated_margin = notional / lev + extra
        liq = None
        tier = _hl_tier(asset, tables, notional)
        # Iterate because HL tier selection depends on position value at the
        # liquidation price, not only current notional.
        for _ in range(6):
            max_lev = max(1.0, _num(tier.get("max_leverage"), _num(asset.get("maxLeverage"), 3.0)))
            maintenance_leverage = max_lev * 2.0
            maint_rate = 1.0 / maintenance_leverage
            maintenance_required = notional * maint_rate
            margin_available = isolated_margin - maintenance_required
            denom = 1.0 - maint_rate * side
            liq = entry - side * (margin_available / qty) / denom
            next_tier = _hl_tier(asset, tables, max(0.0, qty * max(liq, 0.0)))
            if next_tier == tier:
                break
            tier = next_tier
        max_lev = max(1.0, _num(tier.get("max_leverage"), _num(asset.get("maxLeverage"), 3.0)))
        maintenance_leverage = max_lev * 2.0
        maint_rate = 1.0 / maintenance_leverage
        formula = "hyperliquid_isolated_tiered_v1"
        return {
            "success": True,
            "exchange": exchange_key,
            "symbol": symbol,
            "margin_mode": margin_mode,
            "liquidation_price": round(max(liq or 0.0, 0.0), 12),
            "distance_pct": _distance_pct(direction_key, entry, liq),
            "maintenance_margin_rate": maint_rate,
            "maintenance_leverage": maintenance_leverage,
            "maintenance_margin_usd": round(notional * maint_rate, 8),
            "position_margin_usd": round(isolated_margin, 8),
            "risk_tier": tier,
            "formula": formula,
            "exact": True,
            "notes": "Hyperliquid isolated formula with maintenance leverage from margin table; funding can move the live liquidation price.",
        }

    return {"success": False, "error": f"unsupported exchange {exchange_key}"}
