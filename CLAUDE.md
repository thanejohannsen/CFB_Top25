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
order = argmin  strength x (results contradicted)
               + drift   x (places moved) ^ drift_exponent
               + gap     x (places past what the grounds licence) ^ gap_exponent
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
target. It also loses, by a lot: **65.4% to 58.7%** on the same 179 games from
2025, measured on `--weeks 4,6,8,10,12`. On the default `--weeks 5,7,9,11,13`
it is **63.4% to 56.5%** over 161 games: the same ~7-point edge, different
sample.

**Re-measure before quoting a number.** An earlier version of this file said
67.0%, which was true when it was written and is not now: the shipped config has
moved since (`w_adjust` 0.5, `stage4.strength` 4, weights 0.55/0.30/0.15) and
nobody re-ran the tool. AP's 58.7% is config-independent and reproduces exactly,
which is the cheap way to confirm you are on the same week set as a quoted
figure. The sweeps recorded further down this file are dated records of what was
measured at the time, not claims about today's absolute level.

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
remaining candidate is a cap on the cover component.

**`cover.SHRINKAGE_GAMES` is NOT inert, and an earlier version of this file said
it was.** I claimed the z-score normalises it straight back out. That is only
true if every team has the same number of lined games. It is a per-team factor
`n/(n+4)`, and on 2026 week 5 the board splits 31 teams on 4 games (0.500), 105
on 5 (0.556) and 2 on 6 (0.600). A z-score undoes ONE global affine transform,
not 138 different ones, so the shrinkage does real differential work: it costs
Northwestern 0.38 rank points and drops them from 2nd to 5th on the cover
leaderboard. It is a live lever on exactly the small-sample problem, which is
why "scoring cover on an absolute scale so sample size counts" is no longer
listed above as a thing still to build -- it is already partly built.

### `loss_quality` is ONE-SIDED, and must stay that way

**A loss may cost rank points. It may never earn them.** `engine/resume.py` scores
it through `stats.penalty_scaler`, not `zscorer`: divide by the board's spread,
do **not** subtract the mean, floor at zero.

It was centred, and that was a real bug the owner caught. The z-score population
is *the teams that have lost*, whose mean badness is ~13.3 because it is dominated
by genuinely ugly losses. So a ranked team's tidy loss landed two standard
deviations below that mean and came out **negative — a credit** — while an
undefeated team's flat `0.0` was the worst score on the component. The evidence:

- 2026 week 5: **60 of 123 teams with a loss were paid for it.** Ohio State's
  one-point road loss was worth **−3.99**, nearly four rank points *in their
  favour* against a team that had not lost at all.
- The published 2025 board: **24 of 25 rows negative**, and the one `0.00` was
  16-0 Indiana — finishing LAST in the top 25 on "loss quality". Notre Dame
  banked −7.22 for losing well.

The fix needed no new weight, because **badness is already anchored at zero**:
across the 123 teams with a loss it runs −0.170 … 28.902 and only one is below
zero, by 0.17. Zero badness already means the best loss available, a one-point
road defeat to the best team in the country. Dropping the centring shifts every
losing team by the same `mean/sd x w` and leaves the spread identical, so
`w_loss_quality` stays 3.0 and only the anchor moves: no loss **0**, a perfect
loss **~0**, an average loss **+3.95**, the worst on the board **+8.55**.

Two things checked before shipping it, both worth not redoing:

- **The divisor is stable enough to carry the absolute level**, which it now does
  rather than only ordering losses. 2025: `pstdev` 5.87 (wk 3) -> 5.01 (wk 15),
  so an average loss costs 3.57 -> 4.16 across a whole season. No case for
  hard-coding a scale.
- **It reordered nothing on the completed 2025 season** — published order and
  every count byte-identical. The shift is near-uniform among teams with losses,
  so it changes what the component *means* without churning a finished board.

**What it cost to predict, measured both ways.** This is a values fix, so a cost
would have been acceptable; it is near zero on the sample big enough to read.

| | before | after |
| --- | --- | --- |
| all FBS, `--weeks 4,6,8,10,12` (1,997 games) | 67.8% | **67.4%** |
| all FBS, default weeks (1,726 games) | 68.1% | **68.0%** |
| AP-vs-AP, `--weeks 4,6,8,10,12` (179) | 65.4% | **62.0%** |
| AP-vs-AP, default weeks (161) | 63.4% | **63.4%** |

Read the all-FBS rows: −0.4 and −0.1 points on ~1,700-2,000 games. The AP-vs-AP
rows disagree with each other (−3.4 and 0.0) on 161-179 games, which is what a
sample that small does. Do not quote the −3.4 as the cost of this change.

