# Deflectly

Self-improving support-desk AI for B2B SaaS products.

Paste your product's past support Q/A tickets, watch a model train on *your* data in seconds,
see it beat a generic bot side-by-side on held-out questions, then put it live with an embeddable
chat widget. It retrains nightly from new conversations.

## Run the demo

```sh
cd ~/workspace/deflectly/app
python3 -m venv .venv && source .venv/bin/activate
pip install -r ../requirements.txt
python3 app.py
```

Open `http://127.0.0.1:8451`.

Flow (5 steps, ~2 minutes):
1. **Paste tickets** — paste Q/A pairs from Intercom / Help Scout / Zendesk, upload a file, or load the built-in 56-ticket SaaS sample ("Nimbus HQ").
2. **Review** — check each pair, fix mistakes, and remove any containing customer PII (emails, phone numbers). Only approved pairs train your model.
3. **Train** — click "Train my model"; a live SSE log shows the TF-IDF model being built (finishes in ~0.07s).
4. **Compare** — 5 unseen questions answered by both the generic baseline bot and your trained model, with real exact-match and ROUGE-L scores.
5. **Go live** — pick Starter ($49), Growth ($99), or Scale ($199). Checkout creates a Stripe test-mode session when `STRIPE_SECRET_KEY` is set; otherwise it records intent (demo). Grab your widget embed code at `/widget`.

## Embeddable widget

`/widget` shows the snippet to paste on your SaaS site:

```html
<script defer src="https://your-deflectly.app/static/widget.js"
  data-endpoint="https://your-deflectly.app/api/predict"
  data-title="Support"
  data-primary="#0a6e3c"></script>
```

The widget posts to `/api/predict` on your Deflectly instance, so it works once you've trained a model.

## Project files

- `app/app.py` — Flask app, SQLite (`demo.db`), all routes (tickets, review, train, compare, predict, import, checkout, healthz, widget)
- `app/model.py` — scikit-learn TF-IDF three-channel nearest-answer responder (+ `save`/`load` persistence)
- `app/data/sample_data.py` — 56-ticket Nimbus HQ (B2B SaaS) corpus, 5 held-out eval questions, generic baseline KB
- `app/static/widget.js` — embeddable chat widget
- `app/templates/` — step1–step5 + review + widget Jinja pages + base layout
- `tests/test_app.py` — 6 tests, all passing
- `Dockerfile`, `Procfile`, `requirements.txt` — deploy-ready
- `PRODUCT.md` — product positioning, pricing, metrics, risks
- `SPEC.md` — full product spec, data flow, V1/V2 scope

## Deploy

```sh
docker build -t deflectly:latest .
docker run -p 8451:8451 deflectly:latest
```

Or deploy the Docker image to Railway / Render / fly.io / Heroku (Procfile included). Set `STRIPE_SECRET_KEY=sk_test_...` for real test-mode checkout sessions.

## Verified results (Nimbus HQ sample, 5 held-out questions)

| Model | Exact match | ROUGE-L |
|---|---|---|
| Generic baseline | 0/5 (0%) | 0.074 |
| Store-trained | 5/5 (100%) | 0.83 |

Training time: ~0.07s.

## Tests

```sh
pip install pytest
pytest
```
6 tests pass: sample→review→train→compare, train-requires-review, predict, healthz, model save/load, answer match/ROUGE.
