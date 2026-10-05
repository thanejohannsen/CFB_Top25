"""Command line entry point.

Exit codes are a protocol the GitHub Actions workflow depends on:

    0  ranked, content changed, files written  -> commit
    2  ranked, content identical              -> no-op, success
    3  upstream unavailable                   -> warn, keep the last snapshot
    4  configuration error                    -> fail
    1  anything unexpected                    -> fail
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from cfbrank import __version__
from cfbrank.config import load as load_config
from cfbrank.engine.pipeline import RankingResult, rank
from cfbrank.errors import ConfigError, DataQualityError, UpstreamUnavailable
from cfbrank.models import Dataset
from cfbrank.output.history import load_previous, snapshot_path, update_index
from cfbrank.output.schema import build_payload, validate_payload
from cfbrank.output.writer import dumps_stable, write_if_changed
from cfbrank.sources.loader import build_dataset

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNCHANGED = 2
EXIT_UPSTREAM = 3
EXIT_CONFIG = 4


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cfbrank", description="Build an explainable college football Top 25."
    )
    p.add_argument("--config", default="config/ranking.toml")
    p.add_argument("--set", dest="overrides", action="append", default=[], metavar="path=value")
    p.add_argument("--year", type=int)
    p.add_argument("--week", help='week number, or "auto"')
    p.add_argument("--pool-size", type=int)
    p.add_argument("--offline", action="store_true", help="read fixtures; never touch the network")
    p.add_argument("--fixtures", help="fixture root directory")
    p.add_argument("--fixture-year", help="fixture subdirectory to read")
    p.add_argument("--out", help="override output.rankings_path")
    p.add_argument("--dry-run", action="store_true", help="compute but write nothing")
    p.add_argument("--force", action="store_true", help="write even when unchanged")
    p.add_argument("--print-top", type=int, metavar="N", help="print the top N to stdout")
    p.add_argument("--explain", metavar="TEAM", help="print one team's full justification")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--version", action="version", version=f"cfbrank {__version__}")
    return p


def _overrides_from_flags(args: argparse.Namespace) -> list[str]:
    extra: list[str] = []
    if args.year is not None:
        extra.append(f"season.year={args.year}")
    if args.week is not None:
        extra.append(f"season.week={args.week}")
    if args.pool_size is not None:
        extra.append(f"stage1.pool_size={args.pool_size}")
    if args.offline:
        extra.append("source.offline=true")
    if args.fixtures:
        extra.append(f"source.fixtures_dir={args.fixtures}")
    if args.fixture_year:
        extra.append(f"source.fixture_year={args.fixture_year}")
    if args.out:
        extra.append(f"output.rankings_path={args.out}")
    return list(args.overrides) + extra


def open_source(cfg, offline_env: bool):
    """Pick the data source. Offline never imports requests."""
    if bool(cfg["source.offline"]) or offline_env:
        from cfbrank.sources.fixtures import FixtureSource

        return FixtureSource(cfg["source.fixtures_dir"], cfg["source.fixture_year"])

    from cfbrank.sources.cfbd import CFBDClient
    from cfbrank.sources.http_cache import HttpCache

    # The environment wins over the committed value, so adding a GitHub Actions
    # secret later silently takes precedence without touching the config.
    key = os.environ.get("CFBD_API_KEY", "").strip() or str(cfg.get("source.api_key", "")).strip()
    if not key:
        raise UpstreamUnavailable(
            "No CFBD API key.\n"
            "  Set CFBD_API_KEY, or put one in config/ranking.toml under [source] api_key,\n"
            "  get a free key at https://collegefootballdata.com/key, or\n"
            "  run with --offline to rank from the checked-in fixtures."
        )
    cache = HttpCache(cfg["source.cache_dir"], int(cfg["source.cache_ttl_minutes"]))
    return CFBDClient(
        api_key=key,
        base_url=cfg["source.base_url"],
        cache=cache,
        timeout=float(cfg["source.timeout_seconds"]),
        max_retries=int(cfg["source.max_retries"]),
        backoff=float(cfg["source.retry_backoff_seconds"]),
    )


def print_table(result: RankingResult, n: int, out=None) -> None:
    out = sys.stdout if out is None else out
    season = f"{result.year} {result.season_type} week {result.week}"
    print(f"\n  CFB Top 25 -- {season}\n", file=out)
    print(f"  {'#':>3}  {'team':22}{'rec':>7}  {'base':>5}{'move':>6}  {'SoR':>4}{'SoS':>5}{'FPI':>7}  flags", file=out)
    print("  " + "-" * 76, file=out)
    for i, team in enumerate(result.order[:n], 1):
        tb = result.teams[team]
        drift = tb.base_rank - i
        flags = []
        if team in result.team_cycle:
            flags.append(result.team_cycle[team])
        if abs(tb.regression_adj) > 1e-9:
            flags.append("regressed")
        line = (
            f"  {i:3}. {team:22}{(tb.record.overall if tb.record else '?'):>7}"
            f"  {tb.base_rank:>5}{(f'{drift:+d}' if drift else ''):>6}"
            f"  {tb.sor_rank:>4}{tb.sos_rank:>5}{tb.fpi:>+7.1f}  {' '.join(flags)}"
        )
        print(line.rstrip(), file=out)
    c = result.counts
    print(
        f"\n  {c['h2h_honored']} of {c['h2h_honored'] + c['h2h_overridden']} head-to-head results honoured"
        f"  |  {c['cycles']} contradiction loop(s)"
        + (f", largest {c['largest_cycle']} teams" if c["cycles"] else "")
        + f"  |  {c['regressions']} upset regression(s)",
        file=out,
    )
    if result.ordering.violated:
        print("\n  Overridden head-to-head results (weakest evidence first):", file=out)
        rank_of = {t: i + 1 for i, t in enumerate(result.order)}
        for w, l in result.ordering.violated:
            f = result.edge_facts[(w, l)]
            print(
                f"    {w} (#{rank_of[w]}) beat {l} (#{rank_of[l]}) {f.score} {f.site}"
                f"  adj {f.adj_margin:+.1f}, FPI gap {f.fpi_gap:+.1f}, weight {f.weight:.2f}",
                file=out,
            )
    print(file=out)


def print_explain(result: RankingResult, team: str, out=None) -> int:
    out = sys.stdout if out is None else out
    match = next((t for t in result.order if t.lower() == team.lower()), None)
    if match is None:
        match = next((t for t in result.order if team.lower() in t.lower()), None)
    if match is None:
        print(f"'{team}' is not in the pool of {len(result.order)} teams.", file=sys.stderr)
        return EXIT_ERROR

    tb = result.teams[match]
    final = result.order.index(match) + 1
    print(f"\n  {match} -- #{final} (base #{tb.base_rank}, resume #{tb.raw_rank})\n", file=out)
    for line in result.reasons.get(match, []):
        print(f"    - {line}", file=out)
    print(f"\n    resume adjustment: {tb.resume_adj:+.2f}", file=out)
    for k, v in sorted(tb.resume_components.items()):
        print(f"      {k:16} {v:+.3f}", file=out)
    if abs(tb.regression_adj) > 1e-9:
        print(f"    upset regression:  {tb.regression_adj:+.2f}", file=out)
    rank_of = {t: i + 1 for i, t in enumerate(result.order)}
    overridden = set(result.ordering.violated)
    print("\n    head-to-head inside the pool:", file=out)
    rows = [f for k, f in result.edge_facts.items() if match in k] or []
    if not rows:
        print("      (none -- played nobody else in the pool)", file=out)
    from cfbrank.engine.provenance import site_phrase

    for f in sorted(rows, key=lambda f: rank_of.get(f.winner if f.loser == match else f.loser, 99)):
        won = f.winner == match
        verb = "beat" if won else "lost to"
        other = f.loser if won else f.winner
        # Score and venue from this team's point of view, not the winner's.
        score = f"{f.winner_points}-{f.loser_points}" if won else f"{f.loser_points}-{f.winner_points}"
        where = site_phrase(f, match)
        state = "OVERRIDDEN" if (f.winner, f.loser) in overridden else "honoured"
        print(
            f"      {verb:8} {other:20} #{rank_of.get(other, '-'):<3} {score:>7} {where:18}"
            f" adj {f.adj_margin:+6.1f}  FPI gap {f.fpi_gap:+6.1f}  weight {f.weight:.2f}  [{state}]",
            file=out,
        )
    print(file=out)
    return EXIT_OK


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = (lambda *a: None) if args.quiet else (lambda *a: print(*a, file=sys.stderr))

    cfg = load_config(args.config, _overrides_from_flags(args))
    year = int(cfg["season.year"])

    source = open_source(cfg, os.environ.get("CFB_OFFLINE") == "1")
    dataset: Dataset = build_dataset(source, year, str(cfg["season.season_type"]))
    if not dataset.ratings:
        raise DataQualityError(f"no usable ratings for {year}")

    result = rank(dataset, cfg)
    log(
        f"ranked {result.snapshot_id}: {result.counts['rated_teams']} teams, "
        f"{result.counts['pool_results']} pool results, "
        f"{result.counts['h2h_overridden']} overridden, "
        f"{result.counts['cycles']} cycle(s)"
    )

    if args.print_top:
        print_table(result, args.print_top)
    if args.explain:
        code = print_explain(result, args.explain)
        if code != EXIT_OK:
            return code

    rankings_path = Path(cfg["output.rankings_path"])
    history_dir = Path(cfg["output.history_dir"])
    index_path = Path(cfg["output.index_path"])
    precision = int(cfg["output.float_precision"])

    previous = load_previous(
        index_path,
        history_dir,
        str(cfg["output.previous_snapshot"]),
        result.snapshot_id,
        result.year,
    )
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = build_payload(result, dataset, cfg, generated_at, previous)

    problems = validate_payload(payload)
    if problems:
        for p in problems:
            print(f"payload invalid: {p}", file=sys.stderr)
        return EXIT_ERROR

    text = dumps_stable(payload, precision)

    if args.dry_run:
        log("dry run: nothing written")
        return EXIT_OK

    existing = None
    if rankings_path.exists():
        try:
            import json

            existing = json.loads(rankings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
    unchanged = bool(
        existing
        and (existing.get("meta") or {}).get("content_hash") == payload["meta"]["content_hash"]
    )

    if unchanged and not args.force:
        log(f"unchanged ({payload['meta']['content_hash'][:19]}...); nothing to commit")
        return EXIT_UNCHANGED

    write_if_changed(snapshot_path(history_dir, result.snapshot_id), text, args.force)
    write_if_changed(rankings_path, text, args.force)
    index = update_index(index_path, payload, history_dir)
    write_if_changed(index_path, dumps_stable(index, precision), args.force)
    log(f"wrote {rankings_path}, {snapshot_path(history_dir, result.snapshot_id)}, {index_path}")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except UpstreamUnavailable as exc:
        print(f"data source unavailable: {exc}", file=sys.stderr)
        return EXIT_UPSTREAM
    except DataQualityError as exc:
        print(f"unusable data: {exc}", file=sys.stderr)
        return EXIT_UPSTREAM
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
