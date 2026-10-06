"""Stage 1 -- the base order: what you achieved, and how good you actually are.

Three inputs, each a national rank where 1 is best, so the score is a weighted
rank average and lower is better:

    base = w_sor * SoR_rank + w_market * Market_rank + w_perf * PPA_rank

  * **Strength of Record** is the resume, and it is now computed here rather
    than taken from ESPN: `engine.resume_strength` asks how often a top-25 team
    would match this record against this schedule. Lower is harder. Its weight is
    a judgement about what a ranking is *for*, not something a backtest sets.
  * **Market** is the neutral-field rating implied by betting lines
    (`engine.market`), the sharpest available read on how good a team is.
  * **PPA** is opponent-adjusted points added per play (`engine.performance`) --
    the eye test, counted.

Market and PPA split the quality half evenly, which is what measured best: on
2025, predicting every later game from what was knowable at the time, market
alone got 61.2% of ranked-vs-ranked games and PPA alone 62.8%, while a 50/50
blend got 67.3%. Either signal alone is worse than both together.

`w_fpi` and `w_sos` both ship at 0 and both stay available as knobs.

  * FPI left the base because the market and PPA do its job better, and because
    it cannot be honestly backtested: `/ratings/fpi` serves one end-of-season
    snapshot with no week dimension, so grading it on a finished season reads the
    answer. It is still used where a single number has to break a tie, and in
    weighing which head-to-head result to set aside. ESPN's Strength of Record
    came from the same undated snapshot, which is why the resume is computed
    here now -- it is what lets the whole formula be scored honestly.
  * Strength of Record already accounts for the schedule, so adding Strength of
    Schedule on top double-counts it and rewards playing hard games regardless of
    the result.

A team missing a term (too few lined games to imply a market rating, say) is
scored on the terms it has, with the weights renormalised so its base stays on
the same scale as everyone else's. The renormalised weights are what `formula()`
renders, so the published string always adds up to `raw_score`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import AbstractSet, Mapping, Sequence

from cfbrank.engine.stats import rank_desc
from cfbrank.models import Record, TeamRating, Warning_
from cfbrank.normalize import sort_key


@dataclass(slots=True)
class TeamBase:
    team: str
    conference: str | None
    sor_rank: int
    sos_rank: int
    fpi: float
    fpi_rank: int
    fpi_resume_rank: int | None = None
    awp_rank: int | None = None
    rsos_rank: int | None = None
    game_control_rank: int | None = None
    eff_overall: float | None = None
    eff_offense: float | None = None
    eff_defense: float | None = None
    eff_special: float | None = None
    record: Record | None = None

    resume_prob: float | None = None     # P(a top-25 team matches this record)
    resume_rank: int | None = None       # our own SoR; ESPN's stays in sor_rank
    resume_expected_wins: float | None = None
    resume_actual_wins: int | None = None
    market_rating: float | None = None   # neutral-field points implied by the market
    market_rank: int | None = None
    market_games: int = 0
    ppa_rating: float | None = None      # opponent-adjusted net PPA per play
    ppa_rank: int | None = None
    ppa_raw: float | None = None
    ppa_games: int = 0

    # (label, weight actually applied, rank used) for every term in the base.
    # Stored rather than recomputed so the rendered formula cannot drift from
    # the number it claims to explain.
    base_terms: tuple[tuple[str, float, int], ...] = ()

    base_raw: float = 0.0           # stage 1 only
    raw_rank: int = 0
    resume_adj: float = 0.0         # stage 2 delta (positive = penalty)
    resume_components: dict[str, float] = field(default_factory=dict)
    resume_detail: dict[str, object] = field(default_factory=dict)
    regression_adj: float = 0.0     # stage 3 delta
    regression_notes: list[str] = field(default_factory=list)

    @property
    def base_score(self) -> float:
        """The score stage 4 ranks against: stage 1 + stage 2 + stage 3."""
        return self.base_raw + self.resume_adj + self.regression_adj

    base_rank: int = 0              # rank by base_score, assigned by rerank()

    def formula(self) -> str:
        """Render the terms actually in play, at the weights actually applied.

        A zero-weight term must not appear, or the site shows a misleading
        "+ 0 x SoS #97" next to a team the schedule did not move. A team missing
        an input shows its renormalised weights, so the arithmetic on screen is
        the arithmetic that produced the score.
        """
        shown = [f"{w:g} x {label} #{rank}" for label, w, rank in self.base_terms if w]
        return (" + ".join(shown) if shown else "no weighted terms") + f" = {self.base_raw:.2f}"

    @property
    def missing_terms(self) -> tuple[str, ...]:
        """Base inputs this team has no value for, lowest-numbered first."""
        present = {label for label, _w, _r in self.base_terms}
        return tuple(
            label
            for label, value in (("Mkt", self.market_rank), ("PPA", self.ppa_rank))
            if value is None and label not in present
        )


def rateable(
    ratings: Sequence[TeamRating], fbs: AbstractSet[str], require_fpi: bool, fbs_only: bool
) -> tuple[list[TeamRating], list[Warning_]]:
    keep: list[TeamRating] = []
    warnings: list[Warning_] = []
    for r in ratings:
        if fbs_only and fbs and r.team not in fbs:
            warnings.append(Warning_("excluded_not_fbs", "not an FBS team", r.team))
            continue
        if r.sor_rank is None or r.sos_rank is None or (require_fpi and r.fpi is None):
            warnings.append(
                Warning_("excluded_missing_rating", "no usable SoR/SoS/FPI entry", r.team)
            )
            continue
        keep.append(r)
    return keep, warnings


def weighted_terms(
    candidates: Sequence[tuple[str, float, int | None]], warn_team: str | None = None
) -> tuple[tuple[tuple[str, float, int], ...], float, list[Warning_]]:
    """Pick the terms a team actually has, renormalise, and score them.

    Dropping a term without renormalising would hand the team a *lower* (better)
    base purely for missing data, which is the kind of bug that quietly rewards
    the teams with the least information about them.
    """
    nominal = sum(w for _label, w, _rank in candidates if w)
    live = [(label, w, rank) for label, w, rank in candidates if w and rank is not None]
    warnings: list[Warning_] = []

    if not live:
        return (), 0.0, warnings
    have = sum(w for _label, w, _rank in live)
    scale = (nominal / have) if have else 0.0
    terms = tuple((label, w * scale, int(rank)) for label, w, rank in live)  # type: ignore[arg-type]

    if abs(scale - 1.0) > 1e-9 and warn_team is not None:
        dropped = ", ".join(
            label for label, w, rank in candidates if w and rank is None
        )
        warnings.append(
            Warning_(
                "base_term_missing",
                f"no {dropped} rating; the remaining terms carry it "
                f"(weights scaled x{scale:.2f})",
                warn_team,
            )
        )
    return terms, sum(w * rank for _label, w, rank in terms), warnings


def compute_base(
    ratings: Sequence[TeamRating],
    records: Mapping[str, Record],
    fbs: AbstractSet[str],
    cfg: Mapping[str, object],
    resume_ranks: Mapping[str, int] | None = None,
    resume_probs: Mapping[str, float] | None = None,
    resume_expected: Mapping[str, float] | None = None,
    resume_actual: Mapping[str, int] | None = None,
    market_ranks: Mapping[str, int] | None = None,
    market_ratings: Mapping[str, float] | None = None,
    ppa_ranks: Mapping[str, int] | None = None,
    ppa_ratings: Mapping[str, float] | None = None,
    ppa_raw: Mapping[str, float] | None = None,
    market_games: Mapping[str, int] | None = None,
    ppa_games: Mapping[str, int] | None = None,
) -> tuple[list[TeamBase], list[Warning_]]:
    w_sor = float(cfg.get("w_sor", 0.60))  # type: ignore[arg-type]
    w_sos = float(cfg.get("w_sos", 0.0))  # type: ignore[arg-type]
    w_fpi = float(cfg.get("w_fpi", 0.0))  # type: ignore[arg-type]
    w_market = float(cfg.get("w_market", 0.25))  # type: ignore[arg-type]
    w_perf = float(cfg.get("w_perf", 0.15))  # type: ignore[arg-type]

    resume_ranks = resume_ranks or {}
    resume_probs = resume_probs or {}
    resume_expected = resume_expected or {}
    resume_actual = resume_actual or {}
    market_ranks = market_ranks or {}
    market_ratings = market_ratings or {}
    ppa_ranks = ppa_ranks or {}
    ppa_ratings = ppa_ratings or {}
    ppa_raw = ppa_raw or {}
    market_games = market_games or {}
    ppa_games = ppa_games or {}

    usable, warnings = rateable(
        ratings, fbs, bool(cfg.get("require_fpi", True)), bool(cfg.get("fbs_only", True))
    )

    # Rank FPI ourselves rather than trusting resumeRanks.fpi, then cross-check:
    # `fpi` is a rating (higher better) while `resumeRanks.fpi` is a rank (lower
    # better), and silently confusing the two would invert the whole board.
    fpi_values = {r.team: float(r.fpi) for r in usable if r.fpi is not None}
    fpi_ranks = rank_desc(fpi_values)

    teams: list[TeamBase] = []
    for r in usable:
        own = fpi_ranks.get(r.team, len(fpi_ranks) + 1)
        if r.fpi_resume_rank is not None and abs(own - r.fpi_resume_rank) > 2:
            warnings.append(
                Warning_(
                    "fpi_rank_mismatch",
                    f"we rank FPI #{own}, the feed says #{r.fpi_resume_rank}",
                    r.team,
                )
            )
        # Our own resume rank is the SoR term. ESPN's is the fallback for a team
        # we could not score (too few completed games) and stays published for
        # comparison either way.
        own_resume = resume_ranks.get(r.team)
        terms, base_raw, term_warnings = weighted_terms(
            [
                ("SoR", w_sor, own_resume if own_resume is not None else int(r.sor_rank)),
                ("Mkt", w_market, market_ranks.get(r.team)),
                ("PPA", w_perf, ppa_ranks.get(r.team)),
                ("FPI", w_fpi, own),
                ("SoS", w_sos, int(r.sos_rank)),  # type: ignore[arg-type]
            ],
            warn_team=r.team,
        )
        warnings.extend(term_warnings)
        teams.append(
            TeamBase(
                team=r.team,
                conference=r.conference,
                sor_rank=int(r.sor_rank),  # type: ignore[arg-type]
                sos_rank=int(r.sos_rank),  # type: ignore[arg-type]
                fpi=float(r.fpi) if r.fpi is not None else 0.0,
                fpi_rank=own,
                fpi_resume_rank=r.fpi_resume_rank,
                awp_rank=r.awp_rank,
                rsos_rank=r.rsos_rank,
                game_control_rank=r.game_control_rank,
                eff_overall=r.eff_overall,
                eff_offense=r.eff_offense,
                eff_defense=r.eff_defense,
                eff_special=r.eff_special,
                record=records.get(r.team),
                resume_prob=resume_probs.get(r.team),
                resume_rank=resume_ranks.get(r.team),
                resume_expected_wins=resume_expected.get(r.team),
                resume_actual_wins=resume_actual.get(r.team),
                market_rating=market_ratings.get(r.team),
                market_rank=market_ranks.get(r.team),
                market_games=int(market_games.get(r.team, 0)),
                ppa_rating=ppa_ratings.get(r.team),
                ppa_rank=ppa_ranks.get(r.team),
                ppa_raw=ppa_raw.get(r.team),
                ppa_games=int(ppa_games.get(r.team, 0)),
                base_terms=terms,
                base_raw=base_raw,
            )
        )

    for i, tb in enumerate(sorted(teams, key=priority), 1):
        tb.raw_rank = i
    rerank(teams)
    return teams, warnings


def priority(tb: TeamBase) -> tuple[float, int, int, tuple[str, str]]:
    """The total, deterministic ordering key. Every tie bottoms out in the name."""
    resume = tb.resume_rank if tb.resume_rank is not None else tb.sor_rank
    return (tb.base_score, resume, tb.fpi_rank, sort_key(tb.team))


def rerank(teams: Sequence[TeamBase]) -> list[TeamBase]:
    """Assign base_rank from the current base_score. Call after stages 2 and 3."""
    ordered = sorted(teams, key=priority)
    for i, tb in enumerate(ordered, 1):
        tb.base_rank = i
    return ordered


def pool(teams: Sequence[TeamBase], size: int) -> list[TeamBase]:
    return sorted(teams, key=priority)[: max(0, size)]
