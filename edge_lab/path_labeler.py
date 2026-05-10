from __future__ import annotations

import pandas as pd
import numpy as np

PATH_TEMPLATES = {
    "TP0_5_SL0_5": {"tp": 0.5, "sl": 0.5},
    "TP1_0_SL0_5": {"tp": 1.0, "sl": 0.5},
    "TP1_5_SL0_75": {"tp": 1.5, "sl": 0.75},
    "TP2_0_SL1_0": {"tp": 2.0, "sl": 1.0},
}


def label_paths_from_arrays(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    index: int,
    forward_horizon_candles: int = 96,
    interval_minutes: int = 15,
) -> dict | None:
    if index + forward_horizon_candles >= len(closes):
        return None
    entry = float(closes[index])
    if entry <= 0 or np.isnan(entry):
        return None

    future_highs = highs[index + 1:index + 1 + forward_horizon_candles]
    future_lows = lows[index + 1:index + 1 + forward_horizon_candles]
    if len(future_highs) < forward_horizon_candles or len(future_lows) < forward_horizon_candles:
        return None

    paths = {}
    for name, template in PATH_TEMPLATES.items():
        paths[name] = {
            **_label_side_arrays(future_highs, future_lows, entry, template["tp"], template["sl"], "long", interval_minutes),
            **_label_side_arrays(future_highs, future_lows, entry, template["tp"], template["sl"], "short", interval_minutes),
        }
    return paths


def _label_side_arrays(
    future_highs: np.ndarray,
    future_lows: np.ndarray,
    entry: float,
    tp_pct: float,
    sl_pct: float,
    side: str,
    interval_minutes: int,
) -> dict:
    prefix = f"{side}_"
    if side == "long":
        tp_price = entry * (1 + tp_pct / 100.0)
        sl_price = entry * (1 - sl_pct / 100.0)
        tp_hits = future_highs >= tp_price
        sl_hits = future_lows <= sl_price
        mfe = float(np.nanmax((future_highs - entry) / entry * 100.0))
        mae = float(np.nanmin((future_lows - entry) / entry * 100.0))
    else:
        tp_price = entry * (1 - tp_pct / 100.0)
        sl_price = entry * (1 + sl_pct / 100.0)
        tp_hits = future_lows <= tp_price
        sl_hits = future_highs >= sl_price
        mfe = float(np.nanmax((entry - future_lows) / entry * 100.0))
        mae = float(np.nanmin((entry - future_highs) / entry * 100.0))

    both = tp_hits & sl_hits
    tp_idx = int(np.argmax(tp_hits)) if tp_hits.any() else None
    sl_idx = int(np.argmax(sl_hits)) if sl_hits.any() else None
    both_idx = int(np.argmax(both)) if both.any() else None

    result = {
        f"{prefix}tp_hit_first": False,
        f"{prefix}sl_hit_first": False,
        f"{prefix}neither_hit": False,
        f"{prefix}ambiguous_hit": False,
        f"{prefix}mfe_pct": mfe,
        f"{prefix}mae_pct": mae,
        f"{prefix}time_to_tp_minutes": None,
        f"{prefix}time_to_sl_minutes": None,
    }

    first_candidates = [idx for idx in [tp_idx, sl_idx, both_idx] if idx is not None]
    if not first_candidates:
        result[f"{prefix}neither_hit"] = True
        return result

    first = min(first_candidates)
    minutes = (first + 1) * interval_minutes
    if both_idx == first:
        result[f"{prefix}ambiguous_hit"] = True
        result[f"{prefix}time_to_tp_minutes"] = minutes
        result[f"{prefix}time_to_sl_minutes"] = minutes
    elif tp_idx == first:
        result[f"{prefix}tp_hit_first"] = True
        result[f"{prefix}time_to_tp_minutes"] = minutes
    else:
        result[f"{prefix}sl_hit_first"] = True
        result[f"{prefix}time_to_sl_minutes"] = minutes
    return result
