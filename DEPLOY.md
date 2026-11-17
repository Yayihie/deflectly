# Deflectly — deploy & domain

## Domain: verified availability + prices (2026-11-17)

Availability checked two ways — registry RDAP (rdap.org) and `whois` — not just DNS.

| Name | Status | Yr 1 | Renewal | 3-yr total | Registrar |
|---|---|---|---|---|---|
| `deflectly.com` | **TAKEN** (live site, Squarespace IPs 216.150.x) | — | — | — | — |
| `deflectly.cc` | available | $2.40 | $8.55 | $19.50 | Porkbun |
| `deflectly.xyz` | available | $1.29 | $14.21 | $29.71 | Porkbun |
| `deflectly.dev` | available | $7.75 | $12.87 | $33.49 | Porkbun |
| `deflectly.app` | available | $7.75 | $14.93 | $37.61 | Porkbun |
| `deflectly.co` | available | $14.76 | $31.20 | $77.16 | Porkbun |
| `deflectly.io` | available | $27.12 | $51.80 | $130.72 | Porkbun |
| `deflectly.ai` | available | $81.70 | $82.70 | $248.10 | Porkbun |

Also confirmed available: `getdeflectly.com`, `deflectly.net`, `deflectly.us`, `deflectly.org`,
`deflectly.chat`, `deflectly.tools`, `deflectly.help`, `deflectly.sh`.

Cloudflare Registrar sells at cost (flat pricing, no first-year promo): `.us` $6.50, `.cc` $8.00,
`.dev` $12.20, `.app` $14.20, `.co` $30, `.io` $50 per year. Needs a Cloudflare account.

**Recommendation:** `deflectly.app` — cheap first year, sane renewal, and `.app` forces HTTPS,
which is the right signal for a support-widget product. Cheapest long-run is `deflectly.cc`.
Avoid `.xyz`/`.store`/`.shop` if you plan to hold the name: their renewals are 11–17× year one.

> Prices are third-party tracker snapshots (Porkbun API, tldbee, nextname) from 2026-11-17;
> confirm at checkout. Registrar sites block automated checks (Cloudflare Turnstile).

## Live (deployed 2026-11-17)

- URL: **https://deflectly-production.up.railway.app** (Railway project `deflectly`, workspace yayihie's Projects)
- Deployed via `railway up` from this directory; `$PORT` injected and honored.
- Verified on the public URL: `/`, `/demo`, `/widget`, `/pricing`, `/teach` → 200; healthz OK;
  sample→review→train→compare gives baseline 0.0 vs store 1.0; correction loop retrains and
  serves the corrected answer; `/api/stats` counts corrections.
- Redeploy: `railway up --ci` from this directory.
- Custom domain later: `railway domain` (add your own) then point registrar DNS at it.

## Run locally

```sh
cd ~/workspace/deflectly/app && python3 app.py     # dev server, port 8451
```

Production-style (this is what the container runs):

```sh
pip install -r requirements.txt
PORT=8451 gunicorn app.app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

Verified 2026-11-17: gunicorn boots, `/api/healthz` OK, sample→review→train→compare green,
correction loop 0.485 → 0.831 on the same question.

## Deploy the Docker image

```sh
docker build -t deflectly:latest .
docker run -p 8451:8451 deflectly:latest
```

### Railway (free `*.up.railway.app` subdomain)
1. `railway login` (browser approval), then `railway init` + `railway up` in this directory.
2. Railway sets `$PORT` automatically — the app and Docker CMD both read it.
3. Free subdomain: Settings → Networking → Generate Domain. No registrar needed.
4. Custom domain later: add it in Railway, then point registrar DNS at the given CNAME.
5. Optional: `railway variables --set STRIPE_SECRET_KEY=sk_live_...`.

### Render
1. New Web Service from the repo; Render detects the Dockerfile.
2. Add env var `PORT=10000` (Render requires this port), or let Docker CMD handle `$PORT`.
3. Health check path: `/api/healthz`.

### fly.io
1. `fly launch` (detects the Dockerfile), `fly deploy`.
2. `fly ips allocate-v4`; `fly certs add deflectly.app` once a domain is owned.

### Notes on persistence
SQLite (`app/demo.db`) and trained models (`app/models/`) live on the container filesystem.
On Railway/Render that disk is ephemeral — data resets on redeploy. Attach a volume (Railway)
or move to Postgres before this carries real customer data.

## Pointing your domain
Provider gives a hostname (e.g. `deflectly.up.railway.app`). At the registrar set a `CNAME`
for `www` and either an `A`/`ALIAS`/flattened `CNAME` for `@`. Then verify `https://<domain>`
loads the landing page. Railway/Render/fly issue TLS automatically.

## Email capture
Landing page POSTs to `/api/capture-email`, stored in `demo.db`. Wire to a real provider
before launch.
