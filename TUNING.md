# Tuning the ranking

> **When does the site update?** Sunday morning, automatically, after Saturday's
> games — and **once**. A week becomes the current ranking only when *every* one
> of its games is final, so a stray midweek fixture cannot publish a near-empty
> week; and once a week is published it is **frozen**, so it never quietly
> changes underneath you afterwards. (It used to: week 5 of 2026 was republished
> five times with different numbers, purely because each run refetched betting
> lines.) Editing `config/ranking.toml` still publishes immediately, whatever day
> it is — that is the one thing allowed to rebuild a finished week.

Every number that decides this ranking lives in one file: **[`config/ranking.toml`](config/ranking.toml)**.
Edit it on GitHub and commit. **The site rebuilds itself within a few minutes** —
a push that touches that file triggers the Rank workflow, which regenerates
`docs/data` from the live API and publishes it.

Nothing is hard-coded anywhere else, and editing weights will not break the
build: the golden regression test runs on its own frozen weights
(`scripts/make_golden.py`), so it catches engine changes rather than your dial
settings.

This page is the plain-language index of what each knob does and which way it
pushes. The file itself carries the longer reasoning next to each setting.

```bash
# try a change without committing anything
python3 -m cfbrank --offline --fixture-year 2026 --year 2026 --print-top 25 --dry-run \
  --set stage1.w_sor=0.7 --set stage1.w_perf=0.1
```

---

## The three numbers (`[stage1]`)

The headline weights. **They should sum to 1.0.**

| knob | now | what it means | turn it up to… |
| --- | --- | --- | --- |
| `w_sor` | **0.55** | The résumé — who you beat and how hard that was | reward what teams have achieved |
| `w_market` | **0.30** | Vegas's power ranking — how good the money thinks you are | trust the market's read |
| `w_perf` | **0.15** | The game tape — how well you actually played per snap | reward teams that look good on film |
| `w_adjust` | **0.5** | How loudly the résumé adjustment below speaks, as a multiplier on the whole of `[stage2]` | let losses, best wins and covering matter more |
| `w_fpi` | 0.0 | ESPN's FPI | *leave at 0 — see below* |
| `w_sos` | 0.0 | ESPN's Strength of Schedule | *leave at 0 — see below* |

Two are off on purpose. `w_fpi` and `w_sos` come from an ESPN feed with no week
stamp, so they can't be tested honestly, and `w_sos` double-counts a schedule the
résumé already prices.

Also here: `pool_size` (40) is how many teams are eligible for head-to-head
reordering; `output_size` (25) is how many get published.

---

## How the résumé is scored (`[resume]`)

| knob | now | what it means |
| --- | --- | --- |
| `reference_place` | 25 | Who you're measured against. "Could a **top-25** team have done this?" Lower = tougher yardstick |
| `margin_sigma` | 13.5 | The spread → win-chance curve. **Sensitive — don't change casually.** Lower makes favourites look more certain and softens every résumé |
| `min_games` | 3 | Below this, no résumé score; the other weights rescale |

---

### `w_adjust` — one dial for the whole adjustment

`[stage2]` has four weights. `w_adjust` multiplies all of them at once, so they
keep their proportions to each other without being edited in step.

| `w_adjust` | adjustment range (2026 wk 5) |
| --- | --- |
| 1.0 | −16.62 … +2.54 |
| **0.5** ← now | −8.31 … +2.07 |
| 0.0 | off; teams sit where the three numbers put them |

**What it does not do:** scaling the stage scales it for everyone, so a team that
leads the field on this stage still leads it. Northwestern — whose cover credit on
four games is what prompted this dial — moved exactly one place when it went to
0.5. Use `w_adjust` for "this whole stage is too loud"; it is not a fix for one
team being flattered.

## Vegas's rating (`[market]`)

### Does an easy schedule inflate the Mkt rating?

No, and it is worth knowing why, because the answer is not obvious from outside.
The ratings are **solved as a system**, not averaged:

```
expected margin = rating[you] − rating[opponent] + home field
```

The opponent's rating is *subtracted*. Being favoured by 25 over a team rated −20
implies you are **+5**, not +25 — schedule strength divides out by construction.

