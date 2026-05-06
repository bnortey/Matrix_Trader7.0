"""
MEXC perpetual swap to ExchangeContext adapter.
"""

import time

from lib.exchange_context import ExchangeContext


class MexcAdapter:
    EXCHANGE_KEY = "MEXC"
    NORMAL_FUNDING_INTERVAL_H = 8
    STRESS_FUNDING_INTERVAL_H = 1

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

        symbol_raw = t.get("symbol", "")
        base = symbol_raw.replace("_USDT", "").replace("_USDC", "")

        last_price = float(t.get("lastPrice") or t.get("last") or t.get("price") or 0)
        mark_price = float(t.get("fairPrice") or t.get("indexPrice") or last_price)
        index_price = float(t.get("indexPrice") or last_price)

        best_bid = float(t.get("bid1") or d.get("best_bid") or 0)
        best_ask = float(t.get("ask1") or d.get("best_ask") or 0)
        mid = (best_bid + best_ask) / 2 if (best_bid and best_ask) else last_price

        bid_depth_usd = float(d.get("bid_depth_usd") or 0)
        ask_depth_usd = float(d.get("ask_depth_usd") or 0)
        imbalance = float(d.get("imbalance") or 0)
        spread_pct = ((best_ask - best_bid) / mid * 100) if mid > 0 else 0

        funding_rate = float(t.get("fundingRate") or 0)
        open_interest = float(t.get("holdVol") or 0)
        volume_units = float(t.get("volume24") or t.get("vol24h") or 0)
        volume_24h = volume_units * last_price
        change_24h = float(t.get("riseFallRate") or 0) * 100

        next_ft = t.get("nextFundingTime")
        funding_interval_h = self.NORMAL_FUNDING_INTERVAL_H
        next_funding_minutes = None
        if next_ft:
            try:
                diff_ms = int(next_ft) - int(time.time() * 1000)
                next_funding_minutes = max(0, int(diff_ms / 60000))
                if next_funding_minutes <= 70:
                    funding_interval_h = self.STRESS_FUNDING_INTERVAL_H
            except Exception:
                pass

        exchange_stress = (
            funding_interval_h == self.STRESS_FUNDING_INTERVAL_H
            or abs(funding_rate) > 0.002
        )
        oi_vol_ratio = (
            open_interest / (volume_24h / last_price)
            if volume_24h and last_price else 0
        )
        adl_risk = abs(funding_rate) > 0.003 or oi_vol_ratio >= 15

        return ExchangeContext(
            exchange=self.EXCHANGE_KEY,
            symbol=symbol_raw,
            base_asset=base,
            quote_asset="USDT",
            last_price=last_price,
            mark_price=mark_price,
            index_price=index_price,
            mid_price=mid,
            change_24h_pct=change_24h,
            change_4h_pct=float(e.get("change_4h_pct") or 0),
            change_1h_pct=float(e.get("change_1h_pct") or 0),
            volume_24h=volume_24h,
            open_interest=open_interest,
            funding_rate=funding_rate,
            next_funding_minutes=next_funding_minutes,
            funding_interval_hours=funding_interval_h,
            best_bid=best_bid,
            best_ask=best_ask,
            bid_depth_usd=bid_depth_usd,
            ask_depth_usd=ask_depth_usd,
            spread_pct=spread_pct,
            imbalance=imbalance,
            klines_1h=klines,
            max_leverage=int(t.get("maxLeverage") or e.get("leverage_cap") or 20),
            adl_risk=adl_risk,
            exchange_stress_notice=exchange_stress,
            basis_vs_binance_bps=float(e.get("basis_pct") or 0) * 100,
            venue_leader=e.get("venue_leader", "unknown"),
            is_retail_fragmented=True,
            is_institutional=False,
            supports_mark_price=True,
            typical_spread_bps=5.0,
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