**The general rule this is an instance of:** a component with a hard `0.0` for
"this team has no value here" must not sit on a centred scale, because `z = 0` is
"average", not "nothing". `best_win` and `game_control` avoid it by giving a
team with nothing the `worst = n + 20` sentinel instead, which is a genuine
continuum. `cover` has the same `0.0` branch but it is dead code (every rateable
team has a line) and `cover` is two-sided on purpose — covering is meant to pay.

### `best_win` is RATING points past a bar, not a rank. Do not put the rank back

The owner asked what Notre Dame's best win was and how it could be anywhere near
Texas beating Ohio State. It was a fair question: the answer was **0.28 rank
points**, half of ONE place of SoR rank.

**A rank cannot carry this credit.** Rank is not linear in quality — #1 to #10 is
a chasm, #100 to #110 is nothing — and z-scored over all 138 teams with 20 pinned
at the no-good-win sentinel, the population had mean 88.12 and sd 43.96. Beating
#6 and beating #16 differed by 0.25 sd. The whole meaningful range, beating #6
through beating #46, fitted inside 0.91 sd. Using the opponent's `base_raw`
instead is no better (0.33 points) because that is itself a weighted sum of
*ranks* and inherits the same non-linearity. By market rating the same pair differ
by **1.58**.

**And WHO counts is a separate question from how much it is worth.** Eligibility
is the top `best_win_place` of the **base order** (`raw_rank`, the Base column on
the site); the credit is then **market rating points** within that set. Gating on
the top 25 BY RATING instead was a bug the owner caught: Mississippi State are
12th on the base order and 14th published but only 31st by rating, so a rating
gate denied Alabama any credit for beating them. It is not the AP poll either --
this ranking never reads one. The base order and not the published order because
the published order is circular: stage 4 reorders using these credits.

So: the best market rating beaten among base-top-25 opponents, minus the weakest
rating inside that set, floored at zero.

- **Anchored, not centred**, for the same reason as `loss_quality`: 0 means no
  qualifying win, and 0 must be the FLOOR of a credit. Centring would hand a
  PENALTY to whichever team's best scalp happened to be the weakest qualifying
  one — worse than beating nobody good.
- **Scale per standard deviation over THE QUALIFIERS, not the whole board.** Two
  wrong answers were tried first. `penalty_scaler` over all 138 is wrong: only 11
  qualify, so the spread is set by the 127 zeros, collapses to 2.31, and inflated
  Texas's single win to **−9.27 rank points** — more than the rest of the
  adjustment put together — while the #1 team scored nothing. Normalising to a
  0–1 range fixed the blow-up but silently changed the UNIT to "rank points,
  maximum, ever", so a weight of 2.5 delivered 1.25 here against 3.89 for the same
  number on `loss_quality`. **The owner caught that too** — "what happened to the
  2.5 weight?" Scoping the spread to the qualifiers restores "rank points per
  standard deviation" and is stable: max credit measures 4.55 / 4.86 / 5.04 /
  4.56 / 4.96 across five weeks of two seasons while the qualifier count triples.
- **The bar means 15 of the published top 25 score 0**, Georgia included: they
  beat Oklahoma, rated +18.8 but 33rd on the base order, so it does not qualify.
  That is the owner's explicit choice — "0 pts if they have no best win" — but the
  term is silent about more than half the board, so read it before raising
  `w_best_win`.
- **Take the MINIMUM rating in the eligible set as the floor, not the
  `place`-th rating.** Rank and rating disagree, so the lowest-rated team in the
  base top 25 is not always the 25th one.

### A GLOBAL transform on a z-scored term is invisible. Check before building one

This has now bitten three times in one session, so it is a rule rather than an
anecdote. These terms are z-scored across the board, so **anything that moves
every team the same way is normalised straight back out.** Only the *differential*
part survives.

- `cover.SHRINKAGE_GAMES` DOES work, but only because `n/(n+4)` is a per-team
  factor (teams have different game counts). I first called it inert, which was
  wrong for that reason.
- `cover.GAME_CAP` does NOT work. Clipping compresses the whole board, so every
  absolute number improves and every standing survives. Ole Miss came out 15th at
  every cap from 10 to none. Built, measured, shipped off.
- `loss_quality`'s centring DID matter, because it moved the *anchor* rather than
  the scale: an undefeated team's hard `0.0` does not shift with the mean, so
  subtracting a mean changed where zero sat relative to everybody.

The test: does the change alter the *ordering* of the raw quantity, or the ratio
of one team's value to another's? If not, the z-score will eat it. Measure the
spread of team means before and after, not just one team's number.

### The posted line under-prices home field, and the cover term corrects for it

`cover.venue_bias` measures it from the season's own lines; `fit_cover_venue`
shipped on. Measured on completed non-neutral lined games, home teams beat the
number by **+1.045** in 2025 (n=870, SE 0.511) and **+1.640** in 2026 (n=384, SE
0.762) — same direction both seasons at about two standard errors each, and in
2026 home teams covered 54.7% against road teams' 44.0%.

