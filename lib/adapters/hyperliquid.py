"""
Hyperliquid to ExchangeContext adapter.

Hyperliquid is USDC-settled. The adapter canonicalizes symbols as COIN_USDC
and filters normal USDC/USDT peg noise from cross-venue basis fields.
"""

from lib.exchange_context import ExchangeContext


class HyperliquidAdapter:
    EXCHANGE_KEY = "HYPERLIQUID"
    NORMAL_FUNDING_INTERVAL_H = 1
    ORACLE_DEVIATION_STRESS_BPS = 50
    USDC_USDT_NOISE_FLOOR_BPS = 8.0

    def normalize(
        self,
        ticker_data: dict,
        klines=None,
        depth_data: dict = None,
        enriched_fields: dict = None,
    ) -> ExchangeContext:
        t = ticker_data or {}
        d = depth_data or {}
        e = enriched_fields or {}

        coin = t.get("coin") or t.get("name") or ""
        if not coin:
            coin = (t.get("symbol") or "").replace("_USDC", "").replace("_USDT", "")
        symbol_canonical = f"{coin}_USDC"

        mark_price = float(t.get("markPx") or t.get("fairPrice") or t.get("lastPrice") or 0)
        oracle_price = float(t.get("oraclePx") or t.get("indexPrice") or mark_price)
        mid_price = float(t.get("midPx") or t.get("lastPrice") or mark_price)
        last_price = mid_price or mark_price

        prev_day = float(t.get("prevDayPx") or last_price)
        change_24h = ((last_price - prev_day) / prev_day * 100) if prev_day else 0.0
        if not change_24h:
            change_24h = float(t.get("riseFallRate") or 0) * 100

        volume_24h = float(t.get("dayNtlVlm") or t.get("vol24h") or t.get("volume24") or 0)
        open_interest = float(t.get("openInterest") or t.get("holdVol") or 0)
        funding_rate = float(t.get("funding") or t.get("fundingRate") or 0)

        premium_bps = 0.0
        if oracle_price and mark_price:
            premium_bps = (mark_price - oracle_price) / oracle_price * 10000
        exchange_stress = abs(premium_bps) > self.ORACLE_DEVIATION_STRESS_BPS

        best_bid = float(d.get("best_bid") or (mid_price * 0.9995 if mid_price else 0))
        best_ask = float(d.get("best_ask") or (mid_price * 1.0005 if mid_price else 0))
        bid_depth_usd = float(d.get("bid_depth_usd") or 0)
        ask_depth_usd = float(d.get("ask_depth_usd") or 0)
        imbalance = float(d.get("imbalance") or 0)
        spread_pct = ((best_ask - best_bid) / mid_price * 100) if mid_price else 0.0

        raw_basis_bps = float(e.get("basis_pct") or 0) * 100
        if abs(raw_basis_bps) <= self.USDC_USDT_NOISE_FLOOR_BPS:
            basis_vs_binance_bps = 0.0
        else:
            sign = 1 if raw_basis_bps > 0 else -1
            basis_vs_binance_bps = sign * (
                abs(raw_basis_bps) - self.USDC_USDT_NOISE_FLOOR_BPS
            )

        # adl_risk gates Hyperliquid signals. HL funding is hourly, and 0.1%/hr
        # is routine for trending markets — the old 0.0010 threshold fired
        # constantly and the Risk Manager hard-blocked valid signals. 0.005/hr
        # (≈ 12% annualised) corresponds to genuinely anomalous funding pressure.
        # Audit §02 finding HL_adl_001.
        adl_risk = abs(funding_rate) > 0.005
        max_leverage = int(t.get("maxLeverage") or t.get("leverage") or 20)

        return ExchangeContext(
            exchange=self.EXCHANGE_KEY,
            symbol=symbol_canonical,
            base_asset=coin,
            quote_asset="USDC",
            quote_is_usdc=True,
            last_price=last_price,
            mark_price=mark_price,
            index_price=oracle_price,
            mid_price=mid_price,
            change_24h_pct=change_24h,
            change_4h_pct=float(e.get("change_4h_pct") or 0),
            change_1h_pct=float(e.get("change_1h_pct") or 0),
            volume_24h=volume_24h,
            open_interest=open_interest,
            funding_rate=funding_rate,
            next_funding_minutes=60,
            funding_interval_hours=self.NORMAL_FUNDING_INTERVAL_H,
            best_bid=best_bid,
            best_ask=best_ask,
            bid_depth_usd=bid_depth_usd,
            ask_depth_usd=ask_depth_usd,
            spread_pct=spread_pct,
            imbalance=imbalance,
            klines_1h=klines,
            max_leverage=max_leverage,
            adl_risk=adl_risk,
            exchange_stress_notice=exchange_stress,
            basis_vs_binance_bps=basis_vs_binance_bps,
            venue_leader=e.get("venue_leader", "unknown"),
            is_retail_fragmented=False,
            is_institutional=True,
            supports_mark_price=True,
            typical_spread_bps=2.0,
            data_quality=e.get("data_quality", "current"),
            data_stale=False,
            kline_depth_1h=int(e.get("kline_depth_1h") or 0),
            kline_depth_4h=int(e.get("kline_depth_4h") or 0),
            rsi_1h=float(e.get("rsi_1h") or 50),
            trend_score=int(e.get("trend_score") or 0),
            atr_pct=float(e.get("atr_pct") or 0),
            volatility=e.get("volatility", "medium"),
            direction=e.get("direction", "LONG"),
        )
