"""Regression: the session secret must be identical in every worker process.

If two gunicorn workers boot at the same moment with no SECRET_KEY set and no key
file yet, a naive implementation lets each generate its own secret. Cookies signed
by worker A are then rejected by worker B, so every worker switch silently starts a
new session — the demo flow loses its tickets and looks broken.

Run: pytest tests/test_secret_key_race.py
"""
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

SNIPPET = (
    "import sys; sys.path.insert(0, %r); import app; print(app.app.secret_key)" % str(APP_DIR)
)


def test_concurrent_workers_share_one_secret(tmp_path):
    key_file = APP_DIR / ".session_key"
    backup = key_file.read_text() if key_file.exists() else None
    try:
        key_file.unlink(missing_ok=True)

        # Two processes import the app at the same time, as gunicorn workers would.
        procs = [
            subprocess.Popen([sys.executable, "-c", SNIPPET], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, cwd=str(APP_DIR))
            for _ in range(2)
        ]
        keys = []
        for p in procs:
            out, err = p.communicate(timeout=60)
            assert p.returncode == 0, f"import failed: {err[-400:]}"
            keys.append(out.strip().splitlines()[-1])

        assert keys[0] and keys[1], f"missing secret: {keys}"
        assert keys[0] == keys[1], f"workers generated different secrets: {keys}"

        # A shipped static secret would be forgeable by anyone reading the repo.
        assert keys[0] != "deflectly-demo-key-not-for-production", "static demo secret reused"
        assert len(keys[0]) >= 32, f"secret too weak: {keys[0]!r}"

        # and the key is persisted for the next boot
        assert key_file.read_text().strip() == keys[0]

        # an explicit env var always wins
        env_proc = subprocess.run(
            [sys.executable, "-c", SNIPPET], capture_output=True, text=True,
            cwd=str(APP_DIR), env={"PATH": "/usr/bin:/bin", "SECRET_KEY": "explicit-secret"},
        )
        assert env_proc.stdout.strip().splitlines()[-1] == "explicit-secret"
    finally:
        if backup is None:
            key_file.unlink(missing_ok=True)
        else:
            key_file.write_text(backup)
