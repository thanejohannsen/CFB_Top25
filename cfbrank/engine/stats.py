"""Small statistics helpers. No numpy -- the inputs are a few hundred floats."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from cfbrank.normalize import sort_key

SIGMA_FLOOR = 1e-9


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def pstdev(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mu = mean(xs)
    return (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5


def zscorer(xs: Sequence[float]) -> Callable[[float], float]:
    """Return a z-score function for this sample.

    A degenerate sample (every value equal, as in week 1) yields a function
    returning 0.0 rather than dividing by zero.
    """
    mu, sigma = mean(xs), pstdev(xs)
    if sigma <= SIGMA_FLOOR:
        return lambda _x: 0.0
    return lambda x: (x - mu) / sigma


def penalty_scaler(xs: Sequence[float]) -> Callable[[float], float]:
    """Scale by the sample's spread WITHOUT centring it, floored at zero.

    For a ONE-SIDED quantity: one where zero already means "nothing to answer
    for", so the only question is how far past zero you are.

    `zscorer` centres on the sample mean, which turns any below-average value
    into a negative — a credit. That is right for a two-sided measure and wrong
    for a penalty, because it pays a team for having a *tidy* example of the
    thing rather than none of it. Loss quality is the case in point: the mean of
    teams that have lost is dominated by ugly losses, so a good loss scored
    below it and an undefeated team's flat 0.0 came out *worse* than losing well.

    Keeping the spread as the divisor means the caller's weight still reads as
    "rank points per standard deviation", exactly as for `zscorer`. Same
    degenerate-sample guard, for the same reason.
    """
    sigma = pstdev(xs)
    if sigma <= SIGMA_FLOOR:
        return lambda _x: 0.0
    return lambda x: max(0.0, x) / sigma


def rank_desc(values: Mapping[str, float]) -> dict[str, int]:
    """1 = highest value. Ties share the minimum rank. Platform-stable order."""
    ordered = sorted(values.items(), key=lambda kv: (-kv[1], sort_key(kv[0])))
    out: dict[str, int] = {}
    prev_val: float | None = None
    prev_rank = 0
    for i, (name, val) in enumerate(ordered, 1):
        if prev_val is not None and val == prev_val:
            out[name] = prev_rank
        else:
            out[name] = i
            prev_rank, prev_val = i, val
    return out


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)


def half_life_weight(age_weeks: float, half_life: float, floor: float = 0.0) -> float:
    """How much a thing `age_weeks` old still counts, halving every `half_life`.

    `floor` is what it decays toward rather than zero: an old game fades, it does
    not stop having happened. A non-positive `half_life` turns decay off.
    """
    if half_life <= 0:
        return 1.0
    decayed = 0.5 ** (max(0.0, age_weeks) / half_life)
    return floor + (1.0 - floor) * decayed


def round_floats(obj: Any, precision: int) -> Any:
    """Recursively round floats so serialized output diffs stay clean."""
    if isinstance(obj, float):
        r = round(obj, precision)
        # Normalize -0.0 to 0.0 so the bytes never flip on sign of zero.
        return 0.0 if r == 0 else r
    if isinstance(obj, dict):
        return {k: round_floats(v, precision) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [round_floats(v, precision) for v in obj]
    return obj
