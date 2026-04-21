import pandas as pd
import numpy as np


def vwap(df: pd.DataFrame) -> pd.Series:
    """
    Volume-Weighted Average Price.

    Args:
        df: OHLCV DataFrame with 'close' and 'volume' columns.

    Returns:
        Series of cumulative VWAP values over the session.

    Trader use: VWAP is the fair-value anchor for institutional flow. Price
    above VWAP favors longs; below favors shorts. It resets each session, so
    it's most useful intraday (1m–1h candles). Note: this implementation uses
    close price rather than typical price — preserved from MT6 for consistency.
    """
    cum_vol_price = (df['close'] * df['volume']).cumsum()
    cum_vol = df['volume'].cumsum()
    return cum_vol_price / cum_vol


def ema(df: pd.DataFrame, period: int = 20, column: str = 'close') -> pd.Series:
    """
    Exponential Moving Average.

    Args:
        df:     OHLCV DataFrame.
        period: Lookback window (default 20). Common: 9, 20, 50, 200.
        column: Column to compute on (default 'close').

    Returns:
        Series of EMA values.

    Trader use: EMA reacts faster than SMA. Price above EMA = bullish bias.
    EMA crossovers (e.g. 9/20) are entry triggers. Used in MT7 scoring as a
    trend confirmation layer.
    """
    return df[column].ewm(span=period, adjust=False).mean()


