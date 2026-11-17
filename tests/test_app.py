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