That is counter-intuitive enough to be worth stating plainly: the line already
contains home field, so this term *looks* venue-neutral and is not. Uncorrected it
is a standing penalty on a road-heavy schedule that says nothing about the team.
Measured rather than assumed, for the same reason `market.fit_home_field` is.

`VENUE_MIN_GAMES = 50` is load-bearing: fitted on one game the bias EQUALS that
game's cover margin, so subtracting it zeroes the result. The suite caught that.

Two things it is not. It is **not** the fix for a team held down by one blowout —
Ole Miss are home-heavy (2 home, 1 away, 1 neutral through week 5), so the
correction nets them −0.41 and makes them slightly *worse*. And a big favourite
failing to cover is **not** a real effect: checked by spread bucket across both
seasons, there is no monotone pattern and the seasons disagree bucket by bucket.
Beating a 44-point line is no harder than beating a 4-point one.

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
to being overridden, because the forcer no longer drags LSU down to meet it.
Honouring that one result was what the extra constraint bought. **The grounds
stage has since narrowed it rather than restored it** -- Ole Miss #12, LSU #7
where it was #15 and #7 -- and for a reason that reads correctly: Ole Miss have
lost since (at Florida) and LSU have not, so the override is licensed to 8 places
and they finish 5 apart. That is the gap limit doing its job, not `strength`
quietly going back up.

Do not add a signature-win bonus to the resume to compensate. That was built,
measured and removed once already for the same double-counting reason.

### Overriding a result is NOT binary -- the gap it buys is licensed

`engine/grounds.py`, `[stage4.grounds]`. The owner's words: "right now its binary
-> you over rule and then the teams can be like 10 spots apart."

That was exactly right, and the published board proved it. Stage 4 priced the
DECISION to rank against a result (`strength x weight`) and then let the DISTANCE
be free. On **2026 week 5 the board published Missouri's 45-17 win over Florida
with Florida at #9 and Missouri at #20** -- eleven places, over a 28-point result,
on the Saturday it happened, with nothing in between. Oklahoma State's week-2 win
over Oregon came out fifteen places apart on the same board.

**`evidence.py` and `grounds.py` answer two different questions and must stay
apart.** Conviction is about the game -- margin, venue, rating gap, common
opponents -- and it never changes once the game is played. The grounds are about
what the two teams have DONE since, which is what anybody actually argues about:

- **Form.** The winner has lost since, and lost more often than the team it beat.
  `slide = winner_losses - loss_offset x loser_losses`, floored at zero. The
  offset is 0.5, not 1.0, because the owner's rule has two clauses joined by
  "or": having lost at all is grounds, having lost MORE is stronger grounds. At
  1.0 the first clause disappears.
- **Resume.** The loser has since beaten better teams. **Strengthens with time**,
  by an explicit `ramp_weeks` dial, because that is what the owner asked for and
  because one good win the week after a game proves little.

What they buy is a **licence in places** -- `allowance` -- and stage 4 charges
`gap_weight x (places past it) ^ gap_exponent`. With no grounds the licence is
`base_places = 2`: the two may swap, because the base order is allowed to
disagree about near-neighbours, but they stay near-neighbours.

Six things about it that were measured and should not be re-derived:

- **The resume ground is the MEAN quality of the wins since, not the sum.** The
  sum was built first and it saturates: on the completed 2025 season it reached
  73 rating points for a week-8 result, blew past `max_places` on its own, and
  pinned 15 of 111 results at the cap, so every old result looked equally
  undermined and the term stopped discriminating exactly where the
  contradictions are. A mean is also the plainer reading of "big wins while the
  other has mediocre wins". Growth with time belongs to `ramp`, where it is one
  dial instead of an accident of volume.
- **`gap_weight` does not move predictive accuracy.** Swept 0.5 / 1.0 / 1.5 /
  3.0 / 6.0 on 1,997 all-FBS games of 2025 (`--weeks 4,6,8,10,12`): 67.4 / 67.3
  / 67.2 / 67.3 / 67.4%. A three-game spread across a twelvefold range. It is a
  judgement about what a ranking owes a result, like `w_sor`. It ships at 1.5
  because that takes the widest contradiction on 2026 week 5 from 15 places to 7
  without churning the board; at 3 and above the last place or two is paid for
  in real drift (biggest move 4 -> 6).
- **The whole stage costs about nothing on the big sample and ~2 points on the
  small one.** All-FBS 67.5% -> 67.2% (1,997 games) and 68.4% -> 68.3% (1,726,
  default weeks). AP-vs-AP 64.8% -> 62.6% (179) and 66.5% -> 64.6% (161). Read
  the all-FBS rows; the AP rows agree in direction this time, unlike the
  `loss_quality` change, but 160-180 games carries about +-3.7 points of standard
  error. This is a values fix and a small cost was acceptable.
