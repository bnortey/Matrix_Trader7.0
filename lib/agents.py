"""
Matrix Trader 8-Analyst Hybrid Agent Intelligence Layer.

Exchange-agnostic: all agents operate on ExchangeContext, never raw dicts.
Phase 1 is shadow mode. Agent deltas are stored for validation and are not
applied to signal conviction, except deterministic Risk Manager hard blocks.
"""

import json
import time
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeout,
    as_completed,
)
from dataclasses import dataclass, field
from typing import Optional

from lib.ai_client import call_ai
from lib.exchange_context import ExchangeContext


@dataclass
class NarrativeMarketState:
    float_fragility_score: float = 50.0
    unlock_risk: bool = False
    supply_concentration: str = "unknown"
    tokenomics_reasoning: str = ""
    social_momentum: float = 0.0
    social_volume_spike: bool = False
    sentiment_credibility: str = "low"
    sentiment_reasoning: str = ""
    catalyst_tags: list = field(default_factory=list)
    exchange_stress_detected: bool = False
    macro_risk: str = "neutral"
    news_reasoning: str = ""
    technical_trend: str = "neutral"
    late_move: bool = False
    vwap_position: str = "neutral"
    rsi_context: str = "neutral"
    technical_reasoning: str = ""
    narrative_bull_strength: float = 50.0
    narrative_bear_strength: float = 50.0
    narrative_key_disagreement: str = ""
    narrative_reasoning: str = ""


@dataclass
class StructuralMarketState:
    imbalance: float = 0.0
    microprice_dev_bps: float = 0.0
    aggressive_buy_pct: float = 0.5
    spread_z: float = 0.0
    microstructure_pressure: str = "neutral"
    microstructure_reasoning: str = ""
    funding_rate: float = 0.0
    funding_z: float = 0.0
    oi_delta_trend: str = "flat"
    liq_proximity_pct: float = 100.0
    adl_risk: bool = False
    funding_signal: str = "neutral"
    funding_reasoning: str = ""
    basis_binance_bps: float = 0.0
    venue_leader: str = "unknown"
    premium_z: float = 0.0
    basis_signal: str = "neutral"
    cross_venue_reasoning: str = ""
    regime: str = "choppy"
    atr_pct: float = 0.0
    btc_corr_1h: float = 0.0
    leverage_cap_recommendation: int = 20
    no_trade_zone: bool = False
    regime_reasoning: str = ""
    structural_bull_strength: float = 50.0
    structural_bear_strength: float = 50.0
    structural_key_disagreement: str = ""
    structural_reasoning: str = ""


@dataclass
class AgentOutput:
    conviction_delta: int = 0
    tags: list = field(default_factory=list)
    hard_blocked: bool = False
    block_reasons: list = field(default_factory=list)
    composite_reasoning: str = ""
    narrative_bull_strength: float = 50.0
    structural_bull_strength: float = 50.0
    detected_regime: str = "unknown"
    exchange: str = ""
    agent_version: str = "v2-phase2-live"
    shadow_delta: int = 0
    shadow_narrative_delta: int = 0
    shadow_structural_delta: int = 0
    shadow_disagreement_score: float = 0.0
    # True when fewer than the minimum number of analysts returned parseable
    # LLM JSON. When set, callers must NOT apply conviction_delta — it's the
    # all-neutral fallback, not a real assessment. Audit §02 fix.
    llm_unavailable: bool = False
    llm_ok_count: int = 0
    llm_analyst_total: int = 0


REGIME_WEIGHTS = {
    "trending": {"narrative": 0.50, "structural": 0.50},
    "choppy": {"narrative": 0.30, "structural": 0.70},
    "volatile_squeeze": {"narrative": 0.20, "structural": 0.80},
    "news_catalyst": {"narrative": 0.70, "structural": 0.30},
    "low_liquidity": {"narrative": 0.35, "structural": 0.65},
    "institutional": {"narrative": 0.45, "structural": 0.55},
}

