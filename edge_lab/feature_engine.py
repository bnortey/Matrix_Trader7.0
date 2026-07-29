from __future__ import annotations

import pandas as pd

from edge_lab.normalization import rolling_decile
# lib.indicators is permitted here ONLY because it is a pure function
# module with no app.py imports, no Flask routes, and no side effects.
# If you add any app.py dependency to lib/indicators.py, you MUST
# remove or replace this import to preserve Edge Lab isolation.
from lib.indicators import atr, ema, rsi, volatility_regime

FEATURE_VERSION = "edge_features_v2"


def compute_features(
    candles: pd.DataFrame,
    rolling_window: int = 500,
    min_periods: int = 100,
) -> pd.DataFrame:
    df = candles.copy().sort_values("timestamp").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["rsi_15m"] = rsi(df, period=14)
    df["rsi_15m_decile"] = rolling_decile(df["rsi_15m"], rolling_window, min_periods)

    atr_values = atr(df, period=14)
    df["atr_pct_15m"] = (atr_values / df["close"]) * 100.0
    df["atr_pct_15m_decile"] = rolling_decile(df["atr_pct_15m"], rolling_window, min_periods)

    df["volume_decile"] = rolling_decile(df["volume"], rolling_window, min_periods)
    df["stddev_pct"] = df["close"].pct_change().rolling(window=20, min_periods=20).std() * 100.0
    df["stddev_decile"] = rolling_decile(df["stddev_pct"], rolling_window, min_periods)

    df["volatility_regime"] = df["atr_pct_15m"].apply(
        lambda v: volatility_regime(float(v)) if pd.notna(v) else None
    )
    df["ema20"] = ema(df, period=20)
    df["ema50"] = ema(df, period=50)

    def trend_state(row) -> str:
        if row["close"] > row["ema20"] > row["ema50"]:
            return "bullish"
        if row["close"] < row["ema20"] < row["ema50"]:
            return "bearish"
        return "neutral"

    def compression_state(row) -> str:
        atr_decile = row["atr_pct_15m_decile"]
        std_decile = row["stddev_decile"]
        if pd.isna(atr_decile) or pd.isna(std_decile):
            return None
        if atr_decile <= 3 and std_decile <= 3:
            return "compressed"
        if atr_decile >= 8 or std_decile >= 8:
            return "expanded"
        return "normal"

    df["trend_state"] = df.apply(trend_state, axis=1)
    df["compression_state"] = df.apply(compression_state, axis=1)
    df["funding_state"] = "unknown"
    df["feature_version"] = FEATURE_VERSION
    df["funding_available"] = False
    df["tags"] = df.apply(_tags_for_row, axis=1)
    return df


def _tags_for_row(row) -> list[str]:
    tags: list[str] = []
    if row.get("compression_state") == "compressed":
        tags.append("compressed")
    if row.get("compression_state") == "expanded":
        tags.append("expanded")
    if row.get("trend_state") == "bullish":
        tags.append("bullish_trend")
    if row.get("trend_state") == "bearish":
        tags.append("bearish_trend")
    if row.get("volatility_regime") == "extreme":
        tags.append("extreme_vol")
    if row.get("volatility_regime") == "low":
        tags.append("low_vol")
    return tags


def features_json_for_row(row) -> dict:
    keys = [
        "rsi_15m", "rsi_15m_decile", "atr_pct_15m", "atr_pct_15m_decile",
        "volume_decile", "stddev_pct", "stddev_decile", "volatility_regime",
        "ema20", "ema50", "trend_state", "compression_state", "funding_state",
        "funding_available", "feature_version", "tags",
    ]
    out = {}
    for key in keys:
        value = row.get(key)
        if _is_missing(value):
            value = None
        if hasattr(value, "item"):
            value = value.item()
        out[key] = value
    return out


def has_required_features(row) -> bool:
    required = ["rsi_15m_decile", "atr_pct_15m_decile", "volume_decile", "stddev_decile"]
    return all(pd.notna(row.get(key)) for key in required)


def _is_missing(value) -> bool:
    if isinstance(value, list):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False
