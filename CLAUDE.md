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
