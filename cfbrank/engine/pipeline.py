"""Orchestrates the five stages. Pure: no I/O, no clock, no randomness.

    rank(dataset, config) -> RankingResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from cfbrank.config import Config
from cfbrank.engine import grounds, h2h, market, performance, resume_strength
from cfbrank.engine.base_score import TeamBase, compute_base, pool, priority, rerank
from cfbrank.engine.evidence import EdgeFact, score_edges
from cfbrank.engine.graph import Digraph, nontrivial_sccs
from cfbrank.engine.order import Edge, OrderResult, minimum_violations_order
from cfbrank.engine.provenance import cycle_explanation, team_reasons
from cfbrank.engine.regression import RegressionEvent, apply_upset_regression
from cfbrank.engine import cover
from cfbrank.engine.resume import apply_resume_adjustment
from cfbrank.models import Dataset, GameResult, Warning_
from cfbrank.normalize import sort_key


@dataclass(slots=True)
class CycleReport:
    cycle_id: str
    members: list[str]
    edges: list[EdgeFact]
    overridden: list[EdgeFact]
    explanation: str

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass(slots=True)
class RankingResult:
    year: int
    week: int
    season_type: str
    snapshot_id: str

    order: list[str]                       # the full pool, final order
    teams: dict[str, TeamBase]
    edge_facts: dict[Edge, EdgeFact]
    results: list[GameResult]              # pool results after series collapse
    all_results: list[GameResult]
    ordering: OrderResult
    cycles: list[CycleReport]
    team_cycle: dict[str, str]
    regressions: list[RegressionEvent]
    series_notes: list[h2h.SeriesNote]
    skipped: list[h2h.SkippedGame]
    warnings: list[Warning_]
    resume: resume_strength.ResumeRatings = field(
        default_factory=resume_strength.ResumeRatings
    )
    market: market.MarketRatings = field(default_factory=market.MarketRatings)
    performance: performance.PerformanceRatings = field(
        default_factory=performance.PerformanceRatings
    )
    reasons: dict[str, list[str]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def final_rank(self, team: str) -> int:
        return self.order.index(team) + 1


def snapshot_id(year: int, season_type: str, week: int) -> str:
    return f"{year}-{season_type}-{week:02d}"


def rank(dataset: Dataset, cfg: Config) -> RankingResult:
    s1 = cfg.section("stage1")
    s2 = cfg.section("stage2")
    s3 = cfg.section("stage3")
    s4 = cfg.section("stage4")
    ev_cfg = cfg.section("stage4.evidence")
    mkt_cfg = cfg.section("market")
    perf_cfg = cfg.section("performance")
    resume_cfg = cfg.section("resume")

    # -- the week to rank through -----------------------------------------
    week, season_type, cutoff = h2h.resolve_week(
        dataset.games,
        dataset.calendar,
        cfg["season.week"],
        cfg["season.season_type"],
        material_teams=dataset.material_teams,
    )
    all_results, skipped = h2h.to_results(dataset.games, cutoff)

    fbs = {
        name
        for g in dataset.games
        for name, cls in (
            (g.home_team, g.home_classification),
            (g.away_team, g.away_classification),
        )
        if (cls or "").lower() == "fbs"
    }

    # -- quality inputs ----------------------------------------------------
    # Both are restricted to games already finished at the cutoff. A line posted
    # for a game nobody has played is information the ranking is not entitled to,
    # and on a completed season's file it is information from the future.
    played_ids = {
        g.game_id
        for g in dataset.games
        if g.game_id is not None
        and g.completed
        and g.home_points is not None
        and g.away_points is not None
        and g.order_key <= cutoff
    }
    mkt = market.compute(dataset.lines, fbs, cutoff, mkt_cfg, played_ids)
    perf = performance.compute(dataset.ppa, fbs, cutoff, perf_cfg, played_ids)
    # The resume is measured against the market's view of each opponent, so it
    # has to come after the market solve.
    res = resume_strength.compute(
        dataset.games, mkt.ratings, mkt.home_field_points, fbs, cutoff, resume_cfg
    )

    # -- stage 1 ----------------------------------------------------------
    teams, warnings = compute_base(
        dataset.ratings,
        dataset.records_by_team(),
        fbs,
        s1,
        resume_ranks=res.ranks,
        resume_probs=res.probability,
        resume_expected={t: res.expected_wins(t) or 0.0 for t in res.ranks},
        resume_actual={t: res.actual_wins(t) or 0 for t in res.ranks},
        market_ranks=mkt.ranks,
        market_ratings=mkt.ratings,
        ppa_ranks=perf.ranks,
        ppa_ratings=perf.ratings,
        ppa_raw=perf.raw,
        market_games=mkt.games,
        ppa_games=perf.games,
    )
    warnings = (
        list(dataset.warnings)
        + list(mkt.warnings)
        + list(perf.warnings)
        + list(res.warnings)
        + list(warnings)
    )
    by_team = {tb.team: tb for tb in teams}

    # -- stage 2 ----------------------------------------------------------
    apply_resume_adjustment(
        teams,
        all_results,
        s2,
        ev_cfg,
        cover.cover_margins(
            dataset.lines,
            dataset.games,
            cutoff,
            cap=float(s2.get("cover_game_cap", cover.GAME_CAP)),
            fit_venue=bool(s2.get("fit_cover_venue", True)),
        ),
        scale=float(s1.get("w_adjust", 1.0)),
    )

    # -- stage 3 (measured on the provisional pool) ------------------------
    pool_size = int(s1["pool_size"])
    provisional = {tb.team for tb in pool(teams, pool_size)}
    prov_results, _ = h2h.collapse_series(
        h2h.restrict(all_results, provisional), str(s4["split_series"])
    )
    regressions = apply_upset_regression(teams, prov_results, s3)

    # -- final pool, final results ----------------------------------------
    final_pool = pool(teams, pool_size)
    pool_names = [tb.team for tb in final_pool]
    pool_set = set(pool_names)
    results, series_notes = h2h.collapse_series(
        h2h.restrict(all_results, pool_set), str(s4["split_series"])
    )

    # -- stage 4 ----------------------------------------------------------
    # The gap term reads the market's neutral-field points where they exist and
    # falls back to FPI where they do not, which is the role FPI keeps: settling
    # which result to set aside when nothing better is available.
    quality = {
        tb.team: (tb.market_rating if tb.market_rating is not None else tb.fpi)
        for tb in final_pool
    }
    # The grounds read the WHOLE board's ratings, not just the pool's: a team's
    # subsequent win over a good unranked side is real evidence, and a loss to
    # anybody at all is still a loss. Market ratings only -- FPI has no week
    # dimension, so folding it in here would read a season-end snapshot into a
    # question about what has happened since a particular Saturday.
    edge_grounds = grounds.compute(results, all_results, mkt.ratings, s4.get("grounds", {}))
    edge_facts = score_edges(
        results,
        quality,
        h2h.opponents(all_results),
        ev_cfg,
        home_field_points=mkt.home_field_points if mkt else None,
        grounds=edge_grounds,
    )
    base_rank = {tb.team: tb.base_rank for tb in final_pool}
    # Pool ranks are 1..N so the drift term is measured inside the pool.
    pool_base_rank = {t: i + 1 for i, t in enumerate(pool_names)}
    ordering = minimum_violations_order(pool_names, edge_facts, pool_base_rank, s4)
    final_order = ordering.order
    final_rank = {t: i + 1 for i, t in enumerate(final_order)}

    # -- stage 5: report contradictions -----------------------------------
    graph = h2h.build_graph(results, pool_names)
    overridden_set = set(ordering.violated)
    cycles: list[CycleReport] = []
    team_cycle: dict[str, str] = {}
    sccs = sorted(
        nontrivial_sccs(graph),
        key=lambda c: (-len(c), min(final_rank.get(t, 10**6) for t in c)),
    )
    for n, members in enumerate(sccs, 1):
        cid = f"C{n}"
        inside = sorted(members, key=lambda t: final_rank.get(t, 10**6))
        facts = [
            edge_facts[(w, l)]
            for (w, l) in graph.subgraph(members).edges()
            if (w, l) in edge_facts
        ]
        over = [f for f in facts if (f.winner, f.loser) in overridden_set]
        for t in members:
            team_cycle[t] = cid
        cycles.append(
            CycleReport(
                cycle_id=cid,
                members=inside,
                edges=sorted(facts, key=lambda f: -f.weight),
                overridden=sorted(over, key=lambda f: f.weight),
                explanation=cycle_explanation(inside, len(facts) - len(over), over),
            )
        )

    # -- justifications ----------------------------------------------------
    reasons: dict[str, list[str]] = {}
    for team in final_order:
        tb = by_team[team]
        wins = [edge_facts[k] for k in edge_facts if k[0] == team]
        losses = [edge_facts[k] for k in edge_facts if k[1] == team]
        honored = sorted(
            (
                (f, final_rank.get(f.loser))
                for f in wins
                if (f.winner, f.loser) not in overridden_set
            ),
            key=lambda pair: (pair[1] or 10**6),
        )
        over_losses = sorted(
            (
                (f, final_rank.get(f.winner))
                for f in losses
                if (f.winner, f.loser) in overridden_set
            ),
            key=lambda pair: (pair[1] or 10**6),
        )
        over_wins = sorted(
            (
                (f, final_rank.get(f.loser))
                for f in wins
                if (f.winner, f.loser) in overridden_set
            ),
            key=lambda pair: (pair[1] or 10**6),
        )
        cid = team_cycle.get(team)
        size = next((c.size for c in cycles if c.cycle_id == cid), 0)
        reasons[team] = team_reasons(
            tb, final_rank[team], honored, over_losses, over_wins, cid, size,
        )

    counts = {
        "rated_teams": len(teams),
        "pool_size": len(pool_names),
        "games_considered": len(all_results),
        "pool_results": len(results),
        "series_split": sum(1 for n in series_notes if n.meetings > 1),
        "h2h_honored": len(ordering.honored),
        "h2h_overridden": len(ordering.violated),
        "cycles": len(cycles),
        "largest_cycle": max((c.size for c in cycles), default=0),
        "regressions": len(regressions),
        "resume_rated": len(res.probability),
        "market_rated": len(mkt.ratings),
        "ppa_rated": len(perf.ratings),
        "h2h_licensed": sum(
            1 for f in edge_facts.values() if f.grounds is not None and f.grounds.licensed
        ),
        "h2h_over_licence": sum(1 for v in ordering.excess.values() if v > 0),
        "max_rise": -min(ordering.drift.values(), default=0),
        "max_drop": max(ordering.drift.values(), default=0),
        "local_search_passes": ordering.passes,
    }

    return RankingResult(
        year=dataset.year,
        week=week,
        season_type=season_type,
        snapshot_id=snapshot_id(dataset.year, season_type, week),
        order=final_order,
        teams=by_team,
        edge_facts=edge_facts,
        results=results,
        all_results=all_results,
        ordering=ordering,
        cycles=cycles,
        team_cycle=team_cycle,
        regressions=regressions,
        series_notes=series_notes,
        skipped=skipped,
        warnings=warnings,
        resume=res,
        market=mkt,
        performance=perf,
        reasons=reasons,
        counts=counts,
    )
