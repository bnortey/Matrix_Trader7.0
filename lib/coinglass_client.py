import os
import sys
import time
from threading import Lock
from typing import Any

import requests


BASE_URL = "https://open-api-v4.coinglass.com"
DEFAULT_EXCHANGES = "MEXC,Binance,OKX,Bybit"
_CACHE_TTL_SECONDS = 300
_TIMEOUT_SECONDS = 8

_cache_lock = Lock()
_coins_cache: dict[str, Any] = {"expires_at": 0.0, "data": {}}
_symbol_cache: dict[str, dict[str, Any]] = {}
_disabled_until = 0.0


def _api_key() -> str | None:
    key = os.getenv("COINGLASS_API_KEY", "").strip()
    return key or None


def coinglass_enabled() -> bool:
    return bool(_api_key())


def _request(path: str, params: dict | None = None) -> Any:
    global _disabled_until
    key = _api_key()
    if not key:
        return None
    if _disabled_until > time.time():
        return None
    try:
        resp = requests.get(
            f"{BASE_URL}{path}",
            headers={
                "CG-API-KEY": key,
                "accept": "application/json",
            },
            params=params or {},
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
        code = str(payload.get("code", "0"))
        if code not in {"0", "200"}:
            msg = str(payload.get("msg") or payload)
            print(f"coinglass api error [{path}]: {msg}", file=sys.stderr)
            if "invalid api key" in msg.lower():
                _disabled_until = time.time() + _CACHE_TTL_SECONDS
            return None
        return payload.get("data")
    except Exception as e:
        print(f"coinglass request error [{path}]: {e}", file=sys.stderr)
        return None


def _rows_from_payload(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("list", "data", "rows", "items"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
    return []


def _num(row: dict, *keys: str) -> float | None:
    for key in keys:
        if key not in row:
            continue
        try:
            value = row.get(key)
            if value in (None, ""):
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _normalize_coin_row(row: dict) -> dict | None:
    symbol = str(row.get("symbol") or row.get("base_asset") or row.get("coin") or "").upper().strip()
    if not symbol:
        return None

    open_interest_usd = _num(row, "open_interest_usd", "openInterestUsd", "open_interest", "openInterest")
    market_cap_usd = _num(row, "market_cap_usd", "marketCapUsd", "market_cap", "marketCap")
    oi_market_cap_ratio = _num(
        row,
        "open_interest_market_cap_ratio",
        "openInterestMarketCapRatio",
        "oi_market_cap_ratio",
    )
    if oi_market_cap_ratio is None and open_interest_usd is not None and market_cap_usd:
        oi_market_cap_ratio = open_interest_usd / market_cap_usd

    return {
        "coinglass_symbol": symbol,
        "coinglass_current_price": _num(row, "current_price", "currentPrice", "price"),
        "coinglass_open_interest_usd": open_interest_usd,
        "coinglass_market_cap_usd": market_cap_usd,
        "coinglass_oi_market_cap_ratio": oi_market_cap_ratio,
        "coinglass_funding_oi_weighted": _num(
            row,
            "avg_funding_rate_by_oi",
            "avgFundingRateByOi",
            "funding_rate_oi_weighted",
        ),
        "coinglass_funding_vol_weighted": _num(
            row,
            "avg_funding_rate_by_vol",
            "avgFundingRateByVol",
            "funding_rate_vol_weighted",
        ),
        "coinglass_volume_usd": _num(row, "volume_usd", "volumeUsd", "turnover_usd", "turnoverUsd"),
    }


def _sum_rows(rows: list[dict], *keys: str) -> float | None:
    all_row = _pick_exchange(rows, "All")
    if all_row:
        return _num(all_row, *keys)
    total = 0.0
    seen = False
    for row in rows:
        value = _num(row, *keys)
        if value is not None:
            total += value
            seen = True
    return total if seen else None


def _pick_exchange(rows: list[dict], exchange: str) -> dict | None:
    needle = exchange.lower()
    for row in rows:
        name = str(row.get("exchange") or row.get("exchange_name") or row.get("exchangeName") or "").lower()
        if name == needle:
            return row
    return None


def _normalize_symbol_derivatives(symbol: str, oi_rows: list[dict], funding_rows: list[dict], liq_rows: list[dict]) -> dict:
    base = symbol.split("_")[0].replace("USDT", "").upper()
    mexc_oi = _pick_exchange(oi_rows, "MEXC")
    mexc_funding = _pick_exchange(funding_rows, "MEXC")
    mexc_liq = _pick_exchange(liq_rows, "MEXC")

    oi_total = _sum_rows(oi_rows, "open_interest_usd", "openInterestUsd", "open_interest", "openInterest", "oiUsd")
    liq_long = _sum_rows(liq_rows, "long_liquidation_usd", "longLiquidation_usd", "longLiquidationUsd", "longLiquidation", "longVolUsd", "longVol")
    liq_short = _sum_rows(liq_rows, "short_liquidation_usd", "shortLiquidation_usd", "shortLiquidationUsd", "shortLiquidation", "shortVolUsd", "shortVol")

    return {
        "coinglass_available": bool(oi_rows or funding_rows or liq_rows),
        "coinglass_symbol": base,
        "coinglass_open_interest_usd": oi_total,
        "coinglass_mexc_open_interest_usd": _num(mexc_oi or {}, "open_interest_usd", "openInterestUsd", "open_interest", "openInterest", "oiUsd"),
        "coinglass_oi_change_1h_pct": _num(_pick_exchange(oi_rows, "All") or {}, "open_interest_change_percent_1h", "openInterestChangePercent1h"),
        "coinglass_oi_change_24h_pct": _num(_pick_exchange(oi_rows, "All") or {}, "open_interest_change_percent_24h", "openInterestChangePercent24h"),
        "coinglass_funding_oi_weighted": _num(mexc_funding or {}, "funding_rate", "fundingRate", "rate", "funding"),
        "coinglass_funding_interval_hours": _num(mexc_funding or {}, "funding_rate_interval", "fundingRateInterval"),
        "coinglass_liq_long_24h_usd": liq_long,
        "coinglass_liq_short_24h_usd": liq_short,
        "coinglass_mexc_liq_long_24h_usd": _num(mexc_liq or {}, "long_liquidation_usd", "longLiquidation_usd", "longLiquidationUsd", "longLiquidation", "longVolUsd", "longVol"),
        "coinglass_mexc_liq_short_24h_usd": _num(mexc_liq or {}, "short_liquidation_usd", "shortLiquidation_usd", "shortLiquidationUsd", "shortLiquidation", "shortVolUsd", "shortVol"),
    }


def get_coin_market_snapshot(force: bool = False) -> dict[str, dict]:
    """
    Return CoinGlass futures market metrics keyed by base symbol (BTC, ETH, ...).
    Missing API key, plan limits, or transient API errors return an empty dict.
    """
    now = time.time()
    with _cache_lock:
        if not force and _coins_cache["expires_at"] > now:
            return dict(_coins_cache["data"])

    if not coinglass_enabled():
        return {}

    result: dict[str, dict] = {}
    for page in range(1, 6):
        data = _request(
            "/api/futures/coins-markets",
            params={
                "exchange_list": DEFAULT_EXCHANGES,
                "per_page": 200,
                "page": page,
            },
        )
        rows = _rows_from_payload(data)
        if not rows:
            break
        for row in rows:
            normalized = _normalize_coin_row(row)
            if normalized:
                result[normalized["coinglass_symbol"]] = normalized
        if len(rows) < 200:
            break

    with _cache_lock:
        _coins_cache["data"] = result
        _coins_cache["expires_at"] = now + _CACHE_TTL_SECONDS
    return dict(result)


def enrich_symbol_with_coinglass(symbol: str, snapshot: dict[str, dict] | None = None) -> dict:
    base = symbol.split("_")[0].replace("USDT", "").upper()
    data = (snapshot or get_coin_market_snapshot()).get(base) or {}
    if not data:
        return {
            "coinglass_available": False,
            "coinglass_symbol": base,
        }
    out = dict(data)
    out["coinglass_available"] = True
    return out


def get_symbol_derivatives_context(symbol: str, force: bool = False) -> dict:
    """
    Return per-symbol CoinGlass OI/funding/liquidation context.
    This is intended for enriched top candidates, not all 800+ market rows.
    """
    base = symbol.split("_")[0].replace("USDT", "").upper()
    now = time.time()
    with _cache_lock:
        cached = _symbol_cache.get(base)
        if cached and not force and cached.get("expires_at", 0) > now:
            return dict(cached.get("data") or {})

    if not coinglass_enabled():
        return {"coinglass_available": False, "coinglass_symbol": base}

    oi_rows = _rows_from_payload(_request("/api/futures/open-interest/exchange-list", {"symbol": base}))
    funding_payload_rows = _rows_from_payload(_request("/api/futures/funding-rate/exchange-list", {"symbol": base}))
    funding_rows: list[dict] = []
    for row in funding_payload_rows:
        if str(row.get("symbol") or "").upper() != base:
            continue
        for item in row.get("stablecoin_margin_list") or []:
            if isinstance(item, dict):
                funding_rows.append(item)
        break
    liq_rows = _rows_from_payload(_request("/api/futures/liquidation/exchange-list", {"symbol": base, "range": "24h"}))
    data = _normalize_symbol_derivatives(symbol, oi_rows, funding_rows, liq_rows)

    with _cache_lock:
        _symbol_cache[base] = {"expires_at": now + _CACHE_TTL_SECONDS, "data": data}
    return dict(data)
