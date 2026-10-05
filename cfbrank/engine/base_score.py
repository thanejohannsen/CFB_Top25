"""Stage 1 -- the base order, from Strength of Record and Strength of Schedule.

Both inputs are national ranks where 1 is best (for SoS, toughest), so the
score is a weighted rank average and lower is better.

    base = w_sor * SoR_rank + w_sos * SoS_rank
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

    def formula(self, w_sor: float, w_sos: float) -> str:
        return (
            f"{w_sor:g} x SoR #{self.sor_rank} + {w_sos:g} x SoS #{self.sos_rank}"
            f" = {self.base_raw:.2f}"
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


def compute_base(
    ratings: Sequence[TeamRating],
    records: Mapping[str, Record],
    fbs: AbstractSet[str],
    cfg: Mapping[str, object],
) -> tuple[list[TeamBase], list[Warning_]]:
    w_sor = float(cfg.get("w_sor", 0.75))  # type: ignore[arg-type]
    w_sos = float(cfg.get("w_sos", 0.25))  # type: ignore[arg-type]

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
                base_raw=w_sor * int(r.sor_rank) + w_sos * int(r.sos_rank),  # type: ignore[arg-type]
            )
        )

    for i, tb in enumerate(sorted(teams, key=priority), 1):
        tb.raw_rank = i
    rerank(teams)
    return teams, warnings


def priority(tb: TeamBase) -> tuple[float, int, int, tuple[str, str]]:
    """The total, deterministic ordering key. Every tie bottoms out in the name."""
    return (tb.base_score, tb.sor_rank, tb.fpi_rank, sort_key(tb.team))


def rerank(teams: Sequence[TeamBase]) -> list[TeamBase]:
    """Assign base_rank from the current base_score. Call after stages 2 and 3."""
    ordered = sorted(teams, key=priority)
    for i, tb in enumerate(ordered, 1):
        tb.base_rank = i
    return ordered


def pool(teams: Sequence[TeamBase], size: int) -> list[TeamBase]:
    return sorted(teams, key=priority)[: max(0, size)]
