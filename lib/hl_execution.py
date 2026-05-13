"""
Hyperliquid order execution — P11 assisted live trading.
Pure functions only. No Flask, no app.py imports.
All public functions return {"success": bool, ...} — never raise.

Required packages: eth-account>=0.8.0, msgpack>=1.0.0
Required .env:     HL_PRIVATE_KEY (0x-prefixed 64-char hex private key)
                   HL_WALLET_ADDRESS (0x-prefixed 42-char Ethereum address)
"""
from __future__ import annotations

import sys
import time
import requests
import msgpack
from eth_account import Account
from eth_hash.auto import keccak

from lib.hyperliquid_client import fetch_hl_meta_and_ctxs

HL_EXCHANGE_URL = "https://api.hyperliquid.xyz/exchange"
HL_INFO_URL     = "https://api.hyperliquid.xyz/info"
_TIMEOUT        = 10
_SLIPPAGE       = 0.05   # 5% slippage for IOC market-close orders (kill switch)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _float_to_wire(x: float) -> str:
    """Format float to Hyperliquid wire format — 8 dp, no trailing zeros."""
    if x == 0:
        return "0"
    return f"{x:.8f}".rstrip("0").rstrip(".")


def _round_size(size: float, sz_decimals: int) -> float:
    factor = 10 ** sz_decimals
    return round(size * factor) / factor


def _get_asset_info(coin: str) -> tuple[int, int]:
    """
    Return (asset_index, sz_decimals) for coin using cached universe.
    coin: base name e.g. "BTC" not "BTC_USDC".
    Returns (-1, 0) if not found.
    """
    try:
        universe, _ = fetch_hl_meta_and_ctxs()
        for i, asset in enumerate(universe):
            if asset.get("name") == coin:
                return i, int(asset.get("szDecimals", 0))
    except Exception as e:
        print(f"[hl_exec] _get_asset_info({coin}) error: {e}", file=sys.stderr)
    return -1, 0


def _action_hash(action: dict, nonce: int, vault_address: str | None = None) -> bytes:
    """Compute connection_id bytes for EIP-712 signing."""
    data = msgpack.packb(action, use_bin_type=True)
    data += nonce.to_bytes(8, "big")
    if vault_address is None:
        data += b"\x00"
    else:
        data += b"\x01" + bytes.fromhex(vault_address[2:])
    return keccak(data)


def _sign_action(private_key: str, action: dict, nonce: int) -> dict:
    """EIP-712 sign a Hyperliquid action using sign_typed_data. Returns {r, s, v}."""
    connection_id = _action_hash(action, nonce)
    signed = Account.sign_typed_data(
        private_key,
        domain_data={
            "name": "Exchange",
            "version": "1",
            "chainId": 1337,
            "verifyingContract": "0x0000000000000000000000000000000000000000",
        },
        message_types={
            "Agent": [
                {"name": "source",       "type": "string"},
                {"name": "connectionId", "type": "bytes32"},
            ],
        },
        message_data={
            "source":       "a",
            "connectionId": connection_id,
        },
    )
    return {"r": hex(signed.r), "s": hex(signed.s), "v": signed.v}


