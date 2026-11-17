# Deflectly

**Support AI that trains on your own support tickets in ~0.07 seconds — no LLM, no API key, no GPU, no
per-answer cost.**

[![tests](https://github.com/Yayihie/deflectly/actions/workflows/ci.yml/badge.svg)](https://github.com/Yayihie/deflectly/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9%20%E2%80%93%203.13-blue.svg)](https://www.python.org/)

**[Live demo](https://deflectly-production.up.railway.app)** — paste tickets, train, compare, correct it, embed the widget.

![Deflectly demo: a generic bot scores 0/5 on held-out product questions, the model trained on your tickets scores 5/5](app/static/demo-compare.png)

## The part that is actually interesting

Most support-AI products in 2026 are an LLM wrapper: an API key, a per-token bill, a prompt you can't
audit, and an answer that might invent a refund policy. This repo goes the other way.

Answers come from **retrieval over your own past replies**, built from three fused TF-IDF channels:
word-level over questions, word-level over answers, and a character 3-5-gram matcher for typos and
phrasing. It trains in ~0.07s on a CPU, scores 5/5 on held-out questions where a generic bot scores 0/5,
and costs nothing per answer. Because it only ever returns text your team already wrote, it cannot make
up a policy.

The consequence is the point: **learning is a 30-millisecond index rebuild, not a fine-tuning run.** When
an agent corrects an answer, the next customer gets the correction. That loop is the product, and the
`/teach` step lets you watch it happen.

## Try it

```sh
pip install -r requirements.txt
cd app && python3 app.py            # http://127.0.0.1:8451
```

Or with the exact production entrypoint (two workers, as deployed):

```sh
pip install -r requirements.txt
PORT=8451 gunicorn app.app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

Six steps, about three minutes:

| Step | What happens |
|---|---|
| 1 · Tickets | Paste Q/A pairs from Intercom / Help Scout / Zendesk, upload a file, or load the built-in 56-ticket sample ("Nimbus HQ") |
| 2 · Review | Fix bad pairs; customer PII (emails, phones) is flagged so it can be stripped. Only approved pairs train |
| 3 · Train | Live SSE log of the index build. ~0.07s, on CPU |
| 4 · Compare | 5 unseen questions, generic baseline vs. your model, exact-match and ROUGE-L scored against held-out gold answers |
| 5 · Teach it | Mark an answer wrong, type the right one, watch confidence move on the next ask |
| 6 · Go live | Embed snippet for the widget, whose 👍/👎 corrections feed the same loop |

## Reproduce the numbers

```sh
python3 scripts/benchmark.py
```

```
model                 exact match     ROUGE-L     train time
--------------------------------------------------------------
generic baseline      0/5 (0%)        0.075       0.009s
store-trained         5/5 (100%)      1.000       0.030s

correction loop, same question: 0.751 -> 0.842 confidence (retrain 0.030s)
```

Nothing in this README is a claim you can't re-run. `scripts/smoke.py` exercises the same path end to end
over HTTP against any instance, including the live deployment:

```sh
python3 scripts/smoke.py https://deflectly-production.up.railway.app
```

## Use it on your own tickets (no server)

The CLI drives the same model with no Flask, no browser and no API key. Point it at a
CSV/TSV/JSON export from your helpdesk or a JSONL of `{"question": ..., "answer": ...}`:

```sh
pip install -e .                 # gives you the `deflectly` command
deflectly train zendesk_export.csv
deflectly ask "how do I rotate an API key?"
deflectly teach "do you offer nonprofit pricing?" "Yes - nonprofits get 30% off."
deflectly stats
```

```
trained on 10 pairs from csv (question=Ticket Subject, answer=Resolution Comment) in 0.010s
  dropped 1 too-short, 2 with PII (review and re-train with --keep-pii to include)

$ deflectly ask "can I get my data out as csv?"
A: Yes. Settings, Export, and you will get everything as CSV within an hour.
   confidence 50%

$ deflectly teach "do you offer nonprofit pricing?" "Yes - nonprofits get 30% off every plan."
taught in 0.007s on 11 pairs
  before (41%): Yes, 14 days, no card required. Start one from the pricing page.
  after  (84%): Yes - nonprofits get 30% off every plan.
```

Column names are detected from the header (`subject`/`title`/`question`, and
`resolution`/`reply`/`answer`/`comment`/`body`), pairs shorter than 8 characters are dropped, and
**pairs containing emails or phone numbers are dropped by default** so customer PII does not land in
your model — pass `--keep-pii` if you have already anonymised them. Run it without installing via
`python3 scripts/deflectly_cli.py`. `deflectly demo` runs the built-in sample corpus offline.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/tickets` · `/api/upload` · `/api/sample` · `/api/import` | Load tickets (paste, file, sample corpus, helpdesk sync skeleton) |
| `POST /api/review` | Approve/clean the training set |
| `POST /api/train` | Train; streams SSE progress |
| `POST /api/compare` | Baseline vs. your model on held-out questions |
| `POST /api/predict` | Answer a question from the trained model (the widget endpoint) |
| `POST /api/feedback` | 👍/👎; a 👎 with `corrected_answer` retrains immediately |
| `GET /api/healthz` · `GET /api/stats` | `{"ok": true}` (deploy healthcheck) and session/correction counters |

## Embedding it

```html
<script defer src="https://your-deflectly.example/static/widget.js"
  data-endpoint="https://your-deflectly.example/api/predict"
  data-title="Support"
  data-primary="#0a6e3c"></script>
```

## Layout

```
app/app.py                 Flask app, SQLite, all routes
app/model.py               three-channel TF-IDF responder (+ save/load)
app/data/sample_data.py    56-ticket sample corpus, 5 held-out questions, baseline KB
app/static/widget.js       embeddable widget with the feedback loop
app/templates/             landing, demo steps, review, teach, widget
scripts/deflectly_cli.py   `deflectly train|ask|teach|stats|demo` — the no-server path
scripts/smoke.py           HTTP end-to-end check (used by CI and after deploys)
scripts/benchmark.py       reproduces the table above, offline
pyproject.toml             installable: `pip install -e .` gives the `deflectly` command
tests/                     19 tests (app, worker-safety, CLI)
```

## Deploy

```sh
docker build -t deflectly:latest . && docker run -p 8451:8451 deflectly:latest
```

Railway / Render / fly.io / Heroku work as-is (`Dockerfile`, `Procfile`, and `railway.json` with a
healthcheck on `/api/healthz`, so a broken revision never goes live). Notes in `DEPLOY.md`.

| Env var | Purpose |
|---|---|
| `PORT` | HTTP port (Railway/Render inject it; default 8451) |
| `SECRET_KEY` | Flask session secret. Set it in production. If unset, a random key is generated once and persisted to `app/.session_key` so all workers agree |
| `STRIPE_SECRET_KEY` | Optional; Stripe test-mode checkout instead of demo intent recording |

## Running with more than one worker

This is built to run under `gunicorn --workers 2`, which is where the interesting bugs live. Session state
(tickets, review status, model version) lives in SQLite, and trained models are written to
`app/models/<session>.pkl` with a version stamp, so every worker serves the current model instead of its
own stale copy. Four tests exist purely to pin that down:

- `test_state_survives_worker_switch`, `test_review_then_train_survives_stale_cache` — state is not per-process
- `test_compare_page_reloads_model_on_other_worker` — any worker can rebuild a trained model
- `test_correction_visible_across_workers` — a correction made on one worker is not served stale by another
- `test_secret_key_race` — workers booting at the same moment must agree on one session secret

## Limits

- Retrieval, not a fine-tuned model: fast, free, grounded, and it can't hallucinate a policy — but it plateaus on paraphrase-heavy corpora. Embeddings or a cross-encoder reranker are the natural next step, and the eval harness exists to show whether a change actually wins.
- SQLite and `app/models/*.pkl` are container-local; on Railway/Render that disk is ephemeral. Attach a volume or move to Postgres before carrying real customer data. Model files are pruned to the newest 200.
- `POST /api/import` is a skeleton, not a real helpdesk sync. The path that does work today is an export file plus the CLI: `deflectly train <export.csv>`, which auto-detects question/answer columns for Intercom-, Zendesk- and Help Scout-style exports and drops rows containing emails or phone numbers unless you pass `--keep-pii`.
- Checkout is demo-grade: it records intent, or creates a Stripe test-mode session when a key is set.
- No auth or per-tenant isolation — a single-session demo, not a multi-tenant service.

## Contributing

See `CONTRIBUTING.md`. The short version: a regression test must fail without your fix.

## License

MIT — see `LICENSE`.
