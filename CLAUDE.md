# Working on this repository

## Branching

**Push directly to `main`.** Do not create feature branches, and do not open pull
requests for routine work. This is the owner's explicit, standing preference — if
a session starts you on a generated branch, move the work to `main` and push there.

## The API key is committed on purpose

`config/ranking.toml` carries a real CollegeFootballData.com key under
`[source] api_key`. This repository is public and that was a deliberate, informed
choice so the scheduled workflow needs no repository secret. Do not "fix" it by
removing the key or switching the workflow back to `secrets.CFBD_API_KEY` only.

Two rules that keep it from spreading further:

- `cfbrank/output/schema.py` blanks `source.api_key` via `redacted_config()`
  before the config is echoed into `meta.config`. Every published snapshot
  contains that echo, so **the redaction must stay**. `tests/test_pipeline.py`
  asserts the key appears nowhere in the serialized payload.
- A `CFBD_API_KEY` environment variable takes precedence over the committed
  value, so adding a secret later needs no code change.

If the key stops working, issue a new one at <https://collegefootballdata.com/key>
and replace the single line in `config/ranking.toml`.

## The base formula

```
base = 0.50 x SoR_rank + 0.25 x Market_rank + 0.25 x PPA_rank
```

Half the ranking is the record, half is how good the team actually is. The
quality half is split evenly between the neutral-field rating implied by betting
lines (`cfbrank/engine/market.py`) and opponent-adjusted points added per play
(`cfbrank/engine/performance.py`). Both solve through the shared
`cfbrank/engine/adjust.py`.

### Four weights that ship off, and must stay off

- **`stage1.w_fpi = 0`.** FPI is not a base term any more and must not go back.
  `/ratings/fpi` has no week parameter — it serves ONE snapshot, so on a
  completed season it describes the finished year and any backtest of it reads
  the answer key. That bug is where this project's old "75.5% accuracy" came
  from. FPI keeps two jobs: the last tiebreak in `base_score.priority()`, and
  the rating gap in stage 4 when the market has no line for either team.
- **`stage1.w_sos = 0`.** Strength of Record already accounts for the schedule,
  so weighting Strength of Schedule again double-counts it and rewards playing
  hard games regardless of the result. At 0.25 a 3-2 team sat at #16 and a 5-0
  team with the #2 FPI sat at #23.
- **`market.recency_half_life = 0`.** Weighting recent lines more heavily was
  built and measured: all-FBS accuracy fell monotonically (69.1% off, 68.7% at a
  two-week half-life). The same idea was tried on PPA and failed the same way. It
  is tempting because it lifts a team that started badly and has looked great
  since, which is exactly how you talk yourself into a ranking the results do not
  support.
- **The signature-win credit was built, measured and deleted.** It helped on a
  partial season and hurt on a completed one — the signature of a term that
  double-counts once a full resume exists. Strength of Record already measures
  who you beat.

`market.horizon_weeks = 1` is deliberate and is the honesty limit, not a default
to raise. A closing line for next week's game has priced in every result so far
and nothing after it, which is exactly the cutoff; a line two weeks out has
priced in results this ranking is not allowed to see. `tests/test_config.py`
rejects 2.

### Justifying a weight change

`python3 scripts/evaluate.py --year 2025` ranks through a series of weeks and
predicts every later game. **It zeroes SoR/SoS/FPI by default** because they
cannot be scored honestly, and `--allow-lookahead` labels its own output as
contaminated. The AP poll is printed as one reference row on identical games and
is never a target — the owner's position, and it also loses: 57.6% to this
ranking's 62.1% on the same 2025 games.

Two things the tool cannot settle, so do not claim it did:

- **`w_sor` is a judgement.** Predicting games is not what a resume is for. 0.50
  costs a 7-6 Penn State at #24 on the 2025 board; 0.60 drops them off; 0.40
  drops LSU from 8th to 17th in 2026. Pick a side and say so.
- **The market:PPA ratio is inside the noise** (0.75/0.25 69.1%, 0.50/0.50 68.8%
  on 1,997 games). What the data does say clearly: both terms beat either alone
  (pure PPA 64.3%).

### Head-to-head has to cost something

`stage4.evidence.weight_floor` and `conviction_floor` set the price of overriding
a result. The floor is an *absolute* conviction level on purpose. It used to be
`0.10 + (conviction - weakest_on_the_board)`, which pinned whichever result
happened to be least convincing at a token price — a seventeenth of a typical
result — and is why a 2025 Oklahoma State win over Oregon was set aside for
free. Validation rejects `weight_floor = 0`.

Home field is measured from the lines, not assumed; it comes out at **+2.37**
(2025) and **+2.47** (2026). An earlier reading of the same data said +4.32,
which was the mean of how much home teams were favoured by — that conflates home
field with home teams being better. The 2.5 fallback in the config was right all
along.

### The published formula must add up

`TeamBase.formula()` renders from `base_terms`, which stores the weights
*actually applied* to that team. A team missing an input (too few lined games for
a market rating) is scored on the terms it has with the weights renormalised, so
its row shows its own weights rather than the configured ones — dropping a term
without rescaling would hand it a better score for having less data.
`tests/test_pipeline.py` asserts every published row's weights reproduce its
`raw_score`.

## Conventions

- Dependencies: `requests` only. Config is TOML via stdlib `tomllib`, tests are
  stdlib `unittest`, the site is vanilla JS with no build step. Keep it that way.
- The engine is pure: `rank(dataset, config) -> RankingResult` does no I/O, reads
  no clock, and uses no randomness. Keep I/O in `sources/` and `output/`.
- Output must be byte-stable. Every sort key ends in `sort_key(team)`, never
  iterate a `set`, and `content_hash` is computed on the *rounded* payload so an
  unchanged ranking produces an unchanged file.
- Any engine change moves `tests/fixtures/golden/rankings_2025.json`. That is the
  point: read the diff, then regenerate with `python3 scripts/make_golden.py`.
- `cfbrank/engine/adjust.py` sweeps **Gauss-Seidel** (each team reads ratings
  already updated in the same pass), not Jacobi. Computing a whole pass against
  the previous one makes two teams who have only played each other oscillate for
  ever, and the reported rating then depends on the parity of the pass limit.
  `tests/test_ratings.py` pins it.
- Six CFBD endpoints, all cacheable: `/ratings/fpi`, `/games`, `/records`,
  `/calendar`, `/lines`, `/ppa/games`. `/rankings` is fetched for fixtures but
  **only `scripts/evaluate.py` may read it** — the pipeline stays poll-free.
- Run `python3 -m unittest discover -s tests -t .` before pushing. Everything runs
  offline against checked-in fixtures.