Measured on 2026 week 5, the correlation between a team's Mkt rating and the mean
rating of its opponents is **+0.43**: teams with *harder* schedules rate *higher*,
the opposite of inflation. The easiest schedules in the country belong to North
Dakota State (#64), Liberty (#89) and UMass (#125).

The real limit is precision, not bias: a team that plays only weak opponents has
its rating pinned by games with 40-point spreads, which are less precise estimates
than 3-point ones. `min_games` guards the worst of it.



| knob | now | what it means |
| --- | --- | --- |
| `fit_home_field` | true | Measure home-field advantage from the lines (comes out ≈ +2.5) instead of assuming it |
| `home_field_points` | 2.5 | The fallback if fitting is off |
| `horizon_weeks` | 1 | Also read **next** week's lines, so the rating is current rather than a stale average. **2 is rejected** — that line knows results this ranking isn't allowed to see |
| `recency_half_life` | 0.0 | Off. Weighting recent lines more heavily was measured and didn't help |
| `min_games` | 3 | Below this, no market rating |

---

## The game tape (`[performance]`)

| knob | now | what it means |
| --- | --- | --- |
| `fit_home_edge` | true | Measure the home bump in PPA from the data |
| `min_games` | 3 | Below this, no PPA rating |

---

## Adjustments after the three numbers (`[stage2]`)

Rank points, applied after the base order. **Positive = worse.**

| knob | now | what it means | turn it up to… |
| --- | --- | --- | --- |
| `w_cover` | **3.5** | Performance against the posted line — did you look like you meant it | punish teams that don't beat the number |
| `w_loss_quality` | **2.5** | How bad your losses look: margin, venue, and who beat you. **Positive only** — see below | punish ugly losses |
| `w_best_win` | **2.5** | Credit for the best team you beat, **if they're top 25**. See below | reward one big scalp |
| `best_win_place` | 25 | How good an opponent has to be to count at all |  |
| `w_game_control` | 0.5 | Did you lead comfortably or survive | reward wire-to-wire wins |
| `cover_game_cap` | 0.0 | How far one game may move your cover average. **Off** — measured and switched off, see below |  |
| `fit_cover_venue` | true | Measure the market's home-field bias and centre each game on it | *leave on* |

`w_cover` and `w_best_win` are the two that carry the stage now. Above ~6,
`w_cover` starts floating 3-1 teams with soft schedules into the top ten, because
it measures *exceeding expectations* rather than *being good*.

### Best win: rating points past the top-25 bar, not places

This term asks one question — **did you beat anybody really good?** — and it used
to answer it badly.

It was the opponent's *rank*, z-scored over all 138 teams. Rank is not linear in
quality: #1 to #10 is a chasm, #100 to #110 is nothing. With 20 teams pinned at
the no-good-win sentinel the population had mean 88 and sd 44, so the entire
meaningful range lived inside one standard deviation. **Texas beating Ohio State
(#6) outscored Notre Dame beating Wisconsin (#16) by 0.28 rank points** — half of
one place of SoR rank. Using the opponent's base score instead is no better
(0.33), because that is itself a weighted sum of ranks.

Two separate questions, and it helps to keep them apart:

**Who counts?** The top `best_win_place` of **our own Base order** — the Base
column on the site, the three numbers before any adjustment. Not the top 25 by
market rating (a different set), and not the AP poll (which this ranking never
reads). The Base order rather than the published one because the published order
is circular: head-to-head reorders using these very credits.

**How much is it worth?** **Market rating points past the weakest team inside the
bar**, then scaled per standard deviation like the other three — so `w_best_win`
means the same thing here as `w_cover` does. Beating the 25th-best team is worth
~0, so there is no cliff at the bar, and beating nobody inside it is 0: the worst
outcome on this term, not an average one.

On 2026 week 5: Texas over Ohio State **−4.55**, Ole Miss over LSU **−2.87**,
Oregon over USC **−2.68**, Missouri over Florida **−2.48**, Florida over Ole Miss
**−2.19**, Alabama over Mississippi State **−0.87**, Notre Dame over Wisconsin
**−0.51**.

The spread is taken over **the teams that have a qualifying win**, not the whole
board, and that choice is load-bearing. Over all 138 the spread is set by the 127
zeros, collapses, and inflated Texas's single win to **−9.27** — more than the
rest of the adjustment put together. Scoping it to the qualifiers is also stable:
the maximum credit measures 4.55 / 4.86 / 5.04 / 4.56 / 4.96 rank points across
five different weeks of two seasons, while the qualifier count triples from 11 to
31.

Worth knowing before you turn the weight up: **15 of the published top 25 score 0
here**, including Georgia — they beat Oklahoma, who are rated +18.8 but sit 33rd
on the Base order, so that win does not qualify. The term is silent about more than
half the board by design.

### The posted line under-prices home field

A natural assumption about this term is that it is venue-neutral: the line
already contains home field, so beating the number on the road should be the same
achievement as beating it at home. **It is not.** Measured on completed
non-neutral games with a line:

| | games | home teams beat the number by | home covered | road covered |
| --- | --- | --- | --- | --- |
| 2025 | 870 | **+1.045** (SE 0.511) | 50.9% | 47.1% |
| 2026 | 384 | **+1.640** (SE 0.762) | 54.7% | 44.0% |

Same direction in both seasons at about two standard errors each. Left alone that
is a standing penalty on a road-heavy schedule which says nothing about the team,
so `fit_cover_venue` measures it from the season's own lines each week and
centres every game on its venue's mean — the same approach as
`market.fit_home_field`, and for the same reason. Each team's panel shows both
numbers: what happened, and what was scored.

### Why `cover_game_cap` is off

Capping how much one game can move the average sounds obviously right — Ole Miss
lost at Florida by 20 against the number and that one game took their average
from −1.73 to −6.31. **But a cap moves nobody**, because this term is z-scored:
clipping compresses the whole board, so every team's absolute number improves and
its standing among 138 teams survives. Ole Miss came out 15th at every cap from 10
to none.

The cost, on the other hand, is real. The per-game margin has a standard deviation
of **14.97 points**, so a cap bites far more often than it looks:

| cap | games clipped | spread of team means |
| --- | --- | --- |
| 10 | 48.7% | 5.83 |
| 14 | 34.9% | 7.39 |
| 21 | 14.4% | 9.13 |
| **0 (off)** | — | **10.36** |

A cap at 14 throws away 29% of the spread of a term built to discriminate, and
buys nothing. The one real effect is incidental: it clips Northwestern's outsized
*credit* and drops them a couple of places, which is the small-sample flattery
problem from another angle. If that's ever worth chasing, this is the knob.

### A loss can cost you, never pay you

`w_loss_quality` is the one term on a **one-sided** scale, and that is deliberate.
**The best a loss can do is not hurt you.**

| what happened | loss quality |
| --- | --- |
| no losses | **0** — and 0 is the best score available |
| a near-perfect loss (a point on the road to the best team in the country) | **~0** |
| an average loss | **+3.95** |
| the ugliest loss on the board | **+8.55** |

It used to be graded on a curve against *the teams that had lost*, and because
the average loss is ugly, a tidy one scored below that average and came out
negative — a credit. An unbeaten team's 0 was then the **worst** score on the
component: on the finished 2025 board, a 16-0 Indiana placed last in the top 25
on loss quality while Notre Dame banked 7.22 rank points for losing well.

Turning `w_loss_quality` up now makes losses cost more, full stop. It cannot make
a loss profitable, whatever you set it to — and 0 switches the term off rather
than flattening it into a reward.

---

## Big upsets (`[stage3]`)

When a result spans a wide gap, both teams get pulled toward their midpoint — the
upset says the winner was underrated *and* the loser overrated.

| knob | now | what it means |
| --- | --- | --- |
| `gap` | 15 | Places between the two teams needed to trigger it. **Lower = fires more often** |
| `strength` | 0.5 | How far toward the midpoint. **Do not set 1.0** — it oscillates |
| `enabled` | true | Off switch |

---

## Head-to-head (`[stage4]`)

The published order is the one that minimises
`strength × (results it contradicts) + drift × (how far teams move)`.

### How much should beating someone matter?

`stage4.strength` is that dial, and it is probably the one you'll reach for most.

| strength | H2H honoured | teams moved | biggest move |
| --- | --- | --- | --- |
| 14 | 26/27 | 21 | 10 places |
| **4** ← now | 24/27 | 15 | 3 places |
| 2 | 22/27 | 5 | 2 places |
| 0 (off) | 20/27 | 0 | 0 |

Two things worth knowing before you turn it:

- **With it off entirely, 20 of 27 results are still honoured** — the résumé
  usually already agrees. This stage only argues about a handful of games.
- **The résumé already prices beating a good team.** It's a list of games with win
  probabilities, so a win over a strong opponent counts there first. Swap Ole
  Miss's win over LSU for a cupcake at the same record and their résumé goes from
  30.2% to 63.1% — the win is worth 2.1x before this stage touches anything.
  Turning `strength` up means paying for the same result twice, which is what made
  the ranking feel over-constrained at 14.

Useful settings: **2** makes it a tiebreak between teams already close together.
**0** switches it off, and every contradiction is still *reported* — the ranking
just stops reordering to fix them.

| knob | now | what it means |
| --- | --- | --- |
| `strength` | **4.0** | How many places one unit of conviction buys. See above |
| `drift_weight` | 1.0 | Cost per place moved from the starting order |
| `drift_exponent` | 1.5 | Above 1, long moves cost disproportionately. This is what stops one result flinging a team across the board |
| `split_series` | most_recent | How a rematch that flips the result is handled |

### How convincing was a win (`[stage4.evidence]`)

| knob | now | what it means |
| --- | --- | --- |
| `w_margin` | 0.50 | Margin, venue-adjusted and capped. The biggest single signal |
| `w_rating_gap` | 0.30 | Did the result agree with the power ratings |
| `w_recency` | 1.20 | How much age matters. At 0.20 this was effectively off |
| `w_common_opponents` | 0.15 | How the two fared against shared opponents |
| `margin_cap` | 28 | Beyond this, running up the score stops helping |
| `recency_half_life_good` | 8.0 | Weeks for a convincing result to fade by half |
| `recency_half_life_bad` | 6.0 | Weeks for a weak one. Must be ≤ the "good" value |
| `recency_floor` | 0.15 | What results fade *toward* — they never stop counting entirely |
| `weight_floor` | 1.0 | The cheapest possible override still costs this much |
| `conviction_floor` | −2.0 | An absolute floor, so the weakest result isn't free to ignore |

---

## Which numbers are known, and which are opinion

Worth keeping straight before you turn anything.

**Measured** — `w_market` vs `w_perf` ratio, `w_cover`, `margin_sigma`,
`horizon_weeks`, home-field values, and that market recency weighting doesn't help.

**Judgement, and no test can settle it** — `w_sor` above all. Predicting games
isn't what a résumé is *for*. Same for `reference_place`, the stage-2 weights,
`gap`, `strength` and `drift_exponent`.

## Checking a change

```bash
python3 -m unittest discover -s tests -t .          # must pass
python3 scripts/evaluate.py --year 2025 --offline   # does it predict better or worse?
```

`evaluate.py` ranks through several weeks of a finished season and predicts every
later game. It prints the AP poll on the same games as a reference — **not a
target**. On `--weeks 4,6,8,10,12`: AP gets **58.7%**, this ranking **65.4%**.
On the default weeks: AP **56.5%**, this ranking **63.4%**. Either way about
seven points ahead.

Those are measured at the weights shipped today. **Re-run the tool rather than
quoting them** — change the weights and the number changes, which is rather the
point of the tool. AP's figure does not move, so if yours disagrees with the
number above, check you are on the same `--weeks`.

`tests/fixtures/golden/rankings_2025.json` is a byte-for-byte copy of the 2025
output, used to catch accidental changes: 2025 is finished, so if the output
moves, the *code* moved. It is built from the frozen weights in
`scripts/make_golden.py`, **not** from your settings — so retuning never breaks
it. An actual engine change does, and that is the point: read the diff, then
`python3 scripts/make_golden.py`.
