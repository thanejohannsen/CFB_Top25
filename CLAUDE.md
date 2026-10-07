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
base  = 0.55 x SoR_rank + 0.30 x Market_rank + 0.15 x PPA_rank
score = base + w_adjust x resume adjustment (incl. cover) + upset regression
```

In the owner's words: **SoR is the resume** (who you beat and how hard that was),
**Mkt is Vegas's power ranking** (how good the money thinks you ARE,
`engine/market.py`), **PPA is the game tape counted** (how well you actually
PLAYED per snap, `engine/performance.py`). Mkt is an opinion formed before games;
PPA measures what happened during them. Both solve through `engine/adjust.py`.

The resume outweighs the two quality signals combined, deliberately -- what a
team has achieved should outrank how good it looks. `tests/test_config.py` pins
that relationship, and that both quality weights stay above zero.

**`TUNING.md` is the owner-facing guide to every knob.** Keep it in step with
`config/ranking.toml` when you add or retune anything; the file itself is the
single source of truth and must stay that way (no second copy of these values).

**The SoR term is computed here, not taken from ESPN.**
`cfbrank/engine/resume_strength.py` walks a reference team (the 25th-best market
rating) through the schedule a team actually played and asks how often it would
finish with at least that many wins. Texas opening 4-0 with Ohio State and
Tennessee scores 2.6%; a 5-0 against nobody scores far higher.

Two things about it must not be broken:

- **The curve is a NORMAL CDF with sigma 13.5, not a logistic.** A logistic at
  the same scale prices a 30-point favourite at 0.90 against a real ~0.99 and a
  14-point favourite at 0.74 against ~0.85, which made every resume in the
  country look harder to earn than it was. `tests/test_ratings.py` pins the curve
  against known spread/probability pairs.
- **The per-game list is sorted before the Poisson-binomial DP.** `at_least()`
  accumulates floats game by game, so an unsorted list changes the last bits of
  the probability, which can flip a rank and break byte-stability. The same
  applies to `engine/cover.py`. `tests/test_pipeline.py::TestDeterminism`
  shuffles `dataset.games` directly and will catch it.

ESPN's SoR is still published beside ours for comparison and is no longer an
input. Replacing it is what lets the WHOLE formula be backtested: ESPN serves it
from one undated snapshot, so `scripts/evaluate.py` used to zero it and could
only score half the base.

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
- **`market.recency_half_life = 0`.** Weighting recent *lines* more heavily was
  built and measured. The evidence is genuinely mixed rather than damning, and an
  earlier version of this file overstated it: after the Gauss-Seidel fix, all-FBS
  accuracy goes 68.6% (off) / 67.9% (6-week) / 68.1% (3-week), while AP-vs-AP
  goes 64.2% / 64.8% / **67.0%**. It is off because the market term already reads
  next week's lines and is a current opinion by construction, not because the
  numbers condemn it. Head-to-head and play-by-play decay instead; see below.
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
predicts every later game. It zeroes **SoS and FPI** by default -- ESPN serves
those from one undated snapshot -- and `--allow-lookahead` labels its own output
as contaminated. Since the resume is computed here now, the whole formula is
scored rather than half of it.

The AP poll is printed as one reference row on identical games and is never a
target. It also loses, by a lot: **67.0% to 58.7%** on the same 179 games from
2025.

Two things the tool cannot settle, so do not claim it did:

- **`w_sor` is a judgement.** Predicting games is not what a resume is for. 0.50
  costs a 7-6 Penn State at #24 on the 2025 board; 0.60 drops them off; 0.40
  drops LSU from 8th to 17th in 2026. Pick a side and say so.
- **The market:PPA ratio is inside the noise** (0.75/0.25 69.1%, 0.50/0.50 68.8%
  on 1,997 games). What the data does say clearly: both terms beat either alone
  (pure PPA 64.3%), so neither may go to zero. The split itself is the owner's
  call and has moved (50/50, then 25/15); do not "restore" a previous ratio.

### `stage1.w_adjust` scales the COMPONENTS, not just the total

One dial for the whole stage-2 resume adjustment, shipped at 0.5. It lives in
`[stage1]` for discoverability -- the question it answers is "how much should the
adjustment matter next to the three numbers?" -- but it scales stage 2, so it is
deliberately excluded from the base-weight sum check in `validate()` and from
`tests/test_pipeline.py`'s weights assertion.

`apply_resume_adjustment()` applies it to each component before summing, not to
the total afterwards. `tb.resume_components` is published and rendered in the
site's panel, so halving the total while leaving the parts alone would make that
panel stop adding up. `tests/test_stages.py` pins it.

It is pinned at 1.0 in `make_golden.FROZEN`, so retuning it does not move the
regression net.

**Know what it does not do.** Scaling the stage scales it for everybody, so a
team leading the field on this stage still leads it: Northwestern, whose
four-game cover credit prompted the dial, moved one place at 0.5. The
"Northwestern is flattered by a small sample" problem is still open -- the
candidates are a cap on the cover component, or scoring cover on an absolute
scale so sample size actually counts (shrinkage is currently inert because the
z-scoring normalises it straight back out).

### Against the number (`stage2.w_cover`, `engine/cover.py`)

A resume says who you beat; it cannot say whether you looked like you meant it.
`cover margin = actual margin - the posted line`, per game, z-scored across the
board and converted to rank points in stage 2.

It is what separates two unbeaten teams: in 2026 week 5 Alabama were +14.1 per
game against the number and Notre Dame +0.8, and that is why Notre Dame is not
first. The asymmetry the owner asked for falls out of the arithmetic -- the
easier a game was supposed to be, the more a flat performance costs, so Texas
loses almost nothing for close wins over good teams.

**It can only ever be a modifier.** Ranked alone it puts Georgia State and New
Mexico top of the country, because it measures exceeding expectations rather than
being good. At `w_cover = 6` that leak is already visible on the real board:
Northwestern at 3-1 climbs to #6 and UCLA into the top 12. 4.0 is the shipped
value for that reason, not for the accuracy curve.

**I was wrong about this term and the record should say so.** I predicted it
would cost predictive accuracy, on the grounds that markets are efficient and
cover rates sit near 50%. It does the opposite -- on identical 2025 AP-vs-AP
games, accuracy runs 61.5% at `w_cover=0`, 67.0% at 4, 69.3% at 6, 70.4% at 10,
68.7% at 14. The error was conflating two questions: past ATS barely predicts
*future ATS*, but margin against a per-game market line is a well opponent-adjusted
measure of *team strength*, which is a different thing.

### `stage4.strength = 4.0` is deliberate; do not "restore" it to 14

It was 14. Three findings moved it, and without them 14 looks considered and 4
looks like drift:

- **The resume already prices head-to-head, so 14 paid for it twice.** A resume
  is a list of games with win probabilities. Swap Ole Miss's win over LSU for a
  cupcake at the same record and their resume goes 30.2% -> 63.1%: the win is
  worth 2.1x before stage 4 touches anything. That double-count is what made the
  ranking feel over-constrained.
- **Lower predicts better, monotonically.** 2025, same 179 AP-vs-AP games: 65.9%
  at 14, 67.0% at 6, 67.6% at 2, 68.2% at 0. Four games, but it never reverses.
- **With the stage OFF, 20 of 27 results are still honoured** on 2026 week 5 --
  the base order usually agrees already. It only ever argues about seven games.

It is **not** 0, and that is a values call rather than a measurement: head-to-head
is the premise of this project and the site promises "my team beat them and is
ranked below them" a real answer. At 0 that section lists 58 contradictions the
ranking does nothing about.

The visible cost of 4, which the owner accepted: Ole Miss beating LSU goes back
to being overridden (Ole Miss #15, LSU #7), because the forcer no longer drags
LSU down to meet it. Honouring that one result was what the extra constraint
bought.

Do not add a signature-win bonus to the resume to compensate. That was built,
measured and removed once already for the same double-counting reason.

### Head-to-head has to cost something

Results also **fade with age**, on a half-life in weeks, and a convincing result
fades slower: `recency_half_life_good = 8.0`, `recency_half_life_bad = 6.0`,
split at the board's median conviction measured WITHOUT its own recency component
so the half-life is not a function of the age it is about to judge. `w_recency`
is 1.20, not the old 0.20: at 0.20 age moved only 4.3% of the conviction range,
so a week-1 result and a week-12 result cost nearly the same to override. At 1.20
age is 23.8% of the range and a ten-week-old result costs 16.3% less to set aside.
Note the live tension: this makes an early signature win (Oklahoma State over
Oregon, week 2) cheaper to override as the season runs.

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
  It builds from `make_golden.FROZEN`, **not** from `config/ranking.toml`, so the
  owner can retune weights from the GitHub web UI without reddening CI. Changing
  FROZEN means the engine moved; changing the config does not. Do not "simplify"
  it back to reading the live config.
- A push touching `config/ranking.toml` triggers the Rank workflow with
  `--force`, so a weight edit publishes itself. `docs/data` is pre-computed
  output: without that trigger a config change is invisible until the next cron.
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