- **`relief` does almost nothing on its own.** With `gap_weight = 0` the 2025
  backtest reproduces the no-grounds numbers EXACTLY. `stage4.strength` is 4, so
  the override price rarely decides anything by itself. Carried for shape, not
  effect -- do not go looking for its accuracy contribution again.
- **`enabled = false` reproduces the old board exactly**, and that is pinned:
  with it off the 2025 golden's published order and its list of overridden
  results are both identical to the pre-grounds version, as is 2026 week 5.
- **The gap term is NOT incident-local.** Relocating a third team can shift one
  endpoint of a pair and not the other, which changes that pair's gap by one, so
  `move_delta` checks every licensed edge rather than just the moved team's.
  `tests/test_order.py::test_move_delta_matches_full_recompute` licences a random
  subset precisely to catch a delta that forgot this.

**The categories are labels, derived from the licence, never the reverse.** The
owner asked for "categories of severeness" and they are published
(`category`, `severity`) and rendered. But bands that SET the licence would make
it a step function of the evidence, and stage 4 is a local search over a cost
surface: a step is a cliff two teams can straddle, where one more rating point of
subsequent resume jumps the licence four places and the whole board rearranges.
Continuous in, labels out.

**`_credit` sorts before taking the mean**, for the same reason `at_least()` and
`engine/cover.py` sort: summing floats in input order makes the last bits a
function of how the games arrived. `tests/test_grounds.py::TestDeterminism`
shuffles the result list directly.

The two grounds are counted over **every** game, not just pool games, and from
market ratings only. A loss to a team nobody ranks is still a loss; FPI has no
week dimension, so folding it in here would read a season-end snapshot into a
question about what has happened since one particular Saturday.

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
- **A PUBLISHED WEEK IS FROZEN.** `cli.run` refuses to rewrite a week whose
  snapshot file already exists unless `--force` is passed. This is the fix for
  the churn, and the churn was real: week 5 of 2026 was republished five times
  with different numbers and not one new game. Every run refetched `/lines`, and
  five hours of Monday line movement (same 1141 games, different sha256) moved
  the market ratings. `--force` is the way back in, which keeps a config push
  republishing immediately and keeps "delete the file" as the way to withdraw a
  bad week. `tests/test_cli.py` pins both.
- **Lines move BOTH quality terms and the resume, i.e. 85% of the base.**
  `/lines` -> market ratings -> the Mkt term (0.30) *and* the SoR term (0.55),
  because `resume_strength` takes its reference team and every opponent's
  strength from market ratings. On the published week 5 a lines-only refetch
  moved 11 teams' SoR ranks. Combined with a board whose median gap between
  adjacent teams is 0.93 rank points -- 8 of 24 gaps under 0.50, five under 0.25
  -- a one-place input slip (worth 0.55) moves a team several places. When the
  board "shifts for no reason", this is almost always why; check
  `meta.source.endpoints[].sha256` before suspecting the engine.
- **The cron runs Sunday 07:55 CDT, with retries Sunday afternoon and Monday
  morning** (`55 12 * * 0`, `55 19 * * 0`, `55 12 * * 1`). The retries are not
  extra rankings: a run that has nothing new to publish exits 2 and commits
  nothing, so exactly one run a week lands. Without them a single late-logged
  score would cost a full week.
- **`h2h.resolve_week("auto")` advances only once EVERY game in a week is
  final.** It used to take the latest week with ANY completed game, and on
  2026-10-07 one Wednesday fixture promoted the board to week 6 with 57 of 58
  games unplayed. That is not cosmetic: the cutoff drives the market term, so
  advancing dropped 57 unplayed week-6 lines and swapped in week-7 ones --
  ~50 of 329 inputs churned on one game. Half a week was the first fix and was
  not enough, because half a week is still a week in progress. Every week of the
  finished 2025 season reached 100%, all sixteen, so the bar is one real data
  clears. A week that never completes does not strand the board: the resolver
  returns the LATEST settled week, so an abandoned fixture costs that week its
  snapshot and every later week publishes on time. `tests/test_h2h.py` pins it.
- **The straggler escape needs `Dataset.material_teams`.** One abandoned game
  may be written off, but only when neither team is in the last published
  board's 40 AND a later-starting game in the same week is already final. The
  second condition is what stops a week settling at Saturday lunchtime because
  its late kickoffs are late, and it is deliberately clock-free -- "the week has
  moved past this game" is a fact about the data. `material_teams` is read by
  the CLI from the last snapshot and passed in as input, so `rank()` stays pure
  and the test cannot be circular. Empty means wait for every game.
- **`output/history.update_index` drops catalogue entries whose snapshot file is
  gone.** The index drives the site's week selector, so a stale entry is a 404,
  and snapshots do get withdrawn legitimately.
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
