"""Deflectly tests: run with `pip install pytest` then `pytest` from the repo root."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import app as deflectly_app  # noqa: E402
from model import Responder, answer_match, rouge_l  # noqa: E402


def test_sample_load_train_compare():
    client = deflectly_app.app.test_client()
    # load sample store
    r = client.post("/api/sample")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert d["count"] > 0

    # Train without review should be blocked
    r = client.post("/api/train")
    assert r.status_code == 400
    assert "Review" in r.get_json()["error"]

    # Approve via review (re-post sample to get pairs is destructive; instead call review with the pairs
    # we reconstruct from the module). Simpler: hit /review page is HTML, so we test via direct state.
    # Use the app's own sample data to approve.
    import app as A
    with client.application.app_context():
        pairs = A.SAMPLE_TICKETS
    r = client.post("/api/review", json={"pairs": [{"question": q, "answer": a} for q, a in pairs]})
    assert r.status_code == 200
    d2 = r.get_json()
    assert d2["ok"] is True
    assert d2["reviewed"] is True
    assert d2["count"] == len(pairs)

    # train (SSE stream collected by test client)
    r = client.post("/api/train")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Model trained" in body

    # compare
    r = client.post("/api/compare")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert d["summary"]["store_acc"] == 1.0
    assert d["summary"]["baseline_acc"] == 0.0


def test_train_requires_review():
    client = deflectly_app.app.test_client()
    client.post("/api/sample")
    r = client.post("/api/train")
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_predict_endpoint():
    client = deflectly_app.app.test_client()
    client.post("/api/sample")
    import app as A
    pairs = [{"question": q, "answer": a} for q, a in A.SAMPLE_TICKETS]
    client.post("/api/review", json={"pairs": pairs})
    # consume the SSE training stream fully so the model is stored
    r = client.post("/api/train")
    r.get_data(as_text=True)
    r = client.post("/api/predict", json={"question": "what is the cost per user for the growth plan?"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert d["answer"] is not None


def test_feedback_loop_retrains():
    """Thumbs-down with a correction retrains the model so predict serves it."""
    client = deflectly_app.app.test_client()
    client.post("/api/sample")
    import app as A
    pairs = [{"question": q, "answer": a} for q, a in A.SAMPLE_TICKETS]
    client.post("/api/review", json={"pairs": pairs})
    client.post("/api/train").get_data(as_text=True)

    # validation errors
    assert client.post("/api/feedback", json={}).status_code == 400
    assert client.post("/api/feedback", json={"question": "x", "verdict": "sideways"}).status_code == 400

    # ask a question the store corpus can't answer well
    r = client.post("/api/predict", json={"question": "do you ship to the moon overnight?"})
    d = r.get_json()

    # correct it with a brand-new answer
    r = client.post(
        "/api/feedback",
        json={"question": "do you ship to the moon overnight?",
              "predicted_answer": d.get("answer"), "verdict": "down",
              "corrected_answer": "We ship to the moon overnight for $20."},
    )
    assert r.status_code == 200
    d2 = r.get_json()
    assert d2["ok"] is True
    assert d2["retrained"] is True
    assert d2["ticket_count"] == len(pairs) + 1

    # the retrained model now serves the corrected answer
    r = client.post("/api/predict", json={"question": "do you ship to the moon overnight?"})
    assert "moon" in r.get_json()["answer"].lower()

    # stats count the correction
    r = client.get("/api/stats")
    assert r.get_json()["corrections"] >= 1


def test_teach_page_gate_and_render():
    """Teach page redirects without a trained model, renders with one."""
    client = deflectly_app.app.test_client()
    assert client.get("/teach").status_code == 302

    client.post("/api/sample")
    import app as A
    client.post("/api/review", json={"pairs": [{"question": q, "answer": a} for q, a in A.SAMPLE_TICKETS]})
    client.post("/api/train").get_data(as_text=True)
    r = client.get("/teach")
    assert r.status_code == 200
    assert b"Teach it" in r.data


def test_state_survives_worker_switch():
    """Regression: with >1 gunicorn worker, in-memory STATE is per-process.

    A request can land on a worker that never saw this session's tickets. State
    must hydrate from SQLite, otherwise /api/review 400s with 'No tickets loaded'
    (observed in production on Railway with --workers 2).
    """
    client = deflectly_app.app.test_client()
    r = client.post("/api/sample")
    assert r.status_code == 200

    # Simulate the next request hitting a different worker: drop all process-local state.
    deflectly_app.STATE.clear()

    pairs = [{"question": q, "answer": a} for q, a in deflectly_app.SAMPLE_TICKETS]
    r = client.post("/api/review", json={"pairs": pairs})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["reviewed"] is True

    deflectly_app.STATE.clear()  # again: new worker must still be able to train
    r = client.post("/api/train")
    assert r.status_code == 200
    assert "Model trained" in r.get_data(as_text=True)

    deflectly_app.STATE.clear()  # and the correction loop must still work
    r = client.post("/api/predict", json={"question": "how long is the free trial?"})
    assert r.status_code == 200
    r = client.post("/api/feedback", json={
        "question": "how long is the free trial?", "verdict": "down",
        "corrected_answer": "The free trial runs 21 days.",
    })
    assert r.status_code == 200 and r.get_json()["retrained"] is True


def test_review_then_train_survives_stale_cache():
    """Regression: worker B cached state while reviewed was still False.

    Observed in production (Railway, --workers 2): /api/review succeeded on one
    worker, /api/train hit the other and 400'd with 'Review and approve your
    tickets before training'. tickets/reviewed must be read fresh from SQLite,
    not from a process-local cache — simulate two workers with two caches.
    """
    client = deflectly_app.app.test_client()
    original = deflectly_app.STATE
    cacheA, cacheB = {}, {}
    try:
        # Worker A handles the sample load.
        deflectly_app.STATE = cacheA
        assert client.post("/api/sample").status_code == 200

        # Worker B handles the review (its own cache, then back to A).
        deflectly_app.STATE = cacheB
        pairs = [{"question": q, "answer": a} for q, a in deflectly_app.SAMPLE_TICKETS]
        assert client.post("/api/review", json={"pairs": pairs}).status_code == 200

        # Worker A is back for /api/train with the cache it built before review.
        deflectly_app.STATE = cacheA
        r = client.post("/api/train")
        assert r.status_code == 200, f"stale cache broke training: {r.get_json()}"
        assert "Model trained" in r.get_data(as_text=True)
    finally:
        deflectly_app.STATE = original


def test_compare_page_reloads_model_on_other_worker():
    """Regression: /compare (page) must rebuild the model from disk on a worker
    that never trained it. Observed on Railway: the page 302'd to /train even
    though the session had a trained model, so the demo looked broken."""
    client = deflectly_app.app.test_client()
    client.post("/api/sample")
    pairs = [{"question": q, "answer": a} for q, a in deflectly_app.SAMPLE_TICKETS]
    client.post("/api/review", json={"pairs": pairs})
    client.post("/api/train").get_data(as_text=True)

    # A different worker: no in-memory model for this session.
    deflectly_app.STATE.clear()

    r = client.get("/compare")
    assert r.status_code == 200, f"compare page did not reload the model: {r.status_code}"

    # and the API path too
    r = client.post("/api/compare")
    assert r.status_code == 200 and r.get_json()["summary"]["store_acc"] == 1.0


def test_correction_visible_across_workers():
    """Regression: after a correction on worker B, worker A must not serve its stale model.

    Caught by scripts/smoke.py against the live 2-worker deploy: /api/feedback reported
    retrained=true, yet the next /api/predict returned the old answer with an unchanged
    score, because worker A still had the pre-correction model in its cache.
    """
    client = deflectly_app.app.test_client()
    original = deflectly_app.STATE

    def train_current():
        client.post("/api/sample")
        pairs = [{"question": q, "answer": a} for q, a in deflectly_app.SAMPLE_TICKETS]
        client.post("/api/review", json={"pairs": pairs})
        client.post("/api/train").get_data(as_text=True)

    q = "do you offer a nonprofit discount?"
    correction = "Yes, nonprofits get 30% off any plan."
    try:
        deflectly_app.STATE = cache_a = {}
        train_current()

        # worker A answers once, caching the model
        r = client.post("/api/predict", json={"question": q})
        assert r.status_code == 200
        stale_answer = r.get_json()["answer"]
        assert stale_answer != correction

        # worker B receives the correction and retrains
        deflectly_app.STATE = cache_b = {}
        r = client.post("/api/feedback", json={"question": q, "verdict": "down",
                                               "corrected_answer": correction})
        assert r.status_code == 200 and r.get_json()["retrained"] is True

        # worker A must not answer with its pre-correction model
        deflectly_app.STATE = cache_a
        r = client.post("/api/predict", json={"question": q})
        assert r.status_code == 200
        assert r.get_json()["answer"].strip() == correction, (
            f"worker served a stale model: {r.get_json()['answer'][:80]}")
    finally:
        deflectly_app.STATE = original


def test_healthz():
    client = deflectly_app.app.test_client()
    r = client.get("/api/healthz")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_model_save_load(tmp_path):
    r = Responder(
        "test",
        ["what is the cost per user for the growth plan?"],
        ["Growth is $49 per user per month, billed annually."],
    )
    p = tmp_path / "test.pkl"
    r.save(p)
    loaded = Responder.load(p)
    ans, score, _ = loaded.predict("what is the cost per user for the growth plan?")
    assert "$49 per user" in ans


def test_answer_match_and_rouge():
    assert answer_match("Hello world", "hello world") == 1
    assert answer_match("Hello world", "goodbye") == 0
    assert rouge_l("hello world", "hello world") == 1.0
    assert rouge_l("hello world", "goodbye mars") == 0.0
