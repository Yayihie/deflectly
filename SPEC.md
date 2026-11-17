# Deflectly — Spec

**Self-improving support-desk AI for Shopify / e-commerce stores.**

One-liner: paste your store's past support tickets, watch a model trained on *your* store beat a generic support bot in the same session, then put it live — and it retrains nightly from new tickets.

Status: V1 spec. Working demo at `app/` (Flask, port 8451).

---

## 1. Problem

E-commerce support owners (Shopify merchants running Gorgias, Zendesk, or plain inbox) face:

- **High ticket volume, low complexity**: 60–80% of tickets are "where's my order," "return policy," "sizing," "discount codes." They are repetitive but *store-specific* — a generic chatbot can't answer "does the Rainier shell run small?" because the answer lives in the merchant's head and past ticket history, not in a public FAQ.
- **Generic bots deflect badly**: out-of-the-box LLM/FAQ bots answer generic questions and hallucinate or hand off on store specifics, so merchants measure low deflection and turn them off.
- **Retraining is a project**: fine-tuning pipelines (data cleaning, train, eval, deploy, rollback) are something only ML teams run. A Shopify store owner will never do it.

Deflectly's wedge: **the store's own past tickets are the training set**, and training+eval must be something the owner can watch happen in one sitting.

## 2. Target buyer

- Shopify (and BigCommerce/Woo) store owners doing 100–5,000 support tickets/month.
- Currently on Gorgias, Zendesk, Re:amaze, or a shared inbox.
- Buyer persona: operator/founder, not technical. Purchases self-serve, $49–$199/mo, decides within one demo session.
- Champion metric in their head: tickets deflected without a human, CSAT on bot answers, and cost per ticket (a Gorgias seat + human time ≈ $1.50–$4.00/ticket).

## 3. Positioning vs OpenPond

OpenPond is a **horizontal** raw-data→trained-model engine (ingest anything, train SFT/RL LoRA adapters, serve serverless). Deflectly is a **vertical wrapper** on that pattern for one use case: support deflection for e-com.

- Deflectly owns: ticket ingestion UX, the review loop, the eval gate that a store owner can read, pricing/packaging, the Shopify-facing widget.
- OpenPond (or an equivalent trainer) is a pluggable backend, not a dependency for the demo — this repo's demo is fully self-contained.

**Integration path** (when OpenPond is ready):

```
Deflectly ticket store
  → OpenPond SDK addTask("ingest", tickets)      # raw data lands in OpenPond's pipeline
  → Ponder SFT pass (LoRA on ticket Q/A pairs)   # nightly adapter per store
  → Ponder RL pass (preference from thumbs-up/down + CSAT)
  → serverless serve (per-store adapter endpoint)
  → Deflectly eval gate before each deploy; rollback = pin previous adapter
```

Deflectly's own fallback trainer (this demo): TF-IDF multi-channel nearest-answer classifier — real training, seconds, no GPU. The product surface (paste → train → compare → checkout) is identical whichever backend trains.

## 4. Data flow (production)

```
1. INGEST    Shopify order events + past ticket export (CSV/Gorgias API) + live widget transcripts
2. REVIEW    Owner sees candidate Q/A pairs, approves/edits/removes PII
             (dirty pairs never enter training)
3. TRAIN     nightly adapter train on approved pairs
             (demo: in-session TF-IDF; prod: SFT LoRA via backend)
4. EVAL      holdout eval every train: exact-match, ROUGE-L answer overlap,
             deflection-sim score; train only ships if it beats current model
5. SERVE     widget/email/chat answers from best adapter; low-confidence → human handoff
6. LEARN     agent resolutions + CSAT become next night's training data (the loop)
```

The loop is the product: every human answer improves tomorrow's model without the owner doing anything.

## 5. Real-time conversion wedge

The demo IS the sales motion. One session, four steps:

1. **Paste tickets** — owner pastes Q/A pairs (or loads the 60-ticket sample store). Their data, their model.
2. **Train** — click once; live streamed training log (tokenize → 3 channels → fuse → ready), finishes in <1s for the retrieval model.
3. **Compare live** — same 5 held-out test questions answered side-by-side by (a) the generic baseline bot and (b) their just-trained model, with **real** scores rendered as they compute: exact answer match and ROUGE-L answer overlap. The trained model visibly wins because it knows *their* policies.
4. **Checkout** — pricing tiers; the trained model they just watched get built is what they're buying.