AGENT_ROSTER = {
    "trader": {
        "name": "Thomas Reeves",
        "title": "Chief Investment Officer",
        "division": "leadership",
        "specialty": "Synthesizes all research into the final conviction delta",
        "voice": 'Quiet, decisive, sees patterns. "The conviction is there. We move."',
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "risk_manager": {
        "name": "Harper Cross",
        "title": "Chief Risk Officer",
        "division": "leadership",
        "specialty": "Hard blocks, position gates, independent veto on any signal",
        "voice": 'Ruthless gatekeeper. "This trade doesn\'t pass. Full stop."',
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "narrative_debate": {
        "name": "Daria Wren",
        "title": "Head of Narrative Research",
        "division": "narrative",
        "specialty": "Narrative debate chair — synthesizes macro, fundamentals, and sentiment",
        "voice": 'Cold, precise, intimidating. "Three data points confirm it."',
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "tokenomics": {
        "name": "Priya Nair",
        "title": "Tokenomics Lead",
        "division": "narrative",
        "specialty": "On-chain supply, unlock risk, float fragility, tokenomics pressure",
        "voice": "Methodical. Flags unlock risk before anyone else.",
        "exchanges": ["MEXC", "HYPERLIQUID"],
    },
    "sentiment": {
        "name": "Hari Stern",
        "title": "Sentiment & Social Intelligence",
        "division": "narrative",
        "specialty": "Social momentum, sentiment credibility, crowd psychology",
        "voice": 'Eager, anxious, loyal. "Sentiment is... complicated. Leaning bullish."',
        "exchanges": ["MEXC", "HYPERLIQUID"],
    },
    "news": {
        "name": "Yasmin Cole",
        "title": "News & Catalyst Lead",
        "division": "narrative",
        "specialty": "Event-driven signals, macro catalysts, news-driven regime shifts",
        "voice": 'Socially intelligent. "The catalyst is real. Market knows."',
        "exchanges": ["MEXC", "HYPERLIQUID"],
    },
    "technical": {
        "name": "Rishi Sackey",
        "title": "Technical Strategy Lead",
        "division": "narrative",
        "specialty": "Price structure, EMA alignment, RSI context, late-move detection",
        "voice": 'Grinding detail. "RSI 34.2, EMA crossover confirmed, trend score -12."',
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "structural_debate": {
        "name": "Eric Tao",
        "title": "Head of Market Structure",
        "division": "structural",
        "specialty": "Structural debate chair — synthesizes order flow, funding, and regime",
        "voice": 'Commanding, strategic. "Structure is holding. The desk agrees."',
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "microstructure": {
        "name": "Niobe Reyes",
        "title": "Microstructure & Order Flow",
        "division": "structural",
        "specialty": "Book imbalance, microprice deviation, spread pressure, aggressive flow",
        "voice": 'Skeptical, precise. "Order book says one thing. I don\'t trust it."',
        "exchanges": ["MEXC", "HYPERLIQUID"],
    },
    "funding": {
        "name": "Kenny Hassan",
        "title": "Funding & Positioning Strategist",
        "division": "structural",
        "specialty": "Funding rates, OI delta trends, liquidation proximity, crowded positioning",
        "voice": 'Old hand. "Funding negative three sessions running. Shorts are getting squeezed."',
        "exchanges": ["MEXC", "HYPERLIQUID"],
    },
    "cross_venue": {
        "name": "Ghost Kimura",
        "title": "Cross-Venue Intelligence Lead",
        "division": "structural",
        "specialty": "MEXC vs Hyperliquid vs Bybit basis, venue-leader detection, arb pressure",
        "voice": 'Cool, detached. "MEXC and HL diverging 0.3%. That\'s meaningful."',
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
    "regime": {
        "name": "Nadia Okonkwo",
        "title": "Volatility & Regime Specialist",
        "division": "structural",
        "specialty": "Regime classification, ATR context, BTC correlation, no-trade-zone detection",
        "voice": 'Calm authority. "We\'re in volatile squeeze. Treat accordingly."',
        "exchanges": ["MEXC", "HYPERLIQUID", "BYBIT"],
    },
}

FIRM_META = {
    "name": "Cipher Research Group",
    "tagline": "We read the market's structure. Not its noise.",
    "exchange_coverage": ["MEXC", "HYPERLIQUID", "BYBIT"],
}


def _classify_regime(ctx: ExchangeContext, ns: NarrativeMarketState) -> str:
    if ctx.is_institutional and not ctx.exchange_stress_notice:
        return "institutional"
    if ctx.exchange_stress_notice or ns.exchange_stress_detected:
        if abs(ctx.change_4h_pct) > 8:
            return "volatile_squeeze"
        return "news_catalyst"
    if ctx.volatility in ("high", "extreme") and abs(ctx.funding_rate) > 0.001:
        return "volatile_squeeze"
    if abs(ctx.change_4h_pct) > 5 and ctx.trend_score > 40:
        return "trending"
    if ctx.volume_24h < 2_000_000:
        return "low_liquidity"
    return "choppy"


def _analyst_call(system_msg: str, data_subset: dict, output_schema: dict) -> dict:
    """
    Run one analyst LLM call. Always returns a dict with a sentinel '_llm_ok'
    key so run_agent_pipeline() can distinguish "all analysts said neutral"
    (LLM was up; conviction_delta=0 is a real assessment) from "LLM was down"
    (conviction_delta=0 is information-free and must not be trusted).

    Audit §02 finding agents_silent_fallback_001 documented the prior behavior
    where this function returned {} on both 'no response' and 'parse error',
    making all 8 analysts default to neutral and the trader emit
    conviction_delta=0 as if it were a real signal.
    """
    prompt = (
        "Analyze this market data and return ONLY a JSON object. "
        "No text outside JSON. No markdown. No explanation.\n\n"
        f"Market data:\n{json.dumps(data_subset, default=str)}\n\n"
        "Return exactly this JSON structure (fill all fields):\n"
        f"{json.dumps(output_schema, indent=2)}\n\n"
        "Rules:\n"
        "- Numeric scores: 0.0 to 100.0 unless otherwise specified\n"
        "- reasoning fields: max 120 chars, plain English\n"
        "- If data is missing, use the default value shown"
    )
    raw = call_ai(system=system_msg, user=prompt, max_tokens=400)
    if not raw:
        return {"_llm_ok": False}
    try:
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.removeprefix("```json").removeprefix("```")
            clean = clean.removesuffix("```").strip()
        parsed = json.loads(clean)
        if isinstance(parsed, dict):
            parsed["_llm_ok"] = True
            return parsed
        return {"_llm_ok": False}
    except Exception:
        return {"_llm_ok": False}


def _run_with_deadline(fn, timeout: float) -> dict:
    ex = ThreadPoolExecutor(max_workers=1)
    future = ex.submit(fn)
    try:
        return future.result(timeout=max(0.1, timeout)) or {}
    except Exception:
        future.cancel()
        return {}
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def _run_tokenomics_analyst(ctx: ExchangeContext) -> dict:
    try:
        oi_vol_ratio = (
            ctx.open_interest / (ctx.volume_24h / ctx.last_price)
            if ctx.volume_24h and ctx.last_price else 0
        )
        data = {
            "symbol": ctx.base_asset,
            "exchange": ctx.exchange,
            "is_retail_fragmented": ctx.is_retail_fragmented,
            "funding_rate": ctx.funding_rate,
            "funding_interval_hours": ctx.funding_interval_hours,
            "oi_to_daily_volume_ratio": round(oi_vol_ratio, 2),
            "open_interest_usd": round(ctx.open_interest * ctx.last_price, 0),
            "volume_24h_usd": round(ctx.volume_24h, 0),
            "change_24h_pct": ctx.change_24h_pct,
        }
        schema = {
            "float_fragility_score": 50.0,
            "unlock_risk": False,
            "supply_concentration": "unknown",
            "tokenomics_reasoning": "insufficient data",
        }
        persona = AGENT_ROSTER["tokenomics"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Assess float fragility from available market structure data. "
            "Output only JSON."
        )
        return _analyst_call(system, data, schema)
    except Exception:
        return {}


def _run_sentiment_analyst(ctx: ExchangeContext) -> dict:
    try:
        data = {
            "exchange": ctx.exchange,
            "change_24h_pct": ctx.change_24h_pct,
            "change_4h_pct": ctx.change_4h_pct,
            "change_1h_pct": ctx.change_1h_pct,
            "volume_24h_usd": round(ctx.volume_24h, 0),
            "funding_rate": ctx.funding_rate,
            "imbalance": ctx.imbalance,
            "rsi_1h": ctx.rsi_1h,
        }
        schema = {
            "social_momentum": 0.0,
            "social_volume_spike": False,
            "sentiment_credibility": "low",
            "sentiment_reasoning": "inferred from price/volume only",
        }
        persona = AGENT_ROSTER["sentiment"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Infer market sentiment from price and volume data. "
            "Output only JSON."
        )
        return _analyst_call(system, data, schema)
    except Exception:
        return {}


def _run_news_analyst(ctx: ExchangeContext) -> dict:
    try:
        data = {
            "exchange": ctx.exchange,
            "is_institutional": ctx.is_institutional,
            "funding_interval_hours": ctx.funding_interval_hours,
            "exchange_stress_notice": ctx.exchange_stress_notice,
            "funding_rate": ctx.funding_rate,
            "adl_risk": ctx.adl_risk,
            "change_24h_pct": ctx.change_24h_pct,
            "basis_vs_reference_bps": ctx.basis_vs_binance_bps,
        }
        schema = {
            "catalyst_tags": [],
            "exchange_stress_detected": False,
            "macro_risk": "neutral",
            "news_reasoning": "no catalyst data available",
        }
        persona = AGENT_ROSTER["news"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Assess news and catalyst risk for a crypto perp trading system. "
            "Hyperliquid hourly funding is normal, not exchange stress. "
            "Output only JSON."
        )
        return _analyst_call(system, data, schema)
    except Exception:
        return {}


def _run_technical_analyst(ctx: ExchangeContext) -> dict:
    try:
        data = {
            "rsi_1h": ctx.rsi_1h,
            "trend_score": ctx.trend_score,
            "change_1h_pct": ctx.change_1h_pct,
            "change_4h_pct": ctx.change_4h_pct,
            "change_24h_pct": ctx.change_24h_pct,
            "atr_pct": ctx.atr_pct,
            "volatility": ctx.volatility,
            "direction_being_evaluated": ctx.direction,
        }
        schema = {
            "technical_trend": "neutral",
            "late_move": False,
            "vwap_position": "neutral",
            "rsi_context": "neutral",
            "technical_reasoning": "neutral setup",
        }
        persona = AGENT_ROSTER["technical"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Assess technical structure for a crypto perp trading system. "
            "Output only JSON."
        )
        return _analyst_call(system, data, schema)
    except Exception:
        return {}


def _run_microstructure_analyst(ctx: ExchangeContext) -> dict:
    try:
        mid = ctx.mid_price or ctx.last_price
        microprice = mid + (ctx.imbalance * ctx.spread_pct * mid / 100 / 2) if mid else 0
        microprice_dev_bps = ((microprice - mid) / mid * 10000) if mid else 0
        spread_z = (
            (ctx.spread_pct * 100 - ctx.typical_spread_bps)
            / max(ctx.typical_spread_bps, 1)
        )
        data = {
            "exchange": ctx.exchange,
            "is_retail_fragmented": ctx.is_retail_fragmented,
            "imbalance": round(ctx.imbalance, 4),
            "microprice_dev_bps": round(microprice_dev_bps, 2),
            "spread_pct": round(ctx.spread_pct, 4),
            "spread_z_score": round(spread_z, 2),
            "bid_depth_usd": ctx.bid_depth_usd,
            "ask_depth_usd": ctx.ask_depth_usd,
            "direction": ctx.direction,
        }
        schema = {
            "imbalance": 0.0,
            "microprice_dev_bps": 0.0,
            "aggressive_buy_pct": 0.5,
            "spread_z": 0.0,
            "microstructure_pressure": "neutral",
            "microstructure_reasoning": "neutral book",
        }
        persona = AGENT_ROSTER["microstructure"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Assess order book pressure for a crypto perp trading system. "
            "Output only JSON."
        )
        result = _analyst_call(system, data, schema)
        result["imbalance"] = round(ctx.imbalance, 4)
        result["microprice_dev_bps"] = round(microprice_dev_bps, 2)
        result["spread_z"] = round(spread_z, 2)
        return result
    except Exception:
        return {}


def _run_funding_analyst(ctx: ExchangeContext) -> dict:
    try:
        funding_typical = 0.0001
        funding_z = ctx.funding_rate / funding_typical if funding_typical else 0
        data = {
            "exchange": ctx.exchange,
            "funding_interval_hours": ctx.funding_interval_hours,
            "funding_rate": ctx.funding_rate,
            "funding_z_score": round(funding_z, 2),
            "open_interest": ctx.open_interest,
            "adl_risk": ctx.adl_risk,
            "exchange_stress_notice": ctx.exchange_stress_notice,
            "change_1h_pct": ctx.change_1h_pct,
            "direction": ctx.direction,
            "is_institutional": ctx.is_institutional,
        }
        schema = {
            "funding_rate": 0.0,
            "funding_z": 0.0,
            "oi_delta_trend": "flat",
            "liq_proximity_pct": 100.0,
            "adl_risk": False,
            "funding_signal": "neutral",
            "funding_reasoning": "neutral funding",
        }
        persona = AGENT_ROSTER["funding"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Assess funding and positioning signals. Hyperliquid hourly funding "
            "is normal, not exchange stress. Output only JSON."
        )
        result = _analyst_call(system, data, schema)
        result["funding_rate"] = ctx.funding_rate
        result["funding_z"] = round(funding_z, 2)
        result["adl_risk"] = ctx.adl_risk
        return result
    except Exception:
        return {}


def _run_cross_venue_analyst(ctx: ExchangeContext) -> dict:
    try:
        premium_z = ctx.basis_vs_binance_bps / 20.0
        data = {
            "exchange": ctx.exchange,
            "is_institutional": ctx.is_institutional,
            "quote_currency": ctx.quote_asset,
            "quote_is_usdc": ctx.quote_is_usdc,
            "basis_vs_reference_bps": round(ctx.basis_vs_binance_bps, 2),
            "premium_z_score": round(premium_z, 2),
            "venue_leader": ctx.venue_leader,
            "change_24h_pct": ctx.change_24h_pct,
            "direction": ctx.direction,
        }
        schema = {
            "basis_binance_bps": 0.0,
            "venue_leader": "unknown",
            "premium_z": 0.0,
            "basis_signal": "neutral",
            "cross_venue_reasoning": "no basis data",
        }
        persona = AGENT_ROSTER["cross_venue"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Assess cross-venue price and basis divergence. Hyperliquid is "
            "USDC-settled and its basis field has already had USDC/USDT noise filtered. "
            "Output only JSON."
        )
        result = _analyst_call(system, data, schema)
        result["basis_binance_bps"] = round(ctx.basis_vs_binance_bps, 2)
        result["premium_z"] = round(premium_z, 2)
        return result
    except Exception:
        return {}


def _run_regime_analyst(ctx: ExchangeContext, ns_partial: dict) -> dict:
    try:
        data = {
            "exchange": ctx.exchange,
            "is_institutional": ctx.is_institutional,
            "is_retail_fragmented": ctx.is_retail_fragmented,
            "volatility": ctx.volatility,
            "atr_pct": ctx.atr_pct,
            "funding_rate": ctx.funding_rate,
            "funding_interval_hours": ctx.funding_interval_hours,
            "exchange_stress_notice": ctx.exchange_stress_notice,
            "change_4h_pct": ctx.change_4h_pct,
            "trend_score": ctx.trend_score,
            "volume_24h_usd": round(ctx.volume_24h, 0),
            "spread_pct": ctx.spread_pct,
            "max_leverage": ctx.max_leverage,
        }
        schema = {
            "regime": "choppy",
            "atr_pct": 0.0,
            "btc_corr_1h": 0.0,
            "leverage_cap_recommendation": 20,
            "no_trade_zone": False,
            "regime_reasoning": "choppy low-signal environment",
        }
        persona = AGENT_ROSTER["regime"]
        system = (
            f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
            f"Personality: {persona['voice']} "
            "Classify volatility regime as: trending, choppy, volatile_squeeze, "
            "news_catalyst, low_liquidity, institutional. Output only JSON."
        )
        result = _analyst_call(system, data, schema)
        result["atr_pct"] = ctx.atr_pct
        return result
    except Exception:
        return {}


def _run_narrative_debate(ns: NarrativeMarketState, ctx: ExchangeContext) -> dict:
    data = {
        "exchange": ctx.exchange,
        "float_fragility_score": ns.float_fragility_score,
        "unlock_risk": ns.unlock_risk,
        "social_momentum": ns.social_momentum,
        "exchange_stress_detected": ns.exchange_stress_detected,
        "catalyst_tags": ns.catalyst_tags,
        "technical_trend": ns.technical_trend,
        "late_move": ns.late_move,
        "rsi_context": ns.rsi_context,
        "direction": ctx.direction,
    }
    schema = {
        "narrative_bull_strength": 50.0,
        "narrative_bear_strength": 50.0,
        "narrative_key_disagreement": "",
        "narrative_reasoning": "",
    }
    persona = AGENT_ROSTER["narrative_debate"]
    system = (
        f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
        f"Personality: {persona['voice']} "
        "Chair a bull/bear debate on narrative signals. Output only JSON."
    )
    return _analyst_call(system, data, schema)


def _run_structural_debate(ss: StructuralMarketState, ctx: ExchangeContext) -> dict:
    data = {
        "exchange": ctx.exchange,
        "is_institutional": ctx.is_institutional,
        "imbalance": ss.imbalance,
        "microprice_dev_bps": ss.microprice_dev_bps,
        "microstructure_pressure": ss.microstructure_pressure,
        "funding_signal": ss.funding_signal,
        "funding_z": ss.funding_z,
        "adl_risk": ss.adl_risk,
        "basis_signal": ss.basis_signal,
        "premium_z": ss.premium_z,
        "no_trade_zone": ss.no_trade_zone,
        "regime": ss.regime,
        "direction": ctx.direction,
    }
    schema = {
        "structural_bull_strength": 50.0,
        "structural_bear_strength": 50.0,
        "structural_key_disagreement": "",
        "structural_reasoning": "",
    }
    persona = AGENT_ROSTER["structural_debate"]
    system = (
        f"You are {persona['name']}, {persona['title']} at Cipher Research Group. "
        f"Personality: {persona['voice']} "
        "Chair a bull/bear debate on structural signals. Output only JSON."
    )
    return _analyst_call(system, data, schema)


def _run_trader(ns: NarrativeMarketState, ss: StructuralMarketState, ctx: ExchangeContext) -> dict:
    weights = REGIME_WEIGHTS.get(ss.regime, {"narrative": 0.40, "structural": 0.60})
    narrative_net = (ns.narrative_bull_strength - ns.narrative_bear_strength) / 100
    structural_net = (ss.structural_bull_strength - ss.structural_bear_strength) / 100
    composite_net = (
        narrative_net * weights["narrative"]
        + structural_net * weights["structural"]
    )

    raw_delta = composite_net * 20 if ctx.direction == "LONG" else -composite_net * 20
    conviction_delta = int(max(-20, min(20, raw_delta)))

    if ctx.direction == "LONG":
        narrative_raw = narrative_net * 5
        structural_raw = structural_net * 15
    else:
        narrative_raw = -narrative_net * 5
        structural_raw = -structural_net * 15

    tags = []
    if ss.adl_risk:
        tags.append("funding_squeeze_risk")
    if ctx.exchange_stress_notice:
        tags.append("exchange_stress")
    if ns.float_fragility_score >= 65:
        tags.append("float_fragile")
    if ss.imbalance > 0.4 and ctx.direction == "LONG":
        tags.append("imbalance_long")
    elif ss.imbalance < -0.4 and ctx.direction == "SHORT":
        tags.append("imbalance_short")
    if abs(ss.microprice_dev_bps) > 5:
        aligned = (
            (ss.microprice_dev_bps > 0 and ctx.direction == "LONG")
            or (ss.microprice_dev_bps < 0 and ctx.direction == "SHORT")
        )
        if aligned:
            tags.append("microprice_aligned")
    if ss.basis_signal == "dislocation":
        tags.append("basis_dislocation")
    if ns.late_move:
        tags.append("late_move_detected")
    if abs(ns.narrative_bull_strength - ss.structural_bull_strength) > 30:
        tags.append("agent_disagreement")
    elif abs(narrative_net - structural_net) < 0.15:
        tags.append("agent_confirmed")

    reasoning_parts = []
    if ns.narrative_reasoning:
        reasoning_parts.append(f"Narrative: {ns.narrative_reasoning}")
    if ss.structural_reasoning:
        reasoning_parts.append(f"Structure: {ss.structural_reasoning}")

    return {
        "conviction_delta": conviction_delta,
        "tags": tags,
        "composite_reasoning": " | ".join(reasoning_parts)[:300],
        "shadow_narrative_delta": int(max(-5, min(5, narrative_raw))),
        "shadow_structural_delta": int(max(-15, min(15, structural_raw))),
        "shadow_disagreement_score": round(abs(narrative_net - structural_net), 3),
    }


def _run_risk_manager(ss: StructuralMarketState, ctx: ExchangeContext) -> dict:
    blocks = []
    if ctx.data_stale:
        blocks.append("data_stale")
    if ss.adl_risk:
        blocks.append("adl_risk")
    if ss.spread_z > 3.0:
        blocks.append("spread_z_extreme")
    if ss.no_trade_zone:
        blocks.append("no_trade_zone")
    if ss.premium_z >= 4.0 or ss.premium_z <= -4.0:
        blocks.append("premium_z_extreme")
    return {"hard_blocked": bool(blocks), "block_reasons": blocks}


def run_agent_pipeline(
    exchange: str,
    ticker_data: dict,
    klines=None,
    depth_data: Optional[dict] = None,
    enriched_fields: Optional[dict] = None,
    timeout: float = 8.0,
) -> Optional[AgentOutput]:
    """
    Main entry point for the 8-analyst agent pipeline.

    Returns AgentOutput or None on timeout/error. All failures are fail-open for
    scanning: callers keep the original signal unless a deterministic Risk
    Manager block is returned.
    """
    t_start = time.time()
    try:
        from lib.adapters import normalize

        ctx = normalize(
            exchange=exchange,
            ticker_data=ticker_data,
            klines=klines,
            depth_data=depth_data or {},
            enriched_fields=enriched_fields or {},
        )
        if ctx is None:
            print(f"[agents] No adapter for exchange={exchange}", flush=True)
            return None
    except Exception as e:
        print(f"[agents] normalize error: {e}", flush=True)
        return None

    analyst_results = {}
    analyst_fns = {
        "tokenomics": lambda: _run_tokenomics_analyst(ctx),
        "sentiment": lambda: _run_sentiment_analyst(ctx),
        "news": lambda: _run_news_analyst(ctx),
        "technical": lambda: _run_technical_analyst(ctx),
        "microstructure": lambda: _run_microstructure_analyst(ctx),
        "funding": lambda: _run_funding_analyst(ctx),
        "cross_venue": lambda: _run_cross_venue_analyst(ctx),
        "regime": lambda: _run_regime_analyst(ctx, {}),
    }

    remaining_timeout = timeout - (time.time() - t_start) - 1.5
    ex = ThreadPoolExecutor(max_workers=8)
    futures = {ex.submit(fn): name for name, fn in analyst_fns.items()}
    try:
        for future in as_completed(futures, timeout=max(0.5, remaining_timeout)):
            name = futures[future]
            try:
                analyst_results[name] = future.result(timeout=0.1) or {}
            except Exception:
                analyst_results[name] = {}
    except FuturesTimeout:
        pass
    finally:
        for future in futures:
            future.cancel()
        ex.shutdown(wait=False, cancel_futures=True)

    r = analyst_results
    ns = NarrativeMarketState()
    ns.float_fragility_score = float(r.get("tokenomics", {}).get("float_fragility_score") or 50)
    ns.unlock_risk = bool(r.get("tokenomics", {}).get("unlock_risk") or False)
    ns.supply_concentration = r.get("tokenomics", {}).get("supply_concentration") or "unknown"
    ns.tokenomics_reasoning = r.get("tokenomics", {}).get("tokenomics_reasoning") or ""
    ns.social_momentum = float(r.get("sentiment", {}).get("social_momentum") or 0)
    ns.social_volume_spike = bool(r.get("sentiment", {}).get("social_volume_spike") or False)
    ns.sentiment_credibility = r.get("sentiment", {}).get("sentiment_credibility") or "low"
    ns.sentiment_reasoning = r.get("sentiment", {}).get("sentiment_reasoning") or ""
    ns.catalyst_tags = r.get("news", {}).get("catalyst_tags") or []
    ns.exchange_stress_detected = bool(
        r.get("news", {}).get("exchange_stress_detected") or ctx.exchange_stress_notice
    )
    ns.macro_risk = r.get("news", {}).get("macro_risk") or "neutral"
    ns.news_reasoning = r.get("news", {}).get("news_reasoning") or ""
    ns.technical_trend = r.get("technical", {}).get("technical_trend") or "neutral"
    ns.late_move = bool(r.get("technical", {}).get("late_move") or False)
    ns.vwap_position = r.get("technical", {}).get("vwap_position") or "neutral"
    ns.rsi_context = r.get("technical", {}).get("rsi_context") or "neutral"
    ns.technical_reasoning = r.get("technical", {}).get("technical_reasoning") or ""

    ss = StructuralMarketState()
    ss.imbalance = float(r.get("microstructure", {}).get("imbalance") or ctx.imbalance)
    ss.microprice_dev_bps = float(r.get("microstructure", {}).get("microprice_dev_bps") or 0)
    ss.spread_z = float(r.get("microstructure", {}).get("spread_z") or 0)
    ss.microstructure_pressure = r.get("microstructure", {}).get("microstructure_pressure") or "neutral"
    ss.microstructure_reasoning = r.get("microstructure", {}).get("microstructure_reasoning") or ""
    ss.funding_rate = float(r.get("funding", {}).get("funding_rate") or ctx.funding_rate)
    ss.funding_z = float(r.get("funding", {}).get("funding_z") or 0)
    ss.oi_delta_trend = r.get("funding", {}).get("oi_delta_trend") or "flat"
    ss.liq_proximity_pct = float(r.get("funding", {}).get("liq_proximity_pct") or 100)
    ss.adl_risk = bool(r.get("funding", {}).get("adl_risk") or ctx.adl_risk)
    ss.funding_signal = r.get("funding", {}).get("funding_signal") or "neutral"
    ss.funding_reasoning = r.get("funding", {}).get("funding_reasoning") or ""
    ss.basis_binance_bps = float(
        r.get("cross_venue", {}).get("basis_binance_bps") or ctx.basis_vs_binance_bps
    )
    ss.venue_leader = r.get("cross_venue", {}).get("venue_leader") or ctx.venue_leader
    ss.premium_z = float(r.get("cross_venue", {}).get("premium_z") or 0)
    ss.basis_signal = r.get("cross_venue", {}).get("basis_signal") or "neutral"
    ss.cross_venue_reasoning = r.get("cross_venue", {}).get("cross_venue_reasoning") or ""
    ss.regime = r.get("regime", {}).get("regime") or _classify_regime(ctx, ns)
    ss.atr_pct = float(r.get("regime", {}).get("atr_pct") or ctx.atr_pct)
    ss.leverage_cap_recommendation = int(
        r.get("regime", {}).get("leverage_cap_recommendation") or ctx.max_leverage
    )
    ss.no_trade_zone = bool(r.get("regime", {}).get("no_trade_zone") or False)
    ss.regime_reasoning = r.get("regime", {}).get("regime_reasoning") or ""

    remaining = timeout - (time.time() - t_start)
    if remaining > 1.5:
        nd = _run_with_deadline(lambda: _run_narrative_debate(ns, ctx), min(remaining - 0.5, 2.0))
        ns.narrative_bull_strength = float(nd.get("narrative_bull_strength") or 50)
        ns.narrative_bear_strength = float(nd.get("narrative_bear_strength") or 50)
        ns.narrative_key_disagreement = nd.get("narrative_key_disagreement") or ""
        ns.narrative_reasoning = nd.get("narrative_reasoning") or ""

    remaining = timeout - (time.time() - t_start)
    if remaining > 0.8:
        sd = _run_with_deadline(lambda: _run_structural_debate(ss, ctx), min(remaining - 0.2, 2.0))
        ss.structural_bull_strength = float(sd.get("structural_bull_strength") or 50)
        ss.structural_bear_strength = float(sd.get("structural_bear_strength") or 50)
        ss.structural_key_disagreement = sd.get("structural_key_disagreement") or ""
        ss.structural_reasoning = sd.get("structural_reasoning") or ""

    trader = _run_trader(ns, ss, ctx)
    risk = _run_risk_manager(ss, ctx)

    # Count how many of the 8 analysts actually received parseable LLM
    # responses. The trader+debate stages aggregate analyst outputs by
    # pulling specific fields with .get(..., default); when every analyst
    # returns {"_llm_ok": False}, every downstream value collapses to its
    # neutral default and the trader emits conviction_delta = 0 by
    # construction. That zero is mathematically indistinguishable from
    # "real neutral assessment" without this counter. Audit §02 fix.
    _llm_ok_count = sum(
        1 for r in analyst_results.values()
        if isinstance(r, dict) and r.get("_llm_ok")
    )
    _llm_unavailable = _llm_ok_count < 2  # require ≥2/8 analysts to trust delta

    # Risk Manager hard blocks remain authoritative regardless of LLM status —
    # they are deterministic math/risk gates (premium_z, ADL, oracle deviation,
    # vol regime), not LLM judgement. Keep block_reasons intact.
    tags = list(trader["tags"]) + (risk["block_reasons"] if risk["hard_blocked"] else [])
    if _llm_unavailable:
        tags.append("agent_unavailable")

    return AgentOutput(
        conviction_delta=trader["conviction_delta"],
        tags=tags,
        hard_blocked=risk["hard_blocked"],
        block_reasons=risk["block_reasons"],
        composite_reasoning=trader["composite_reasoning"],
        narrative_bull_strength=ns.narrative_bull_strength,
        structural_bull_strength=ss.structural_bull_strength,
        detected_regime=ss.regime,
        exchange=ctx.exchange,
        agent_version="v2-phase1-shadow",
        shadow_delta=trader["conviction_delta"],
        shadow_narrative_delta=trader["shadow_narrative_delta"],
        shadow_structural_delta=trader["shadow_structural_delta"],
        shadow_disagreement_score=trader["shadow_disagreement_score"],
        llm_unavailable=_llm_unavailable,
        llm_ok_count=_llm_ok_count,
        llm_analyst_total=len(analyst_results),
    )
