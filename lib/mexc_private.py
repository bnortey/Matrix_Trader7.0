"""
MEXC private contract API client.
Pure functions — no Flask, no app.py imports.
All errors are caught and logged to stderr; functions never raise.
MEXC keys are passed as arguments — never read from os.getenv here.
"""

import hashlib
import hmac
import sys
import time
from urllib.parse import quote

import requests

_BASE = "https://contract.mexc.com/api/v1"


def _query_string(params: dict | None = None) -> str:
    if not params:
        return ""
    parts = []
    for key in sorted(params):
        value = params[key]
        if value is None:
            continue
        parts.append(f"{key}={quote(str(value), safe='')}")
    return "&".join(parts)


def _sign(api_key: str, api_secret: str, params: dict | None = None) -> dict:
    timestamp = str(int(time.time() * 1000))
    params_str = _query_string(params)
    signature_target = api_key + timestamp + params_str
    signature = hmac.new(
        api_secret.encode(), signature_target.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "ApiKey": api_key,
        "Request-Time": timestamp,
        "Signature": signature,
        "Content-Type": "application/json",
    }


def get_account_assets(api_key: str, api_secret: str) -> dict:
    """
    Returns equity, available_balance, unrealized_pnl, currency.
    Returns {} on any error — never raises.
    """
    try:
        headers = _sign(api_key, api_secret)
        resp = requests.get(
            f"{_BASE}/private/account/assets",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            print(f"[mexc_private] assets error: {data.get('message')}", file=sys.stderr)
            return {}
        assets = data.get("data") or {}
        if isinstance(assets, list):
            # API returns list of asset objects — find USDT
            for a in assets:
                if a.get("currency", "").upper() == "USDT":
                    assets = a
                    break
            else:
                assets = assets[0] if assets else {}
        return {
            "equity": float(assets.get("equity", 0)),
            "available_balance": float(assets.get("availableBalance", 0)),
            "unrealized_pnl": float(assets.get("unrealisedPnl", 0)),
            "currency": assets.get("currency", "USDT"),
        }
    except Exception as e:
        print(f"[mexc_private] get_account_assets failed: {e}", file=sys.stderr)
        return {}


def get_open_positions(api_key: str, api_secret: str) -> list:
    """
    Returns list of open position dicts.
    Returns [] on any error — never raises.
    """
    try:
        headers = _sign(api_key, api_secret)
        resp = requests.get(
            f"{_BASE}/private/position/open_positions",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            print(f"[mexc_private] positions error: {data.get('message')}", file=sys.stderr)
            return []
        return data.get("data") or []
    except Exception as e:
        print(f"[mexc_private] get_open_positions failed: {e}", file=sys.stderr)
        return []


def get_account_summary(api_key: str, api_secret: str) -> dict:
    """
    Combines assets + positions into one summary dict.
    Returns {} on any error — never raises.
    """
    try:
        assets = get_account_assets(api_key, api_secret)
        positions = get_open_positions(api_key, api_secret)
        if not assets and not positions:
            return {}
        return {
            "assets": assets,
            "open_positions": positions,
            "open_position_count": len(positions),
        }
    except Exception as e:
        print(f"[mexc_private] get_account_summary failed: {e}", file=sys.stderr)
        return {}