Why this converts: the "before/after on my own data" moment is the demo; there is nothing to imagine. In the verified run below the lift is large and honest.

## 6. Pricing

| Tier | Price | Tickets/mo | Includes |
|---|---|---|---|
| Starter | $49 | 500 | Nightly retrain, email + chat widget |
| Growth | $99 | 2,500 | + eval reports, Gorgias/Zendesk sync, human handoff rules |
| Scale | $199 | Unlimited | + custom SFT adapter, weekly eval + rollback, priority support |

All plans: nightly retrain from new tickets, eval gate before deploy, one-click rollback. Unit economics target: Growth customer deflecting ~60% of 2,500 tickets at ~$2/ticket human cost saves ~$3,000/mo against $99 — easy sell if deflection claim holds, which is why eval honesty is a core product value.

## 7. Metrics

- **Deflection rate** — % of tickets resolved with no human touch (primary).
- **CSAT** on bot-answered tickets vs human-answered (parity target ≥ 90% of human).
- **Cost per ticket** — blended (bot cents + human dollars weighted by deflection).
- **Eval metrics per train** (shown to owner): exact answer match, ROUGE-L answer overlap vs holdout gold answers, plus retrieval confidence distribution.
- North-star: weekly deflected tickets per store.

## 8. Scope

### V1 (this demo + first 10 design partners)
- Ticket paste/upload ingest with Q/A pair parser (`Q:/A:`, TSV, line pairs)
- In-session training (retrieval model) with streamed progress log
- Baseline-vs-store-model holdout comparison with real scores
- Shopify widget (chat bubble) answering from trained model; human handoff on low confidence
- SQLite → Postgres; single-store; nightly retrain cron; eval report email
- Gorgias CSV export import

### V2
- SFT LoRA adapters via OpenPond backend (multi-store, model per tenant)
- RL pass from 👍/👎 and CSAT signals
- Order-status tool calls (Shopify Admin API) so the bot answers live WISMO, not just policies
- Auto-draft mode (bot drafts, agent one-click sends) before full auto
- Multilingual stores; A/B old-vs-new adapter on live traffic; SOC2 path

## 9. Risks

- **Hallucination on store policies** — mitigations: retrieval-grounded answers only (demo never free-generates), confidence threshold + handoff, eval gate before deploy.
- **Dirty training data** (agents' inconsistent past answers) — review step + eval gate; low-quality corpora just score low and the owner sees it.
- **"Trained model" skepticism** — owners may not believe a seconds-long model is real. Counter: show raw scores, let them paste their own test question live (planned).
- **Gorgias/Zendesk ship their own AI** and bundle it — counter: ours is trained on *their* answers and they can eval it before paying; platform AI is generic.
- **Retrieval-model ceiling**: TF-IDF ranks well on templated support Q/A but plateaus on paraphrase-heavy corpora; the SFT adapter (V2) is the answer, and the eval harness already measures whether the upgrade actually wins.
- **Privacy** — ticket exports contain customer PII; V1 needs scrubbing at the review step, and the demo's paste-your-own-data flow sets the right expectation of per-store isolation.
- **Churn if deflection doesn't move** — the eval report makes progress (or stagnation) visible monthly; honest metrics keep trust even when the number is flat.

## 10. Verified demo results (real run, 2026-11-17)

Sample store (60 pairs), 5 held-out questions, port 8451:

| Model | Exact answer match | ROUGE-L answer overlap | Avg cosine sim |
|---|---|---|---|
| Generic baseline KB | 0/5 (0%) | 0.174 | 0.588 |
| Store-trained model | 5/5 (100%) | 1.000 | 0.557 |

Training time: ~0.07–0.08s. The store model wins on match and overlap; baseline's slightly higher raw cosine is an artifact of its smaller KB (fewer, more generic vectors), which is exactly why match/overlap are the headline metrics.
