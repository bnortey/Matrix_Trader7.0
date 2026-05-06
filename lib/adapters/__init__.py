"""
Exchange adapter registry.

Adding a new exchange should mean adding one adapter file and registering it
here. Agents only consume the normalized ExchangeContext contract.
"""

from typing import Optional

from lib.exchange_context import ExchangeContext


_ADAPTERS = {}


def _load_adapters():
    """Lazy-load adapters to avoid import errors from optional dependencies."""
    global _ADAPTERS
    if _ADAPTERS:
        return

    try:
        from lib.adapters.mexc import MexcAdapter
        _ADAPTERS["MEXC"] = MexcAdapter()
    except Exception as e:
        print(f"[adapters] MEXC adapter unavailable: {e}")

    try:
        from lib.adapters.hyperliquid import HyperliquidAdapter
        _ADAPTERS["HYPERLIQUID"] = HyperliquidAdapter()
    except Exception as e:
        print(f"[adapters] Hyperliquid adapter unavailable: {e}")


def get_adapter(exchange: str):
    """Return the adapter for an exchange key, or None if unavailable."""
    _load_adapters()
    return _ADAPTERS.get((exchange or "").upper())


def normalize(
    exchange: str,
    ticker_data: dict,
    klines=None,
    depth_data: Optional[dict] = None,
    enriched_fields: Optional[dict] = None,
) -> Optional[ExchangeContext]:
    """Convert raw exchange data into canonical ExchangeContext."""
    adapter = get_adapter(exchange)
    if not adapter:
        return None
    try:
        return adapter.normalize(
            ticker_data=ticker_data,
            klines=klines,
            depth_data=depth_data or {},
            enriched_fields=enriched_fields or {},
        )
    except Exception as e:
        print(f"[adapters] {exchange} normalize error: {e}")
        return None
