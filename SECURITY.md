# Security

This is a demo application, not a hosted service — but it does handle support tickets, which routinely
contain customer PII. A few properties are deliberate and worth knowing before you deploy it:

- **Answers come from your own tickets.** No request leaves the process: there are no LLM calls, no
  third-party APIs, and no telemetry. The only outbound HTTP in the app is optional Stripe checkout when
  `STRIPE_SECRET_KEY` is set.
- **PII is flagged, not automatically removed.** `POST /api/review` marks emails and phone numbers in the
  training pairs so the operator can strip them before training. Nothing is redacted for you.
- **Sessions are not authentication.** The Flask session cookie only carries a random session id. There is
  no login, no per-tenant isolation, and no rate limiting. Anyone who can reach the API can train a model
  and read answers in their own session. Set `SECRET_KEY` so cookies can't be forged.
- **The SQLite database and trained models are plain files** on the container filesystem
  (`app/demo.db`, `app/models/<session>.pkl`). They are not encrypted at rest and are gitignored.

## Reporting a vulnerability

Open a private security advisory on this repository (Security → Advisories → Report a vulnerability), or
if you cannot, open an issue that says a private report is coming without including the details.

Please include the request that reproduces it and whether two workers are needed —
`gunicorn app.app:app --bind 0.0.0.0:8451 --workers 2` has been the source of several real bugs in this
codebase, so say which way you ran it.

Expect an acknowledgement rather than a fix timeline; this is a side project.
