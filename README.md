# CFB Top 25

**Live site: <https://thanejohannsen.github.io/CFB_Top25/>**

A college football Top 25 built from an explicit algorithm rather than opinion,
published as a static GitHub Pages site where **every placement opens to show
exactly why it is there**.

```
 1. Georgia      base #2   +1   12-2   SoR 5   SoS 17   FPI +21.4   C1
 2. Indiana      base #4   +2   16-0   SoR 1   SoS 10   FPI +31.5
 3. Oregon       base #1   -2   13-2   SoR 2   SoS  6   FPI +23.9
 4. Miami        base #14 +10   13-3   SoR 3   SoS  7   FPI +22.4   C1 regressed
 ...
 99 of 111 head-to-head results honoured | 2 contradiction loops, largest 22 teams
```

## How it ranks

1. **Resume order** — `0.75 × StrengthOfRecord_rank + 0.25 × StrengthOfSchedule_rank`.
   Both are national ranks where 1 is best, so lower is better. Strength of Record
   carries most of the weight because schedule strength alone rewards nobody for
   winning; the schedule term stops a team sitting high on a soft slate.
2. **Resume adjustment** — Strength of Record knows *who* you played but not *how*
   you lost. A three-point road loss and a 24-point home loss are not the same
   result, so loss quality, best win and game control adjust the base score.
3. **Upset regression** — when a result spans more than 15 places, both teams are
   pulled halfway to their midpoint. The upset says the winner was underrated *and*
   the loser overrated, so neither gets teleported and neither gets a free pass.
4. **Weighted head-to-head** — the published order is the one minimising

   ```
   cost = strength × Σ conviction(each overridden result)
        + drift_weight × Σ |position − resume position| ^ drift_exponent
   ```

   so the results it contradicts are, by construction, the least convincing ones.
   Conviction blends location-adjusted margin, the FPI gap, recency and common
   opponents. A one-point home win scores *negative*: home field alone is worth
   more than the margin.
5. **Contradiction reporting** — loops (A beat B, B beat C, C beat A) are found with
   Tarjan's algorithm and published, along with every overridden result.

### Why head-to-head is weighted rather than absolute

Strictly enforcing "if you beat them you are above them" means topologically
sorting the head-to-head graph, which holds a team behind *every* team its
conqueror is behind. Schedule density tracks schedule strength, so this
systematically sinks teams who play hard schedules and floats teams who play
nobody. Measured on the completed 2025 season it put three teams with
bottom-third schedules in the top twelve while dropping the two best resumes to
17th and 20th.

Real head-to-head graphs are also not tidy triangles. That same season produced a
single tangle of **22 mutually entangled teams**, where resolving each loop
separately is not computable. A weighted objective has neither problem, and a
team nobody in the pool played simply stays where its resume put it.

## Quick start

```bash
pip install -r requirements.txt

make test            # 179 tests, fully offline, no API key needed
make rank            # rank the 2025 fixtures and print the table
make serve           # http://localhost:8000
```

Useful one-offs:

```bash
python3 -m cfbrank --offline --fixture-year 2025 --year 2025 --print-top 25
python3 -m cfbrank --offline --fixture-year 2025 --year 2025 --explain Miami
python3 -m cfbrank --set stage1.w_sor=0.6 --set stage4.strength=20 --dry-run --print-top 25
```

## Going live

The published `docs/data/*.json` is pre-generated, so the site needs no key to
work. The key only drives the scheduled refresh, and it is already committed:

```bash
python3 -m cfbrank --print-top 25      # live data, no setup
```

One manual step remains, because it cannot be done from the API:

**Turn on Pages** — Settings → Pages → Source: *Deploy from a branch*, branch
`main`, folder `/docs`.

After that, Actions → *Rank* → Run workflow, and it runs daily at 12:37 UTC
through the season, committing a new snapshot only when the ranking actually
changes.

### About the committed key