def _post_exchange(action: dict, private_key: str, nonce: int | None = None) -> dict:
    """Sign and POST action to /exchange. Returns parsed JSON response."""
    if nonce is None:
        nonce = int(time.time() * 1000)
    sig     = _sign_action(private_key, action, nonce)
    payload = {"action": action, "nonce": nonce, "signature": sig}
    resp    = requests.post(HL_EXCHANGE_URL, json=payload, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Public execution functions
# ---------------------------------------------------------------------------

def place_limit_order(
    coin: str,
    is_buy: bool,
    size: float,
    limit_px: float,
    private_key: str,
    reduce_only: bool = False,
    tif: str = "Gtc",
) -> dict:
    """
    Place a limit order on Hyperliquid.
    coin:      base asset name e.g. "BTC"
    is_buy:    True = long / buy, False = short / sell
    size:      in base currency (contracts), e.g. 0.001 BTC
    limit_px:  limit price in USDC
    tif:       "Gtc" | "Ioc" | "Alo"
    Returns:   {"success": bool, "order_id": int|None, "status": str, "error": str|None}
    """
    try:
        asset_idx, sz_dec = _get_asset_info(coin)
        if asset_idx < 0:
            return {"success": False, "error": f"Asset not found on Hyperliquid: {coin}"}

        rounded_size = _round_size(size, sz_dec)
        if rounded_size <= 0:
            return {"success": False, "error": f"Size rounds to zero at {sz_dec} decimal places"}

        action = {
            "type": "order",
            "orders": [{
                "a": asset_idx,
                "b": is_buy,
                "p": _float_to_wire(limit_px),
                "s": _float_to_wire(rounded_size),
                "r": reduce_only,
                "t": {"limit": {"tif": tif}},
            }],
            "grouping": "na",
        }
        result = _post_exchange(action, private_key)

        if result.get("status") == "ok":
            statuses = result.get("response", {}).get("data", {}).get("statuses", [{}])
            st = statuses[0] if statuses else {}
            if "resting" in st:
                return {"success": True, "order_id": st["resting"].get("oid"), "status": "resting"}
            if "filled" in st:
                fill = st["filled"]
                return {"success": True, "order_id": None, "status": "filled",
                        "avg_px": fill.get("avgPx"), "total_sz": fill.get("totalSz")}
            if "error" in st:
                return {"success": False, "error": st["error"]}

        return {"success": False, "error": str(result)}
    except Exception as e:
        print(f"[hl_exec] place_limit_order({coin}) error: {e}", file=sys.stderr)
        return {"success": False, "error": str(e)}


def cancel_all_orders(wallet_address: str, private_key: str) -> dict:
    """Cancel all open orders for this wallet in a single batch request."""
    try:
        universe, _ = fetch_hl_meta_and_ctxs()
        coin_to_idx = {u["name"]: i for i, u in enumerate(universe)}

        resp = requests.post(HL_INFO_URL,
            json={"type": "openOrders", "user": wallet_address}, timeout=_TIMEOUT)
        resp.raise_for_status()
        orders = resp.json() or []

        if not orders:
            return {"success": True, "cancelled": 0}

        cancels = []
        for o in orders:
            idx = coin_to_idx.get(o.get("coin", ""), -1)
            if idx >= 0:
                cancels.append({"a": idx, "o": o["oid"]})

        if not cancels:
            return {"success": True, "cancelled": 0}

        result = _post_exchange({"type": "cancel", "cancels": cancels}, private_key)
        ok = result.get("status") == "ok"
        return {"success": ok, "cancelled": len(cancels), "raw": result if not ok else None}
    except Exception as e:
        print(f"[hl_exec] cancel_all_orders error: {e}", file=sys.stderr)
        return {"success": False, "cancelled": 0, "error": str(e)}


def close_all_positions(wallet_address: str, private_key: str) -> dict:
    """
    Close all open positions with IOC limit orders at _SLIPPAGE% slippage.
    Core of the kill switch — closes longs by selling, shorts by buying.
    """
    try:
        universe, asset_ctxs = fetch_hl_meta_and_ctxs()
        mark_prices = {}
        for i, u in enumerate(universe):
            if i < len(asset_ctxs):
                mark_prices[u["name"]] = float(asset_ctxs[i].get("markPx") or 0)

        resp = requests.post(HL_INFO_URL,
            json={"type": "clearinghouseState", "user": wallet_address}, timeout=_TIMEOUT)
        resp.raise_for_status()
        state = resp.json()

        closed = 0
        errors = []

        for entry in state.get("assetPositions", []):
            pos  = entry.get("position", {})
            coin = pos.get("coin", "")
            szi  = float(pos.get("szi") or 0)
            if szi == 0:
                continue

            is_long  = szi > 0
            size     = abs(szi)
            mark_px  = mark_prices.get(coin) or float(pos.get("entryPx") or 1)

            # IOC at aggressive slippage to guarantee fill
            close_px = mark_px * (1 - _SLIPPAGE) if is_long else mark_px * (1 + _SLIPPAGE)

            result = place_limit_order(
                coin=coin,
                is_buy=not is_long,
                size=size,
                limit_px=close_px,
                private_key=private_key,
                reduce_only=True,
                tif="Ioc",
            )
            if result.get("success"):
                closed += 1
            else:
                errors.append(f"{coin}: {result.get('error', 'unknown')}")

        return {
            "success": len(errors) == 0,
            "positions_closed": closed,
            "errors": errors,
        }
    except Exception as e:
        print(f"[hl_exec] close_all_positions error: {e}", file=sys.stderr)
        return {"success": False, "positions_closed": 0, "error": str(e)}


def kill_switch(wallet_address: str, private_key: str) -> dict:
    """
    Emergency kill switch: cancel all open orders then close all positions.
    Always available regardless of LIVE_TRADING_ENABLED.
    """
    try:
        cancel_result = cancel_all_orders(wallet_address, private_key)
        close_result  = close_all_positions(wallet_address, private_key)
        return {
            "success":          cancel_result.get("success") and close_result.get("success"),
            "orders_cancelled": cancel_result.get("cancelled", 0),
            "positions_closed": close_result.get("positions_closed", 0),
            "errors":           (close_result.get("errors") or []),
        }
    except Exception as e:
        print(f"[hl_exec] kill_switch error: {e}", file=sys.stderr)
        return {"success": False, "error": str(e)}


def get_positions(wallet_address: str) -> dict:
    """Fetch current open positions. Returns {"success": bool, "positions": [...]}"""
    try:
        resp = requests.post(HL_INFO_URL,
            json={"type": "clearinghouseState", "user": wallet_address}, timeout=_TIMEOUT)
        resp.raise_for_status()
        state = resp.json()
        positions = []
        for entry in state.get("assetPositions", []):
            pos = entry.get("position", {})
            szi = float(pos.get("szi") or 0)
            if szi == 0:
                continue
            positions.append({
                "coin":           pos.get("coin"),
                "size":           szi,
                "side":           "LONG" if szi > 0 else "SHORT",
                "entry_px":       float(pos.get("entryPx") or 0),
                "unrealized_pnl": float(pos.get("unrealizedPnl") or 0),
                "leverage":       pos.get("leverage"),
            })
        return {
            "success":        True,
            "positions":      positions,
            "margin_summary": state.get("marginSummary", {}),
        }
    except Exception as e:
        print(f"[hl_exec] get_positions error: {e}", file=sys.stderr)
        return {"success": False, "positions": [], "error": str(e)}


def get_open_orders(wallet_address: str) -> dict:
    """Fetch all open orders. Returns {"success": bool, "orders": [...]}"""
    try:
        resp = requests.post(HL_INFO_URL,
            json={"type": "openOrders", "user": wallet_address}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return {"success": True, "orders": resp.json() or []}
    except Exception as e:
        print(f"[hl_exec] get_open_orders error: {e}", file=sys.stderr)
        return {"success": False, "orders": [], "error": str(e)}
