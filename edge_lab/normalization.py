from __future__ import annotations

import pandas as pd
import numpy as np


def rolling_percentile_rank(series: pd.Series, window: int = 500, min_periods: int = 100) -> pd.Series:
    """
    Rolling percentile rank using only the prior/current window.

    The current value is compared against values in its own rolling window.
    No global percentiles and no future rows are used.
    """
    return series.rolling(window=window, min_periods=min_periods).rank(pct=True)


def rolling_decile(series: pd.Series, window: int = 500, min_periods: int = 100) -> pd.Series:
    ranks = rolling_percentile_rank(series, window=window, min_periods=min_periods)
    deciles = np.ceil(ranks * 10)
    deciles = deciles.clip(lower=1, upper=10)
    return deciles.astype("Int64")