def rsi(df: pd.DataFrame, period: int = 14, column: str = 'close') -> pd.Series:
    """
    Relative Strength Index (Wilder's smoothing via EWM).

    Args:
        df:     OHLCV DataFrame.
        period: Lookback window (default 14).
        column: Column to compute on (default 'close').

    Returns:
        Series of RSI values in the range [0, 100].

    Trader use: RSI < 30 = oversold (potential long entry); RSI > 70 =
    overbought (potential short entry). In strong trends, RSI can stay
    overbought/oversold for extended periods — use with trend context, not
    as a standalone reversal signal.
    """
    delta = df[column].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)

    ma_up = up.ewm(com=period - 1, adjust=False).mean()
    ma_down = down.ewm(com=period - 1, adjust=False).mean()

    rs = ma_up / ma_down
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range.

    Args:
        df:     OHLCV DataFrame with 'high', 'low', 'close' columns.
        period: Smoothing window (default 14).

    Returns:
        Series of ATR values in price units. First (period - 1) values are NaN.

    Trader use: ATR measures realized volatility in absolute price terms. Used
    to set stop-loss distance (e.g. 1.5× ATR below entry) and tier spacing in
    the laddering system. Higher ATR = wider stops needed.
    """
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def atr_pct(df: pd.DataFrame, price: float, period: int = 14) -> float:
    """
    ATR expressed as a percentage of the current price.

    Args:
        df:     OHLCV DataFrame (needs at least `period + 1` rows).
        price:  Current market price (used as the denominator).
        period: ATR lookback window (default 14).

    Returns:
        ATR% as a float (e.g. 2.3 means ATR is 2.3% of price).
        Returns 0.0 if ATR cannot be computed (insufficient data).

    Trader use: Normalises volatility across assets with wildly different price
    scales — comparing BTC ATR to SHIB ATR in raw terms is meaningless. ATR%
    lets you compare apples to apples and feeds into volatility_regime().
    """
    if price <= 0:
        return 0.0
    atr_series = atr(df, period=period)
    last_atr = atr_series.dropna().iloc[-1] if not atr_series.dropna().empty else 0.0
    return (last_atr / price) * 100


def volatility_regime(atr_pct_value: float) -> str:
    """
    Classify market volatility into a named regime based on ATR%.

    Args:
        atr_pct_value: Output of atr_pct() — ATR as a % of current price.

    Returns:
        One of: "low", "medium", "high", "extreme".

    Thresholds:
        low     < 1.0%
        medium  < 2.5%
        high    < 5.0%
        extreme >= 5.0%

    Trader use: Regime gates leverage recommendations. In "extreme" regimes,
    the scoring system should cap leverage regardless of signal strength —
    a 5% ATR means a 1× position can lose 5% in a single candle.
    """
    if atr_pct_value < 1.0:
        return "low"
    elif atr_pct_value < 2.5:
        return "medium"
    elif atr_pct_value < 5.0:
        return "high"
    else:
        return "extreme"


def daily_trend_direction(klines) -> str:
    """
    Determine daily trend direction from raw MEXC kline data.
    Uses swing structure: 2 consecutive higher highs + higher lows = LONG,
    2 consecutive lower highs + lower lows = SHORT, else NEUTRAL.

    Args:
        klines: MEXC kline response data — either:
                - dict with "high" and "low" arrays (contract kline API format), or
                - list of entries [timestamp_ms, open, close, high, low, volume, ...]
                Expected at least 6 candles, sorted oldest-first (MEXC default for contract klines).

    Returns: "LONG" | "SHORT" | "NEUTRAL"
    """
    try:
        if isinstance(klines, dict):
            highs = [float(x) for x in klines.get("high", [])]
            lows  = [float(x) for x in klines.get("low",  [])]
        elif isinstance(klines, list):
            highs = [float(entry[3]) for entry in klines]
            lows  = [float(entry[4]) for entry in klines]
        else:
            return "NEUTRAL"

        if len(highs) < 6 or len(lows) < 6:
            return "NEUTRAL"

        # MEXC contract kline API returns candles oldest-first (ascending timestamps).
        # No reversal needed — data is already in chronological order for swing detection.

        lookback = 2
        n = len(highs)
        swing_highs: list[float] = []
        swing_lows:  list[float] = []

        for i in range(lookback, n - lookback):
            if (all(highs[i] > highs[j] for j in range(i - lookback, i)) and
                    all(highs[i] > highs[j] for j in range(i + 1, i + lookback + 1))):
                swing_highs.append(highs[i])
            if (all(lows[i] < lows[j] for j in range(i - lookback, i)) and
                    all(lows[i] < lows[j] for j in range(i + 1, i + lookback + 1))):
                swing_lows.append(lows[i])

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return "NEUTRAL"

        # Take last 4 swings; check if the final 2 are ascending or descending
        last_2_highs = swing_highs[-4:][-2:]
        last_2_lows  = swing_lows[-4:][-2:]

        highs_up   = last_2_highs[1] > last_2_highs[0]
        highs_down = last_2_highs[1] < last_2_highs[0]
        lows_up    = last_2_lows[1]  > last_2_lows[0]
        lows_down  = last_2_lows[1]  < last_2_lows[0]

        if highs_up and lows_up:
            return "LONG"
        if highs_down and lows_down:
            return "SHORT"
        return "NEUTRAL"

    except Exception:
        return "NEUTRAL"


if __name__ == "__main__":
    # Fake OHLCV: 30 candles drifting upward with realistic noise.
    # 30 rows ensures ATR (14-period) and RSI (14-period) both have
    # enough data to produce non-NaN outputs.
    np.random.seed(42)
    n = 30
    closes = 100 + np.cumsum(np.random.randn(n) * 0.5)
    highs = closes + np.abs(np.random.randn(n) * 0.3)
    lows = closes - np.abs(np.random.randn(n) * 0.3)
    volumes = np.random.randint(1000, 5000, size=n).astype(float)

    df = pd.DataFrame({
        'open':   closes - np.random.randn(n) * 0.1,
        'high':   highs,
        'low':    lows,
        'close':  closes,
        'volume': volumes,
    })

    current_price = float(df['close'].iloc[-1])
    print(f"Synthetic price: {current_price:.4f}")

    vwap_series = vwap(df)
    print(f"VWAP (last):     {vwap_series.iloc[-1]:.4f}")
    assert not vwap_series.isnull().all(), "VWAP returned all NaN"

    ema_series = ema(df, period=9)
    print(f"EMA-9 (last):    {ema_series.iloc[-1]:.4f}")
    assert not ema_series.isnull().all(), "EMA returned all NaN"

    rsi_series = rsi(df, period=14)
    last_rsi = rsi_series.dropna().iloc[-1]
    print(f"RSI-14 (last):   {last_rsi:.2f}")
    assert 0 <= last_rsi <= 100, f"RSI out of range: {last_rsi}"

    atr_series = atr(df, period=14)
    last_atr = atr_series.dropna().iloc[-1]
    print(f"ATR-14 (last):   {last_atr:.4f}")
    assert last_atr > 0, "ATR should be positive"

    atr_pct_val = atr_pct(df, current_price, period=14)
    print(f"ATR% :           {atr_pct_val:.4f}%")
    assert atr_pct_val > 0, "ATR% should be positive"

    regime = volatility_regime(atr_pct_val)
    print(f"Volatility:      {regime}")
    assert regime in ("low", "medium", "high", "extreme"), f"Bad regime: {regime}"

    print("\nAll checks passed.")