`config/ranking.toml` carries a real CollegeFootballData.com key under
`[source] api_key`, so the workflow needs no repository secret. **This repository
is public**, which was a deliberate trade: no setup, in exchange for a key anyone
can read. Treat it as disposable — if it stops working, get a new one at
<https://collegefootballdata.com/key> and replace that one line.

The key is kept out of everything the site publishes: `redacted_config()` in
`cfbrank/output/schema.py` blanks it before the config is echoed into each
snapshot, and the test suite asserts it appears nowhere in the output.

To use a different key without editing the file, set `CFBD_API_KEY` — the
environment always wins:

```bash
CFBD_API_KEY=... python3 -m cfbrank --print-top 25
```

Adding a `CFBD_API_KEY` repository secret later works the same way, with no code
change.

## Tuning

Every weight lives in [`config/ranking.toml`](config/ranking.toml). The knobs that
actually change the character of the list:

| Setting | Default | Effect |
| --- | --- | --- |
| `stage1.w_sor` / `w_sos` | 0.75 / 0.25 | How much a soft schedule drags down a good resume. |
| `stage3.strength` | 0.5 | How far a wide-gap upset pulls both teams together. **Do not set 1.0** — the stage then fights stage 4, which drags the winner straight back, and the list swings week to week. |
| `stage4.strength` | 14.0 | Positions of resume drift that one unit of head-to-head conviction buys. |
| `stage4.drift_exponent` | 1.5 | Above 1, long moves cost disproportionately more, so how far a result can move a team scales with how convincing it was. At 1.0 a single marginal win can carry a team across the board. |
| `stage1.pool_size` | 40 | How many teams are eligible to be reordered. |

Try a change without committing it:

```bash
python3 -m cfbrank --offline --fixture-year 2025 --year 2025 \
  --set stage4.strength=25 --dry-run --print-top 25
```

Any change to the engine will fail the golden test. That is the point — read the
diff, and if the change was intended:

```bash
python3 scripts/make_golden.py
```

## Layout

```
cfbrank/
  engine/      base_score, resume, regression, h2h, evidence, order, graph, pipeline
  sources/     cfbd (live), fixtures (offline), http_cache, loader
  output/      schema, writer, history
config/        ranking.toml -- every weight, one file
docs/          the published site (GitHub Pages serves this directory)
scripts/       refresh_fixtures, make_golden, serve_docs
tests/         unittest suite + real 2025 and 2026 fixtures + the golden file
```

The engine is pure: `rank(dataset, config) -> RankingResult`, no I/O, no clock, no
randomness. Output is byte-stable, so an unchanged ranking produces an unchanged
file and the scheduled job commits nothing.

Dependencies: **`requests`**, and nothing else. Config is TOML via stdlib
`tomllib`, tests are stdlib `unittest`, and the site is vanilla JavaScript with no
build step.

## Data

Games, records, and ESPN's FPI, Strength of Record and Strength of Schedule all
come from [CollegeFootballData.com](https://collegefootballdata.com). A full run
makes four API calls, cached for six hours.

Two consequences worth knowing: those ratings are third-party opinions that move
during the week, and the FPI endpoint has no per-week parameter — so a week's
snapshot is taken when it is generated, and earlier weeks cannot be reconstructed
after the fact.

## Known limitations

These are stated on the site's methodology page too, rather than buried here:

- The schedule term rewards playing a hard schedule independently of results, so a
  good team in a weak conference is penalised for something it only partly controls.
- The ordering is found by local search: a strong local optimum, not a proven global
  one. It reaches the same answer from any input ordering in practice, and the test
  suite checks that, but it is an observation rather than a guarantee.
- A chain of individually reasonable moves can still add up to a large one. Per-team
  drift is published so this is visible rather than hidden.
- Early in a season Strength of Record is volatile, and an unbeaten team with a soft
  schedule can look better than it is.

## Licence

MIT — see [LICENSE](LICENSE).
