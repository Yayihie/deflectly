#!/usr/bin/env python3
"""End-to-end smoke test against a running Deflectly server.

Exercises the whole product loop over HTTP: load tickets, approve them, train,
compare against the baseline, then use the correction loop and assert the
corrected answer is actually served back.

    python3 scripts/smoke.py http://127.0.0.1:8451
    python3 scripts/smoke.py https://deflectly-production.up.railway.app

Exits non-zero on the first failed check. Intended for CI (two gunicorn workers)
and for verifying a live deployment after a deploy.
"""
import json
import sys
import urllib.error
import urllib.request
import http.cookiejar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from data.sample_data import SAMPLE_TICKETS  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8451").rstrip("/")
failures = []


def check(name, ok, detail: object = ""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  — ' + str(detail) if detail and not ok else ''}")
    if not ok:
        failures.append(name)
    return ok


def main():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def post(path, payload=None, expect=200):
        data = json.dumps(payload).encode() if payload is not None else b""
        try:
            r = opener.open(urllib.request.Request(
                BASE + path, data=data, method="POST",
                headers={"Content-Type": "application/json"}), timeout=60)
            return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def get(path):
        try:
            r = opener.open(BASE + path, timeout=60)
            return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    code, body = get("/api/healthz")
    try:
        healthy = code == 200 and json.loads(body).get("ok") is True
    except json.JSONDecodeError:
        healthy = False
    check("healthz responds", healthy, f"{code} {body[:80]}")

    code, body = get("/")
    check("landing page renders", code == 200 and "Deflectly" in body, code)

    code, body = post("/api/sample")
    check("load sample tickets", code == 200 and json.loads(body)["count"] == len(SAMPLE_TICKETS), body[:80])

    pairs = [{"question": q, "answer": a} for q, a in SAMPLE_TICKETS]
    code, body = post("/api/review", {"pairs": pairs})
    check("approve reviewed pairs", code == 200 and json.loads(body)["reviewed"] is True, body[:80])

    # train streams SSE; the stream must be drained or the model is never persisted
    code, body = post("/api/train")
    check("train completes", code == 200 and "Model trained" in body, body[-120:])

    code, body = post("/api/compare")
    ok = code == 200
    summary = json.loads(body)["summary"] if ok else {}
    check("compare returns scores", ok and summary.get("store_acc") == 1.0 and summary.get("baseline_acc", 1) < 1.0,
          summary)

    # a state-dependent page must render on whichever worker answers
    # (the heading contains <em>, so match a substring that survives the markup)
    code, body = get("/compare")
    check("compare page renders (worker switch)", code == 200 and "Generic bot vs" in body, code)
    code, body = get("/teach")
    check("teach page renders", code == 200 and "Teach it" in body, code)

    question = "do you offer a nonprofit discount?"
    code, body = post("/api/predict", {"question": question})
    before = json.loads(body)["score"] if code == 200 else None

    correction = "Yes, nonprofits get 30% off any plan — email nonprofit@example.com."
    code, body = post("/api/feedback", {"question": question, "verdict": "down",
                                        "corrected_answer": correction})
    check("correction retrains", code == 200 and json.loads(body).get("retrained") is True, body[:120])

    code, body = post("/api/predict", {"question": question})
    after = json.loads(body)
    check("corrected answer is served", code == 200 and after["answer"].strip() == correction.strip(),
          after.get("answer"))
    check("confidence moved on the same question", after["score"] != before, f"{before} -> {after['score']}")

    print()
    if failures:
        print(f"{len(failures)} check(s) failed against {BASE}: {', '.join(failures)}")
        return 1
    print(f"all checks passed against {BASE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
