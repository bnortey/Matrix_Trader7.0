def generate_ladders(
    current_price: float,
    atr_value: float,
    tiers: int = 3,
    direction: str = "LONG",
    risk_reward: tuple[float, float] = (1, 2),
) -> tuple[list[float], list[float], float]:
    """
    Generate laddered entry prices, take-profit levels, and a stop-loss for a trade.

    Entry tiers are spaced evenly across one ATR risk unit, starting close to
    current price and moving away from it. Take-profit tiers mirror that spacing
    on the reward side. The stop-loss sits one tier-step beyond the farthest
    entry, so it is always wider than any single entry's risk.

    Args:
        current_price: Latest close price of the instrument.
        atr_value:     Most recent ATR value (in price units, from lib/indicators.atr).
        tiers:         Number of entry and exit levels to generate (default 3).
        direction:     "LONG" or "SHORT". LONG places entries below price, exits
                       above. SHORT places entries above price, exits below.
        risk_reward:   Two-tuple (sl_mult, tp_mult). The sl_mult scales the total
                       entry range in ATR units; tp_mult scales the total exit
                       range. Default (1, 2) means risk 1× ATR to target 2× ATR.

    Returns:
        entries:   List of `tiers` entry prices, ordered nearest-to-farthest
                   from current_price.
        exits:     List of `tiers` take-profit prices, ordered nearest-to-farthest
                   from current_price (in the profit direction).
        stop_loss: Single worst-case exit price. One tier-step beyond the
                   farthest entry — if price reaches this level, the entire
                   position is wrong.

    Trader use: Feed `current_price` and the latest ATR into this function to
    get a full trade plan instantly. Scale into entries as price moves toward
    each level; take partial profits at each exit. If stop_loss is breached,
    close everything. The (1, 2) default gives a 1:2 risk/reward on each tier.
    Raise tp_mult (e.g. to 3) in trending markets; tighten sl_mult (e.g. to 0.75)
    in choppy markets.
    """
    if direction not in ("LONG", "SHORT"):
        raise ValueError(f"direction must be 'LONG' or 'SHORT', got {direction!r}")
    if atr_value <= 0:
        raise ValueError(f"atr_value must be positive, got {atr_value}")
    if tiers < 1:
        raise ValueError(f"tiers must be >= 1, got {tiers}")

    sl_mult, tp_mult = risk_reward
    sign = 1 if direction == "SHORT" else -1  # entries move away from price

    # Each tier is spaced evenly across (atr_value * sl_mult).
    # Tier 1 is nearest, tier N is farthest.
    entries = [
        current_price + sign * (atr_value * sl_mult) * (i + 1) / tiers
        for i in range(tiers)
    ]

    # Exits move in the opposite direction (toward profit).
    exits = [
        current_price - sign * (atr_value * tp_mult) * (i + 1) / tiers
        for i in range(tiers)
    ]

    # Stop-loss: one tier-step beyond the farthest entry.
    # For LONG: farthest entry is entries[-1] (lowest price); stop is below that.
    # For SHORT: farthest entry is entries[-1] (highest price); stop is above that.
    stop_loss = current_price + sign * (atr_value * sl_mult) * (tiers + 1) / tiers

    return entries, exits, stop_loss


if __name__ == "__main__":
    price = 100.0
    atr = 2.0

    def print_ladder(direction: str, entries, exits, stop_loss) -> None:
        print(f"\n{'='*40}")
        print(f"  {direction} @ {price:.2f}  |  ATR = {atr:.2f}")
        print(f"{'='*40}")
        if direction == "SHORT":
            print(f"  STOP LOSS : {stop_loss:.4f}  ← bail if price reaches here")
            for i, e in enumerate(entries):
                print(f"  ENTRY {i+1}   : {e:.4f}")
            print(f"  --- current price: {price:.2f} ---")
            for i, x in enumerate(exits):
                print(f"  TP {i+1}      : {x:.4f}")
        else:
            for i, x in reversed(list(enumerate(exits))):
                print(f"  TP {i+1}      : {x:.4f}")
            print(f"  --- current price: {price:.2f} ---")
            for i, e in enumerate(entries):
                print(f"  ENTRY {i+1}   : {e:.4f}")
            print(f"  STOP LOSS : {stop_loss:.4f}  ← bail if price reaches here")

    # LONG test
    entries, exits, stop_loss = generate_ladders(price, atr, tiers=3, direction="LONG")
    print_ladder("LONG", entries, exits, stop_loss)
    assert all(e < price for e in entries), "LONG entries must be below price"
    assert all(x > price for x in exits), "LONG exits must be above price"
    assert stop_loss < entries[-1], "LONG stop must be below deepest entry"
    assert len(entries) == len(exits) == 3

    # SHORT test
    entries, exits, stop_loss = generate_ladders(price, atr, tiers=3, direction="SHORT")
    print_ladder("SHORT", entries, exits, stop_loss)
    assert all(e > price for e in entries), "SHORT entries must be above price"
    assert all(x < price for x in exits), "SHORT exits must be below price"
    assert stop_loss > entries[-1], "SHORT stop must be above highest entry"
    assert len(entries) == len(exits) == 3

    # Symmetry test: LONG and SHORT ladders should be mirrors of each other
    long_e, long_x, long_sl = generate_ladders(price, atr, direction="LONG")
    short_e, short_x, short_sl = generate_ladders(price, atr, direction="SHORT")
    for le, se in zip(long_e, short_e):
        assert abs((price - le) - (se - price)) < 1e-9, "Entry symmetry broken"
    for lx, sx in zip(long_x, short_x):
        assert abs((lx - price) - (price - sx)) < 1e-9, "Exit symmetry broken"

    print("\nAll checks passed.")
