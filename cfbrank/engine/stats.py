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
