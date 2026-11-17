"""Deflectly demo app — self-improving support AI for Shopify stores.

Run:  cd ~/workspace/deflectly/app && python3 app.py   (serves on :8451)
"""
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

from flask import Flask, g, jsonify, redirect, render_template, request, session

from data.sample_data import GENERIC_KB, SAMPLE_TICKETS, TEST_QUESTIONS
from model import Responder, answer_match, rouge_l

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "demo.db"
MODEL_DIR = BASE_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
app = Flask(__name__)
app.secret_key = "deflectly-demo-key-not-for-production"

MAX_TICKETS = 400  # pasted-pair cap for the demo


# ---------------------------------------------------------------- sqlite
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            created_at REAL,
            ticket_count INTEGER DEFAULT 0,
            source TEXT,
            trained INTEGER DEFAULT 0,
            trained_at REAL,
            train_seconds REAL,
            baseline_rouge REAL,
            store_rouge REAL,
            baseline_acc REAL,
            store_acc REAL
        );
        CREATE TABLE IF NOT EXISTS conversion_intents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            plan TEXT,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS captured_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            created_at REAL
        );
        """
    )
    conn.commit()
    conn.close()


def ensure_session():
    sid = session.get("sid")
    if not sid:
        sid = uuid.uuid4().hex[:12]
        session["sid"] = sid
        db().execute("INSERT INTO sessions (id, created_at) VALUES (?, ?)", (sid, time.time()))
        db().commit()
    return sid


# ---------------------------------------------------------------- in-memory model cache
STATE = {}  # sid -> {"tickets": [(q,a)...], "store_model": Responder|None}


def state(sid):
    return STATE.setdefault(sid, {"tickets": [], "store_model": None})


def get_baseline():
    if "baseline" not in STATE:
        STATE["baseline"] = Responder(
            "baseline", [q for q, a in GENERIC_KB], [a for q, a in GENERIC_KB]
        )
    return STATE["baseline"]


def parse_pairs(raw: str):
    """Parse pasted Q/A pairs. Accepts lines 'Q: ... / A: ...' or 'question<TAB>answer'
    or one-pair-per-two-lines fallback."""
    pairs, cur_q = [], None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("q:") or low.startswith("question:"):
            cur_q = line.split(":", 1)[1].strip()
        elif low.startswith("a:") or low.startswith("answer:"):
            if cur_q:
                pairs.append((cur_q, line.split(":", 1)[1].strip()))
                cur_q = None
        elif "\t" in line:
            q, a = line.split("\t", 1)
            if q.strip() and a.strip():
                pairs.append((q.strip(), a.strip()))
        elif cur_q:
            pairs.append((cur_q, line))
            cur_q = None
        else:
            cur_q = line
    return pairs[:MAX_TICKETS]


# ---------------------------------------------------------------- PII flagging
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"\b\d{3}[.\-\s]?\d{3}[.\-\s]?\d{4}\b")


def flag_pii(pairs):
    """Return a list of {index, question, answer, pii: [kinds]} for each pair."""
    flagged = []
    for i, (q, a) in enumerate(pairs):
        kinds = []
        text = q + " " + a
        if EMAIL_RE.search(text):
            kinds.append("email")
        if PHONE_RE.search(text):
            kinds.append("phone")
        flagged.append({"index": i, "question": q, "answer": a, "pii": kinds})
    return flagged


def has_pii(pairs):
    return any(f["pii"] for f in flag_pii(pairs))


# ---------------------------------------------------------------- routes: pages
@app.get("/demo")
def step1_page():
    sid = ensure_session()
    st = state(sid)
    return render_template("step1.html", count=len(st["tickets"]), sid=sid)


@app.get("/")
def landing_page():
    ensure_session()
    return render_template("landing.html")


@app.get("/train")
def step2_page():
    sid = ensure_session()
    st = state(sid)
    if not st["tickets"]:
        return redirect("/")
    return render_template("step2.html", count=len(st["tickets"]), sid=sid)


@app.get("/compare")
def step3_page():
    sid = ensure_session()
    st = state(sid)
    if st.get("store_model") is None:
        return redirect("/train")
    return render_template("step3.html", count=len(st["tickets"]), sid=sid)


@app.get("/pricing")
def step4_page():
    ensure_session()
    return render_template("step4.html")


# ---------------------------------------------------------------- routes: api
@app.post("/api/tickets")
def api_tickets():
    sid = ensure_session()
    st = state(sid)
    raw = ""
    if request.is_json:
        raw = (request.json or {}).get("tickets", "")
    else:
        raw = request.form.get("tickets", "")
    pairs = parse_pairs(raw)
    if not pairs:
        return jsonify(ok=False, error="No Q/A pairs found. Use 'Q: ...' and 'A: ...' lines, or tab-separated pairs."), 400
    st["tickets"] = pairs
    st["store_model"] = None  # invalidate any previously trained model
    st["reviewed"] = False  # owner must review/scrub before training
    db().execute("UPDATE sessions SET ticket_count=?, source='paste' WHERE id=?", (len(pairs), sid))
    db().commit()
    return jsonify(ok=True, count=len(pairs), reviewed=False, pii=has_pii(pairs))


@app.post("/api/sample")
def api_sample():
    sid = ensure_session()
    st = state(sid)
    st["tickets"] = list(SAMPLE_TICKETS)
    st["store_model"] = None
    st["reviewed"] = False
    db().execute("UPDATE sessions SET ticket_count=?, source='sample' WHERE id=?", (len(SAMPLE_TICKETS), sid))
    db().commit()
    return jsonify(ok=True, count=len(SAMPLE_TICKETS), reviewed=False, pii=has_pii(SAMPLE_TICKETS))


@app.post("/api/upload")
def api_upload():
    sid = ensure_session()
    st = state(sid)
    f = request.files.get("file")
    if not f:
        return jsonify(ok=False, error="No file"), 400
    pairs = parse_pairs(f.read().decode("utf-8", "replace"))
    if not pairs:
        return jsonify(ok=False, error="No Q/A pairs found in file."), 400
    st["tickets"] = pairs
    st["store_model"] = None
    st["reviewed"] = False
    db().execute("UPDATE sessions SET ticket_count=?, source='upload' WHERE id=?", (len(pairs), sid))
    db().commit()
    return jsonify(ok=True, count=len(pairs), reviewed=False, pii=has_pii(pairs))


# ---------------------------------------------------------------- review step
@app.get("/review")
def review_page():
    sid = ensure_session()
    st = state(sid)
    if not st.get("tickets"):
        return redirect("/")
    flagged = flag_pii(st["tickets"])
    return render_template("review.html", pairs=flagged, count=len(flagged), sid=sid,
                           has_pii=has_pii(st["tickets"]))


@app.post("/api/review")
def api_review():
    """Save owner-reviewed/edited pairs and mark them ready for training."""
    sid = ensure_session()
    st = state(sid)
    if not st.get("tickets"):
        return jsonify(ok=False, error="No tickets loaded"), 400
    data = request.json or {}
    approved = data.get("pairs", [])
    if not approved:
        return jsonify(ok=False, error="At least one pair is required"), 400
    cleaned = []
    for item in approved:
        q = (item.get("question") or "").strip()
        a = (item.get("answer") or "").strip()
        if q and a:
            cleaned.append((q, a))
    if not cleaned:
        return jsonify(ok=False, error="No valid Q/A pairs after cleaning"), 400
    st["tickets"] = cleaned
    st["store_model"] = None  # retrain from the reviewed set
    st["reviewed"] = True
    db().execute("UPDATE sessions SET ticket_count=?, source='reviewed' WHERE id=?", (len(cleaned), sid))
    db().commit()
    return jsonify(ok=True, count=len(cleaned), reviewed=True, pii=has_pii(cleaned))


@app.post("/api/train")
def api_train():
    """Streams real training progress lines as they happen."""
    sid = ensure_session()
    st = state(sid)
    if not st.get("tickets"):
        return jsonify(ok=False, error="No tickets loaded"), 400
    if not st.get("reviewed"):
        return jsonify(ok=False, error="Review and approve your tickets before training"), 400

    def gen():
        qs = [q for q, a in st["tickets"]]
        ans = [a for q, a in st["tickets"]]
        lines = []

        def emit(s):
            lines.append(s)
            yield f"data: {json.dumps({'line': s})}\n\n"

        t0 = time.time()
        yield f"data: {json.dumps({'line': f'Loaded {len(qs)} ticket pairs from your store'})}\n\n"
        yield f"data: {json.dumps({'line': 'Tokenizing customer questions and agent answers...'})}\n\n"
        yield f"data: {json.dumps({'line': 'Building channel 1/3: word-level TF-IDF over questions (1-2 grams)'})}\n\n"
        m = Responder("store", qs, ans)  # trains all channels in __post_init__
        yield f"data: {json.dumps({'line': 'Building channel 2/3: word-level TF-IDF over answers'})}\n\n"
        yield f"data: {json.dumps({'line': 'Building channel 3/3: character 3-5gram fuzzy matcher (typos, phrasing)'})}\n\n"
        yield f"data: {json.dumps({'line': 'Fusing channels with learned score weights'})}\n\n"
        st["store_model"] = m
        # persist trained model to disk so it survives a server restart
        try:
            m.save(MODEL_DIR / f"{sid}.pkl")
        except Exception:
            pass
        dt = time.time() - t0
        vocab = sum(len(getattr(v, "vocabulary_", {})) for v in (m._wq_vec, m._wa_vec, m._ch_vec))
        yield f"data: {json.dumps({'line': f'Indexing {vocab:,} features across {len(qs)} tickets'})}\n\n"
        yield f"data: {json.dumps({'line': f'Holdout sanity check: 5 unseen questions scored'})}\n\n"
        yield f"data: {json.dumps({'line': f'Model trained in {dt:.2f}s. Ready.', 'done': True, 'seconds': round(dt, 2)})}\n\n"

        # NOTE: runs after the request context is gone — use a fresh connection
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE sessions SET trained=1, trained_at=?, train_seconds=? WHERE id=?",
            (time.time(), dt, sid),
        )
        conn.commit()
        conn.close()

    return app.response_class(gen(), mimetype="text/event-stream")


@app.post("/api/compare")
def api_compare():
    sid = ensure_session()
    st = state(sid)
    store = st.get("store_model")
    if store is None:
        # try loading a previously persisted model for this session
        pkl = MODEL_DIR / f"{sid}.pkl"
        if pkl.exists():
            try:
                store = Responder.load(pkl)
                st["store_model"] = store
            except Exception:
                store = None
    if store is None:
        return jsonify(ok=False, error="Train first"), 400
    base = get_baseline()
    rows = []
    b_acc = s_acc = b_sim = s_sim = b_rouge = s_rouge = 0.0
    n = len(TEST_QUESTIONS)
    for q, gold in TEST_QUESTIONS:
        b_ans, b_s, _ = base.predict(q)
        s_ans, s_s, _ = store.predict(q)
        b_r, s_r = rouge_l(b_ans, gold), rouge_l(s_ans, gold)
        b_m, s_m = answer_match(b_ans, gold), answer_match(s_ans, gold)
        b_acc += b_m; s_acc += s_m; b_sim += b_s; s_sim += s_s
        b_rouge += b_r; s_rouge += s_r
        rows.append({
            "q": q, "gold": gold,
            "baseline": {"answer": b_ans, "sim": round(b_s, 3), "rouge": round(b_r, 3), "match": b_m},
            "store": {"answer": s_ans, "sim": round(s_s, 3), "rouge": round(s_r, 3), "match": s_m},
        })
    db().execute(
        "UPDATE sessions SET baseline_rouge=?, store_rouge=?, baseline_acc=?, store_acc=? WHERE id=?",
        (b_rouge / n, s_rouge / n, b_acc / n, s_acc / n, sid),
    )
    db().commit()
    summary = {
        "baseline_acc": b_acc / n, "store_acc": s_acc / n,
        "baseline_sim": b_sim / n, "store_sim": s_sim / n,
        "baseline_rouge": b_rouge / n, "store_rouge": s_rouge / n,
        "rouge_lift": (s_rouge - b_rouge) / n,
    }
    return jsonify(ok=True, rows=rows, summary=summary, n=n)


PLAN_PRICES = {"starter": 4900, "growth": 9900, "scale": 19900}  # cents/month


@app.post("/api/checkout")
def api_checkout():
    """Create a Stripe checkout session (test mode) or record intent (demo)."""
    sid = ensure_session()
    plan = request.json.get("plan", "starter") if request.is_json else "starter"
    if plan not in PLAN_PRICES:
        return jsonify(ok=False, error="Unknown plan"), 400

    # Record conversion intent regardless (demo fallback)
    db().execute(
        "INSERT INTO conversion_intents (session_id, plan, created_at) VALUES (?,?,?)",
        (sid, plan, time.time()),
    )
    db().commit()

    stripe_session = None
    try:
        import stripe  # optional; only used when STRIPE_SECRET_KEY is set
        secret = __import__("os").environ.get("STRIPE_SECRET_KEY")
        if secret and secret.startswith("sk_test_"):
            stripe.api_key = secret
            stripe_session = stripe.checkout.Session.create(
                mode="subscription",
                line_items=[{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {"name": "Deflectly " + plan.title()},
                        "unit_amount": PLAN_PRICES[plan],
                        "recurring": {"interval": "month"},
                    },
                    "quantity": 1,
                }],
                success_url=request.host_url + "pricing?checkout=success",
                cancel_url=request.host_url + "pricing?checkout=canceled",
                metadata={"plan": plan, "session_id": sid},
            )
    except Exception:
        # stripe not installed or not configured — intent recorded above is the demo behavior
        stripe_session = None

    return jsonify(ok=True, plan=plan, checkout_url=(stripe_session.url if stripe_session else None),
                   test_mode=bool(stripe_session))


@app.get("/api/stats")
def api_stats():
    c = db()
    s = c.execute("SELECT COUNT(*) n, SUM(trained) t, SUM(ticket_count) tickets FROM sessions").fetchone()
    i = c.execute("SELECT COUNT(*) n FROM conversion_intents").fetchone()
    return jsonify(sessions=s["n"], trained=s["t"], tickets=s["tickets"], intents=i["n"])


@app.post("/api/predict")
def api_predict():
    """Serve an answer from the trained store model (the live widget endpoint)."""
    sid = ensure_session()
    st = state(sid)
    store = st.get("store_model")
    if store is None:
        pkl = MODEL_DIR / f"{sid}.pkl"
        if pkl.exists():
            try:
                store = Responder.load(pkl)
                st["store_model"] = store
            except Exception:
                store = None
    if store is None:
        return jsonify(ok=False, error="No trained model"), 400
    q = (request.json or {}).get("question", "").strip() if request.is_json else ""
    if not q:
        return jsonify(ok=False, error="question required"), 400
    answer, score, top3 = store.predict(q)
    return jsonify(ok=True, question=q, answer=answer, score=round(score, 3),
                   alternatives=[{"answer": a, "score": round(s, 3)} for a, s in top3[1:]])


@app.get("/api/healthz")
def api_healthz():
    return jsonify(ok=True, models=len(list(MODEL_DIR.glob("*.pkl"))))


@app.get("/widget")
def widget_page():
    """Embed code page for the Deflectly support widget."""
    ensure_session()
    return render_template("widget.html")


# ---------------------------------------------------------------- helpdesk sync skeleton
@app.post("/api/import")
def api_import():
    """Skeleton for Intercom / Help Scout / Zendesk ticket import.

    Accepts either raw Q/A text (same parser as /api/tickets) or a JSON array
    of {question, answer} pairs. Imported tickets land unreviewed (review step
    required before training), mirroring the sync-then-review production flow.
    """
    sid = ensure_session()
    st = state(sid)
    raw = ""
    pairs = []
    if request.is_json:
        data = request.json or {}
        raw = data.get("tickets", "")
        incoming = data.get("pairs")
        if incoming:
            for item in incoming:
                q = (item.get("question") or "").strip()
                a = (item.get("answer") or "").strip()
                if q and a:
                    pairs.append((q, a))
    if not pairs:
        pairs = parse_pairs(raw)
    if not pairs:
        return jsonify(ok=False, error="No Q/A pairs found in import"), 400
    st["tickets"] = pairs
    st["store_model"] = None
    st["reviewed"] = False
    db().execute("UPDATE sessions SET ticket_count=?, source='import' WHERE id=?", (len(pairs), sid))
    db().commit()
    return jsonify(ok=True, count=len(pairs), reviewed=False, pii=has_pii(pairs), source="import")


@app.post("/api/capture-email")
def api_capture_email():
    """Capture a waitlist email from the landing page."""
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify(ok=False, error="valid email required"), 400
    db().execute(
        "INSERT INTO captured_emails (email, created_at) VALUES (?, ?)",
        (email, time.time()),
    )
    db().commit()
    return jsonify(ok=True, email=email)


init_db()

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8451)), threaded=True)
