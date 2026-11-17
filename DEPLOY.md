# Deflectly — deploy & domain

## Domain status (DNS check, not registration)
Checked via `dig +short`:
- `deflectly.com` — resolves to 216.150.16.1 (taken/parked — do not buy)
- `deflectly.io` — no DNS (available to register)
- `deflectly.app` — no DNS (available to register)

> A missing DNS record means the domain is not currently resolving, not that it's available to buy. Confirm with a registrar (Namecheap, Google Domains, Cloudflare, etc.) before purchasing.

## Deploy the Docker image

The app is containerized. `Dockerfile` builds on `python:3.12-slim`, installs `requirements.txt`, and runs `app.py` on `$PORT` (default 8451).

```sh
# build
docker build -t deflectly:latest .

# run locally
docker run -p 8451:8451 deflectly:latest
# open http://localhost:8451
```

### Railway
1. Connect your GitHub repo (or deploy from Docker Hub image `deflectly:latest`).
2. Set environment variable `PORT=8451` (Railway injects `$PORT` automatically; the app reads it).
3. Add a domain in Railway and point your registrar's DNS to it.
4. Optional: set `STRIPE_SECRET_KEY=sk_live_...` for real checkout.

### Render
1. Create a Web Service, connect repo or deploy image.
2. Build command: `docker build` (Render auto-detects Dockerfile).
3. Start command: `python3 app.py` (or let Docker CMD handle it).
4. Add environment variable `PORT=10000` (Render requires this port).
5. Add a custom domain and configure DNS via Render.

### fly.io
1. `fly launch` (it will detect the Dockerfile).
2. `fly deploy`.
3. `fly ips allocate-v4` (if needed) and `fly domains add deflectly.app`.
4. Set secrets: `fly secrets set STRIPE_SECRET_KEY=...`

### Heroku (Procfile included)
1. `heroku create deflectly-app`
2. `heroku stack:set container`
3. `git push heroku main`
4. `heroku config:set PORT=8451`
5. Add custom domain via Heroku Dashboard; point DNS.

## Pointing your domain
Once deployed, you get a provider hostname (e.g., `deflectly.up.railway.app`). At your registrar, set:
- `A` record or `CNAME` for `www` and `@` pointing to the provider's target.
- Wait for propagation, then verify `https://deflectly.app` loads the landing page.

## Email capture
The landing page POSTs to `/api/capture-email`, which stores addresses in `demo.db`. Connect this to your real email provider (ConvertKit, Mailchimp, etc.) before launch.

## SSL / HTTPS
Railway, Render, and fly.io provision automatic HTTPS on custom domains. No extra config needed.
