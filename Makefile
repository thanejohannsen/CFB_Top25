.PHONY: test rank rank-live serve fixtures lint

test:
	python3 -m unittest discover -s tests -t . -v

rank:
	python3 -m cfbrank --offline --fixture-year 2025 --print-top 25 --dry-run

rank-live:
	python3 -m cfbrank --print-top 25

serve:
	python3 scripts/serve_docs.py

fixtures:
	python3 scripts/refresh_fixtures.py --year 2025
	python3 scripts/refresh_fixtures.py --year 2026

lint:
	python3 -m compileall -q cfbrank scripts tests
	node --check docs/assets/app.js
