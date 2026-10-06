# Tuning the ranking

Every number that decides this ranking lives in one file: **[`config/ranking.toml`](config/ranking.toml)**.
Edit it on GitHub, commit, and the next scheduled run uses your values. Nothing is
hard-coded anywhere else.

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
| `w_sor` | **0.60** | The résumé — who you beat and how hard that was | reward what teams have achieved |
| `w_market` | **0.25** | Vegas's power ranking — how good the money thinks you are | trust the market's read |
| `w_perf` | **0.15** | The game tape — how well you actually played per snap | reward teams that look good on film |
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

## Vegas's rating (`[market]`)

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
| `w_cover` | **4.0** | Performance against the posted line — did you look like you meant it | punish teams that don't beat the number |
| `w_loss_quality` | 3.0 | How bad your losses look: margin, venue, and who beat you | punish ugly losses |
| `w_best_win` | 1.0 | Credit for the best team you beat | reward one big scalp |
| `w_game_control` | 0.5 | Did you lead comfortably or survive | reward wire-to-wire wins |

`w_cover` is currently the most consequential of the four. Above ~6 it starts
floating 3-1 teams with soft schedules into the top ten, because it measures
*exceeding expectations* rather than *being good*.

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

| knob | now | what it means |
| --- | --- | --- |
| `strength` | 14.0 | How many places one unit of conviction buys. **Up = head-to-head wins more arguments** |
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
target**. AP gets 58.7%; this ranking gets 67.0%.

Any engine change moves `tests/fixtures/golden/rankings_2025.json`. That's
intended — read the diff, then `python3 scripts/make_golden.py`.
