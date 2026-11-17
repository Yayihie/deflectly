"""End-to-end tests for the CLI: real subprocess, real files, no mocks.

The CLI is the adoptability path — someone with a helpdesk export should get answers
without running the web app — so it is tested the way a user drives it.
"""
import csv
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "deflectly_cli.py"

# Deliberately named like a real Zendesk export, and deliberately messy:
# a PII row, a one-liner, and a paraphrased question ("api key" vs "API key").
ROWS = [
    ("Ticket Subject", "Resolution Comment"),
    ("How do I rotate an API key?", "Settings, API keys, then Rotate. The old key works for 24 hours."),
    ("Do you support SAML SSO?", "SAML SSO is on the Scale plan. Configure under Security, SSO."),
    ("What is the API rate limit?", "1000 requests per minute per workspace, then 429 responses."),
    ("Can I export my data?", "Yes: Settings, Export. You get CSV within an hour."),
    ("Do you offer a free trial?", "14 days, no card required. Start from the pricing page."),
    ("Email jane.doe@acme.com about upgrades", "Contact jane.doe@acme.com for plan changes."),
    ("hi", "thanks"),
]


def run(tmp_path, *args, expect=0):
    proc = subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == expect, (
        f"`deflectly {' '.join(args)}` exited {proc.returncode} (expected {expect})\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
    return proc.stdout + proc.stderr


def write_export(tmp_path, rows=ROWS):
    path = tmp_path / "zendesk_export.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    return path


def test_train_autodetects_columns_and_drops_pii(tmp_path):
    export = write_export(tmp_path)
    out = run(tmp_path, "train", str(export))
    assert "question=Ticket Subject" in out and "answer=Resolution Comment" in out, out
    assert "trained on 5 pairs" in out, out          # 6 usable minus the PII row
    assert "1 with PII" in out, out
    assert "1 too-short" in out, out
    assert (tmp_path / ".deflectly-model.pkl").exists()
    assert (tmp_path / ".deflectly-tickets.jsonl").exists()


def test_ask_returns_the_matching_answer(tmp_path):
    run(tmp_path, "train", str(write_export(tmp_path)))
    out = run(tmp_path, "ask", "how do I rotate my api key")
    assert "Settings, API keys, then Rotate" in out, out
    assert "confidence" in out, out


def test_ask_json_output_is_machine_readable(tmp_path):
    import json
    run(tmp_path, "train", str(write_export(tmp_path)))
    out = run(tmp_path, "ask", "what is the rate limit?", "--json")
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["answer"].startswith("1000 requests per minute"), payload
    assert 0.0 <= payload["confidence"] <= 1.0


def test_teach_changes_the_answer(tmp_path):
    run(tmp_path, "train", str(write_export(tmp_path)))
    before = run(tmp_path, "ask", "do you offer nonprofit pricing?")
    out = run(tmp_path, "teach", "do you offer nonprofit pricing?", "Yes - nonprofits get 30% off.")
    assert "taught in" in out, out
    after = run(tmp_path, "ask", "do you offer nonprofit pricing?")
    assert "30% off" in after, after
    assert "30% off" not in before, before   # the correction did not already exist


def test_unreadable_export_fails_with_a_clear_message(tmp_path):
    path = tmp_path / "weird.csv"
    path.write_text("foo,bar\nhello,world\n", encoding="utf-8")
    out = run(tmp_path, "train", str(path), expect=1)
    assert "Could not find question/answer columns" in out, out
    assert "headers seen: foo, bar" in out, out
    assert "Traceback" not in out, "the user-facing failure should not be a stack trace"


def test_asking_before_training_explains_what_to_do(tmp_path):
    out = run(tmp_path, "ask", "anything", expect=1)
    assert "run `deflectly train" in out, out
