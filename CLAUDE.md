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

`base = 0.75 x SoR_rank + 0.25 x FPI_rank`. **`stage1.w_sos` ships at 0 on
purpose** — do not "restore" it. Strength of Record already accounts for the
schedule, so weighting Strength of Schedule again double-counts it and rewards
playing hard games regardless of the result. With it at 0.25 a 3-2 team sat at
#16 and a 5-0 team with the #2 FPI sat at #23. `config/ranking.toml` carries the
long version, and `tests/test_config.py` pins the zero.

Any weight change must be justified with `python3 scripts/evaluate.py`, which
grades the ranking against the AP poll (overlap, mean rank gap, Kendall tau).
Treat AP as a yardstick, not ground truth.

`TeamBase.formula()` renders only non-zero terms, so a weight that is off never
appears in the published string. Every caller must pass all three weights or the
rendered formula will not match `raw_score`; `tests/test_pipeline.py` asserts it
adds up.

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
- Run `python3 -m unittest discover -s tests -t .` before pushing. Everything runs
  offline against checked-in fixtures.
