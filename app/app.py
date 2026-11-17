"""Deflectly demo app — self-improving support AI for Shopify stores.

Run:  cd ~/workspace/deflectly/app && python3 app.py   (serves on :8451)
"""
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path

# Make sibling modules (data/, model.py) importable no matter how the app is
# launched: `python3 app/app.py` (script dir on sys.path) or
# `gunicorn app.app:app` from the repo root (only the repo root is on sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import Flask, g, jsonify, redirect, render_template, request, session

from data.sample_data import GENERIC_KB, SAMPLE_TICKETS, TEST_QUESTIONS
from model import Responder, _norm, answer_match, rouge_l

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "demo.db"
MODEL_DIR = BASE_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
app = Flask(__name__)


def _load_secret_key():
    """Session secret resolution that stays correct with multiple workers.

    Order: SECRET_KEY env var → a persisted random key file → (last resort) a random
    key. A per-process random key would be wrong here: gunicorn workers must share the
    secret, or a cookie signed by one worker is rejected by the next and every request
    starts a fresh session. A hardcoded key is worse — anyone could forge a session.

    The key file is created with O_EXCL so that workers booting simultaneously can't
    each generate their own (that race produced two different secrets in one process
    pool, which invalidated cookies on every worker switch).
    """
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    key_file = BASE_DIR / ".session_key"

    def _read_key():
        for _ in range(50):  # the winner may still be writing; give it ~1s
            try:
                stored = key_file.read_text().strip()
            except FileNotFoundError:
                return ""  # nobody created it yet — caller will create it
            except Exception:
                return ""
            if stored:
                return stored
            time.sleep(0.02)
        return ""

    stored = _read_key()
    if stored:
        return stored
    generated = uuid.uuid4().hex
    try:
        fd = os.open(str(key_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(generated)
        return generated
    except FileExistsError:
        # another worker won the race — use its key
        return _read_key() or generated
    except Exception:
        return generated


app.secret_key = _load_secret_key()

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
        CREATE TABLE IF NOT EXISTS feedback_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            question TEXT NOT NULL,
            predicted_answer TEXT,
            verdict TEXT NOT NULL,
            corrected_answer TEXT,
            retrained INTEGER DEFAULT 0,
            created_at REAL
        );
        """
    )
    # Migration: tickets + reviewed must survive across gunicorn workers and restarts.
    # In-memory state is per-process, so with >1 worker a request can land on a
    # worker that has never seen this session's tickets. Keep the source of truth in SQLite.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "tickets" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN tickets TEXT")
    if "reviewed" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN reviewed INTEGER DEFAULT 0")
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


# ------------------------------------------- session state / model cache
# tickets + reviewed are ALWAYS read from SQLite (source of truth across workers).
# Only the trained model is cached in memory — it is also persisted as <sid>.pkl,
# so any worker can rebuild it on demand.
STATE = {}  # sid -> {"store_model": Responder|None}
_BASELINE = {}


def state(sid):
    """Return this session's state with tickets/reviewed read fresh from SQLite.

    In-memory tickets would go stale: with N gunicorn workers, worker B never
    sees the write worker A made, so /api/train would reject an approved set.
    """
    st = STATE.setdefault(sid, {"store_model": None})
    tickets, reviewed = [], False
    try:
        row = db().execute("SELECT tickets, reviewed FROM sessions WHERE id=?", (sid,)).fetchone()
        if row is not None:
            if row["tickets"]:
                tickets = [(q, a) for q, a in json.loads(row["tickets"])]
            reviewed = bool(row["reviewed"])
    except Exception as exc:  # a failed read must be visible, not silently empty
        app.logger.error("state read failed for %s: %r", sid, exc)
    st["tickets"] = tickets
    st["reviewed"] = reviewed
    return st


def persist_tickets(sid, tickets, reviewed):
    """Write the ticket set through to SQLite so every worker sees it."""
    payload = json.dumps([[q, a] for q, a in tickets])
    st = STATE.setdefault(sid, {"store_model": None})
    st["tickets"] = list(tickets)
    st["reviewed"] = bool(reviewed)
    ok = False
    try:
        db().execute(
            "UPDATE sessions SET tickets=?, reviewed=?, ticket_count=? WHERE id=?",
            (payload, 1 if reviewed else 0, len(tickets), sid),
        )
        db().commit()
        ok = True
    except Exception as exc:
        app.logger.error("persist_tickets failed for %s: %r", sid, exc)
    if not ok:  # one retry on a fresh connection, then surface the failure
        conn = sqlite3.connect(DB_PATH, timeout=10)
        try:
            conn.execute(
                "UPDATE sessions SET tickets=?, reviewed=?, ticket_count=? WHERE id=?",
                (payload, 1 if reviewed else 0, len(tickets), sid),
            )
            conn.commit()
            app.logger.warning("persist_tickets recovered on retry for %s", sid)
        finally:
            conn.close()
    return st


def get_baseline():
    if "model" not in _BASELINE:
        _BASELINE["model"] = Responder(
            "baseline", [q for q, a in GENERIC_KB], [a for q, a in GENERIC_KB]
        )
    return _BASELINE["model"]


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


def load_store_model(sid, st=None):
    """Return this session's trained model, loading it from disk if this worker
    doesn't hold it in memory.

    With multiple gunicorn workers, training happens in one process. Any later
    request (page or API) may land on a worker with an empty cache, so every
    reader must be able to rebuild the model from <sid>.pkl.
    """
    st = st if st is not None else state(sid)
    model = st.get("store_model")
    if model is not None:
        return model
    pkl = MODEL_DIR / f"{sid}.pkl"
    if pkl.exists():
        try:
            model = Responder.load(pkl)
            st["store_model"] = model
            return model
        except Exception as exc:
            app.logger.error("model load failed for %s: %r", sid, exc)
    return None


def prune_models(keep=200):
    """Each session trains its own model (~430 KB). Keep the newest N and drop
    the rest so a long-lived container can't fill its disk."""
    try:
        files = sorted(MODEL_DIR.glob("*.pkl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            old.unlink(missing_ok=True)
    except Exception as exc:
        app.logger.error("prune_models failed: %r", exc)


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
    if load_store_model(sid, st) is None:
        return redirect("/train")
    return render_template("step3.html", count=len(st["tickets"]), sid=sid)


@app.get("/teach")
def teach_page():
    """Step 5: correct the model live and watch confidence flip."""
    sid = ensure_session()
    st = state(sid)
    if load_store_model(sid, st) is None:
        return redirect("/train")
    return render_template("teach.html", count=len(st.get("tickets") or []))


@app.get("/pricing")
def step5_page():
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
    st = persist_tickets(sid, pairs, False)
    st["store_model"] = None  # invalidate any previously trained model
    db().execute("UPDATE sessions SET source='paste' WHERE id=?", (sid,))
    db().commit()
    return jsonify(ok=True, count=len(pairs), reviewed=False, pii=has_pii(pairs))


@app.post("/api/sample")
def api_sample():
    sid = ensure_session()
    st = state(sid)
    st = persist_tickets(sid, list(SAMPLE_TICKETS), False)
    st["store_model"] = None
    db().execute("UPDATE sessions SET source='sample' WHERE id=?", (sid,))
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
    st = persist_tickets(sid, pairs, False)
    st["store_model"] = None
    db().execute("UPDATE sessions SET source='upload' WHERE id=?", (sid,))
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
    st = persist_tickets(sid, cleaned, True)
    st["store_model"] = None  # retrain from the reviewed set
    db().execute("UPDATE sessions SET source='reviewed' WHERE id=?", (sid,))
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
        # persist trained model to disk so any worker (or a restart) can reload it
        try:
            m.save(MODEL_DIR / f"{sid}.pkl")
            prune_models()
        except Exception as exc:
            app.logger.error("model save failed for %s: %r", sid, exc)
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
    store = load_store_model(sid, st)
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
    fb = c.execute("SELECT COUNT(*) FROM feedback_events WHERE verdict='down'").fetchone()[0]
    return jsonify(sessions=s["n"], trained=s["t"], tickets=s["tickets"], intents=i["n"], corrections=fb)


@app.post("/api/predict")
def api_predict():
    """Serve an answer from the trained store model (the live widget endpoint)."""
    sid = ensure_session()
    st = state(sid)
    store = load_store_model(sid, st)
    if store is None:
        return jsonify(ok=False, error="No trained model"), 400
    q = (request.json or {}).get("question", "").strip() if request.is_json else ""
    if not q:
        return jsonify(ok=False, error="question required"), 400
    answer, score, top3 = store.predict(q)
    return jsonify(ok=True, question=q, answer=answer, score=round(score, 3),
                   alternatives=[{"answer": a, "score": round(s, 3)} for a, s in top3[1:]])


@app.post("/api/feedback")
def api_feedback():
    """The self-improving loop: capture a correction and retrain the store model.

    Body: {question, verdict: 'up'|'down', corrected_answer?: str}
    A thumbs-down with a corrected_answer appends the pair to the session's
    ticket set and retrains immediately (TF-IDF trains in well under a
    second), so the very next /api/predict serves the corrected answer.
    """
    sid = ensure_session()
    st = state(sid)
    data = request.json or {}
    question = (data.get("question") or "").strip()
    verdict = (data.get("verdict") or "").lower()
    corrected = (data.get("corrected_answer") or "").strip()
    predicted = (data.get("predicted_answer") or "").strip()
    if not question:
        return jsonify(ok=False, error="question required"), 400
    if verdict not in ("up", "down"):
        return jsonify(ok=False, error="verdict must be 'up' or 'down'"), 400

    retrained = 0
    if verdict == "down" and corrected:
        if not st.get("tickets"):
            return jsonify(ok=False, error="No tickets loaded; run the demo first"), 400
        # avoid duplicate training rows for the same question text
        merged = [(q, a) for q, a in st["tickets"] if _norm(q) != _norm(question)]
        merged.append((question, corrected))
        st = persist_tickets(sid, merged, st.get("reviewed", True))
        qs = [q for q, a in st["tickets"]]
        ans = [a for q, a in st["tickets"]]
        st["store_model"] = Responder("store", qs, ans)
        try:
            st["store_model"].save(MODEL_DIR / f"{sid}.pkl")
            prune_models()
            retrained = 1
        except Exception as exc:
            app.logger.error("retrain save failed for %s: %r", sid, exc)
            retrained = 0
        # immediate proof the correction took
        answer, score, _ = st["store_model"].predict(question)

    db().execute(
        "INSERT INTO feedback_events (session_id, question, predicted_answer, verdict,"
        " corrected_answer, retrained, created_at) VALUES (?,?,?,?,?,?,?)",
        (sid, question, predicted, verdict, corrected or None, retrained, time.time()),
    )
    db().commit()
    out = {"ok": True, "verdict": verdict}
    if retrained:
        out.update(retrained=True, ticket_count=len(st["tickets"]),
                   corrected_answer=answer, corrected_score=round(score, 3))
    return jsonify(out)


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
    st = persist_tickets(sid, pairs, False)
    st["store_model"] = None
    db().execute("UPDATE sessions SET source='import' WHERE id=?", (sid,))
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
