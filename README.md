# CFB Top 25

**Live site: <https://thanejohannsen.github.io/CFB_Top25/>**

A college football Top 25 built from an explicit algorithm rather than opinion,
published as a static GitHub Pages site where **every placement opens to show
exactly why it is there**.

```
 1. Indiana        base #1       16-0   SoR 1   Mkt +25.4   PPA #2
 2. Oregon         base #3   +1  13-2   SoR 2   Mkt +23.6   PPA #5
 3. Miami          base #2   -1  13-3   SoR 3   Mkt +20.3   PPA #6
 4. Ohio State     base #4       12-2   SoR 4   Mkt +27.4   PPA #4
 5. Ole Miss       base #5       13-2   SoR 6   Mkt +17.3   PPA #14

100 of 109 head-to-head results honoured | 2 contradiction loops, largest 26
```

## How it ranks

1. **The record, the market and the play-by-play** —
   `0.50 × StrengthOfRecord_rank + 0.25 × Market_rank + 0.25 × PPA_rank`. All three
   are national ranks where 1 is best, so lower is better.

   - **Strength of Record** is the resume: how impressive your record is given who
     you played. Half the ranking, because half of what a ranking is for is
     honouring what teams have actually done.
   - **Market rating** is the neutral-field rating implied by betting lines. A
     spread prices a *matchup*, so it is decomposed into one number per team:
     `expected home margin = rating(home) − rating(away) + home field`. The output
     is in points and reads directly — +19.5 beats +15.5 by four on neutral ground.
     Home field is measured from the lines, not assumed, and lands near +2.4.
   - **PPA** is points added per play on offence less points allowed on defence,
     garbage time stripped, adjusted for opponent strength. The eye test, counted.

   They know different things, and the pair beats either alone. Ranking 2025 only
   on what was knowable at the time and predicting every later game: market alone
   61.2% of ranked-vs-ranked games, play-by-play alone 62.8%, the two together
   67.3%.
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
   Conviction blends location-adjusted margin, the power-rating gap, recency and
   common opponents. A one-point home win scores *negative*: home field alone is
   worth more than the margin. Overriding *any* result costs a real amount — the
   cheapest one on the board still prices at a full unit of conviction.
5. **Contradiction reporting** — loops (A beat B, B beat C, C beat A) are found with
   Tarjan's algorithm and published, along with every overridden result.

### Why FPI is not in the formula

It was the quality term, and two things pushed it out. The market and the
play-by-play do its job better. And FPI **cannot be honestly checked**:
`/ratings/fpi` has no week parameter, so it serves one snapshot — on a completed
season, the finished year's answer. Ranking 2025 "through week 4" with it put
Indiana first because its *stored* Strength of Record is 1, earned in January. An
earlier version of this README quoted 75.5% accuracy from precisely that mistake.

FPI still breaks ties, and still helps weigh which head-to-head result to set
aside when the market has no line for either team. It just does not order the
board.

### Why Strength of Schedule is not in the formula

It was, at a quarter of the weight, and it was the single biggest source of bad
rankings. Strength of Record **already** accounts for the schedule — it means "how
good is your record *given who you played*". Weighting schedule again counts it
twice and rewards playing hard games whether or not you win them.

A 3-2 Clemson sat at 16th on schedule alone; a 5-0 Georgia with the country's #2
rating sat at 23rd for playing an easy one.

Schedule strength is still published for every team and the weight is still a knob
in `config/ranking.toml`. It just no longer moves the ranking.

### Checking it by what it predicts

```bash
python3 scripts/evaluate.py --year 2025           # rank through weeks, predict every later game
python3 scripts/evaluate.py --year 2025 --grid    # sweep the market:play-by-play ratio
```

The test is prediction, not agreement: take the ranking as it stood after week N,
predict the winner of every game after it, count how often that is right. That is
the one test of a ranking that cannot be gamed by copying somebody.

On identical games from 2025 — the ones between two AP-ranked teams — this ranking
got **62.1%** and the AP poll **57.6%**. AP is printed as a reference row and is
never a target; it is the worst number the tool produces. The poll had Miami 18th
in week 11 of 2025 and they finished 2nd.

The tool **zeroes Strength of Record, Strength of Schedule and FPI by default**,
because all three come from that one undated snapshot and scoring them on a
finished season is reading the answer key. `--allow-lookahead` puts them back and
labels its own output as contaminated.

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
  engine/      base_score, market, performance, adjust, resume, regression,
               h2h, evidence, order, graph, pipeline
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

Everything comes from [CollegeFootballData.com](https://collegefootballdata.com):
games, records and the calendar; betting lines; per-game play-by-play PPA; and
ESPN's FPI, Strength of Record and Strength of Schedule. A full run makes six API
calls, cached for six hours.

Three consequences worth knowing:

- Those ESPN ratings are third-party opinions that move during the week, and the
  endpoint has no per-week parameter — so a week's snapshot is taken when it is
  generated and earlier weeks cannot be reconstructed after the fact. Lines and
  PPA are stamped per game, so those *can* be reconstructed, which is the only
  reason the backtesting is honest.
- The market rating reads lines for games already played **and for the coming
  week**, which is what makes it a current opinion rather than an average of stale
  pre-game guesses. A closing line for next Saturday has priced in every result so
  far and nothing after it. Two weeks out is rejected by config validation.
- A spread knows about injuries, suspensions and weather that no box score shows.
  That is why it predicts well, and it is also a real departure from a pure resume
  ranking.

## Known limitations

These are stated on the site's methodology page too, rather than buried here:

- The market rating knows things results cannot — injuries, suspensions, weather.
  A team can be rated highly here for reasons that never appear in a box score.
- A team with too few lined games gets no market rating at all and is scored on the
  terms it has, with the weights rescaled; its row says which input is missing.
  Every FBS-vs-FBS game in both seasons checked had a line, so this mostly affects
  the opening weeks.
- Early in a season the schedule can split into groups that have not played each
  other. Ratings only compare inside such a group, and the snapshot's warnings say
  so when it happens.
- Half the weight on Strength of Record is a judgement, not a measurement:
  predicting games is not what a resume is for. The cost is visible — a 7-6 Penn
  State lands 24th on the 2025 board.
- The ordering is found by local search: a strong local optimum, not a proven global
  one. It reaches the same answer from any input ordering in practice, and the test
  suite checks that, but it is an observation rather than a guarantee.
- A chain of individually reasonable moves can still add up to a large one. Per-team
  drift is published so this is visible rather than hidden.
- Early in a season Strength of Record is volatile, and an unbeaten team with a soft
  schedule can look better than it is.

## Licence

MIT — see [LICENSE](LICENSE).
