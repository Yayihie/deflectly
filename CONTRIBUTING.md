# Contributing

Small, focused pull requests are welcome. This repo is a demo-grade product, so the bar is
"does it work and can I verify it", not "does it scale to a million tenants".

## Setup

```sh
pip install -r requirements.txt pytest
cd app && python3 app.py        # demo at http://127.0.0.1:8451
```

Run the demo: `cd app && python3 app.py` (http://127.0.0.1:8451).

Run the tests: `pytest -q` from the repo root.

## Before you open a PR

1. `pytest -q` passes (12 tests).
2. If you changed anything that touches session state, models, or workers, also run:

   ```sh
   PORT=8451 gunicorn app.app:app --bind 0.0.0.0:8451 --workers 2 --timeout 120
   python3 scripts/smoke.py http://127.0.0.1:8451
   ```

   Multi-worker state bugs do not show up under the single-process dev server — three of the
   repo's regressions were only reproducible that way.
3. Add a test for the bug you fixed, and check it fails without your fix. A regression test that
   passes against the old code is not a regression test.

## Things that would genuinely help

- A real helpdesk sync in `POST /api/import` (Intercom / Help Scout / Zendesk) instead of the skeleton.
- Better retrieval: the current scorer is three fused TF-IDF channels. Embeddings or a small
  cross-encoder reranker are plausible; the eval harness already measures whether a change wins.
- Postgres/SQLAlchemy storage so sessions survive container restarts.
- Widget tests (there are none for `app/static/widget.js`).

## Conventions

- Keep the retrieval path dependency-light: no LLM calls, no API keys, no GPU.
- Never claim a metric in the README that `scripts/benchmark.py` does not reproduce.
- No customer data in tests or fixtures. The sample corpus is fictional ("Nimbus HQ").
