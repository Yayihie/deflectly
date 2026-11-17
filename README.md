# Deflectly

Support AI for B2B SaaS that trains on your own support tickets — no LLM, no API key, no GPU.

Paste your past support Q/A tickets, watch a model train on *your* data in about 0.07s, see it beat a
generic bot side-by-side on held-out questions, then correct it live and watch it get the answer right.
Ship it with an embeddable chat widget whose 👍/👎 corrections retrain the model.

Live demo: https://deflectly-production.up.railway.app

![Deflectly demo: generic bot 0/5 vs your model 5/5](app/static/demo-compare.png)

## Why it exists

Generic chatbots can't answer "does the Growth plan include SAML?" or "what's the API rate limit?"
Those answers live in your ticket history, not in a public FAQ. Deflectly grounds every answer in
retrieval over your own past replies, so it answers product-specific questions and can't invent policy.

## Run it

```sh
pip install -r requirements.txt
cd app && python3 app.py          # dev server on http://127.0.0.1:8451
```

Or production-style with gunicorn:

```sh
pip install -r requirements.txt
PORT=8451 gunicorn app.app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

Flow (6 steps, ~3 minutes):

1. **Paste tickets** — paste Q/A pairs from Intercom / Help Scout / Zendesk, upload a `.txt`/`.tsv`, or load the built-in 56-ticket sample ("Nimbus HQ").
2. **Review** — check each pair, fix mistakes, remove any containing customer PII (emails, phones). Only approved pairs train.
3. **Train** — live SSE log of the model build: word TF-IDF over questions, TF-IDF over answers, character 3–5-gram fuzzy channel, fused with score weights. ~0.07s on CPU.
4. **Compare** — 5 unseen questions answered by a generic baseline bot and by your model, scored with exact-match and ROUGE-L against held-out gold answers.
5. **Teach it** — ask something your docs don't cover, mark it wrong, type the right answer: the model retrains in under a second and serves the correction on the next ask. This is the loop that runs in production whenever an agent fixes a reply.
6. **Go live** — pricing tiers, plus your widget embed code at `/widget`.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/tickets`, `POST /api/upload`, `POST /api/sample`, `POST /api/import` | Load tickets (paste, file, sample corpus, helpdesk sync skeleton) |
| `POST /api/review` | Approve/clean the training set |
| `POST /api/train` | Train; streams SSE progress |
| `POST /api/compare` | Baseline vs. your model on held-out questions |
| `POST /api/predict` | Answer a question from the trained model (widget endpoint) |
| `POST /api/feedback` | 👍/👎; a 👎 with `corrected_answer` retrains immediately |
| `GET /api/healthz`, `GET /api/stats` | Health and session/correction counters |

## Embeddable widget

`/widget` shows the snippet to paste on your site:

```html
<script defer src="https://your-deflectly.app/static/widget.js"
  data-endpoint="https://your-deflectly.app/api/predict"
  data-title="Support"
  data-primary="#0a6e3c"></script>
```

Every answer shows 👍/👎; a 👎 with a correction posts to `/api/feedback`, which retrains the store
model on the spot and persists it.

## Project layout

- `app/app.py` — Flask app, SQLite, all routes
- `app/model.py` — scikit-learn three-channel TF-IDF nearest-answer responder (+ `save`/`load`)
- `app/data/sample_data.py` — 56-ticket sample corpus, 5 held-out eval questions, generic baseline KB
- `app/static/widget.js` — embeddable chat widget with the feedback loop
- `app/templates/` — landing, demo steps 1–4, review, teach, widget pages
- `tests/` — 12 tests
- `PRODUCT.md`, `SPEC.md`, `DEPLOY.md` — positioning, spec, deploy/domain notes

## Deploy

```sh
docker build -t deflectly:latest .
docker run -p 8451:8451 deflectly:latest
```

Railway / Render / fly.io / Heroku work (Dockerfile and Procfile included). See `DEPLOY.md`.

Environment variables:

| Var | Purpose |
|---|---|
| `PORT` | HTTP port (Railway/Render inject it; defaults to 8451) |
| `SECRET_KEY` | Flask session secret. **Set this in production.** If unset, a random key is generated once and persisted to `app/.session_key` so all workers share it |
| `STRIPE_SECRET_KEY` | Optional; enables Stripe test-mode checkout sessions |

## Verified results

Built-in sample store, 5 held-out questions:

| Model | Exact match | ROUGE-L |
|---|---|---|
| Generic baseline | 0/5 (0%) | 0.07 |
| Store-trained | 5/5 (100%) | 1.00 |

Correction loop, same question before/after one fix: 43% → 86% confidence (measured on `/teach`).
Training time: ~0.07s.

## Tests

```sh
pip install pytest
pytest
```

12 tests, including regressions for running under multiple gunicorn workers:

- `test_state_survives_worker_switch`, `test_review_then_train_survives_stale_cache` — session state lives in SQLite, not per-process memory
- `test_compare_page_reloads_model_on_other_worker` — any worker can rebuild a trained model from disk
- `test_secret_key_race` — concurrent workers must agree on one session secret
- plus sample→review→train→compare, review gating, predict, feedback retrain, teach gate, healthz, save/load, scoring

## Notes and limits

- The model is retrieval (TF-IDF), not a fine-tuned LLM: fast, zero marginal cost per answer, and it can't hallucinate a policy — but it plateaus on paraphrase-heavy corpora. Tuned adapters are the natural V2.
- SQLite and `app/models/*.pkl` live on the container filesystem; on Railway/Render that disk is ephemeral. Attach a volume or move to Postgres before carrying real customer data. Model files are pruned to the newest 200.
- `/api/import` is a skeleton for Intercom / Help Scout / Zendesk sync.
- Checkout is demo-grade: it records intent, or creates a Stripe test-mode session when `STRIPE_SECRET_KEY` is set.

## License

MIT — see `LICENSE`.
