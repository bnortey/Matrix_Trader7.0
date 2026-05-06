"""
Canonical exchange-agnostic market context.

Adapters translate raw exchange data into this schema. Agents read only this
schema, never raw exchange dictionaries.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ExchangeContext:
    """
    Normalized market data for one symbol on one exchange.

    All adapters must populate every field or use the documented safe default.
    """

    # Identity
    exchange: str = ""
    symbol: str = ""
    base_asset: str = ""
    quote_asset: str = "USDT"
    quote_is_usdc: bool = False

    # Price
    last_price: float = 0.0
    mark_price: float = 0.0
    index_price: float = 0.0
    mid_price: float = 0.0

    # Momentum
    change_24h_pct: float = 0.0
    change_4h_pct: float = 0.0
    change_1h_pct: float = 0.0

    # Volume and open interest
    volume_24h: float = 0.0
    open_interest: float = 0.0

    # Funding
    funding_rate: float = 0.0
    next_funding_minutes: Optional[int] = None
    funding_interval_hours: int = 8

    # Order book
    best_bid: float = 0.0
    best_ask: float = 0.0
    bid_depth_usd: float = 0.0
    ask_depth_usd: float = 0.0
    spread_pct: float = 0.0
    imbalance: float = 0.0

    # Klines
    klines_1h: Optional[object] = None

    # Exchange-specific risk context
    max_leverage: int = 20
    adl_risk: bool = False
    exchange_stress_notice: bool = False
    basis_vs_binance_bps: float = 0.0
    venue_leader: str = "unknown"

    # Exchange characteristics
    is_retail_fragmented: bool = False
    is_institutional: bool = False
    supports_mark_price: bool = True
    typical_spread_bps: float = 5.0

    # Data quality
    data_quality: str = "current"
    data_stale: bool = False
    kline_depth_1h: int = 0
    kline_depth_4h: int = 0

    # Existing MT pass-through fields
    rsi_1h: float = 50.0
    trend_score: int = 0
    atr_pct: float = 0.0
    volatility: str = "medium"
    direction: str = "LONG"
