"""Smoke tests for the todocore CLI against a fresh temp DB.

Proves the decoupling: runs with no consumer code and no pre-existing DB. The
CLI is invoked as a subprocess with TODO_DB_PATH pointed at a tmp file, so
ensure_schema() must create the schema from scratch.
"""

import os
import subprocess
import sys
from pathlib import Path


def _run(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "TODO_DB_PATH": str(db_path)}
    return subprocess.run(
        [sys.executable, "-m", "todocore.todo", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parent.parent),
    )


def test_full_lifecycle(tmp_path):
    db = tmp_path / "smoke.db"
    assert not db.exists()  # fresh DB: proves ensure_schema creates it

    # add
    r = _run(db, "add", "Hallo Welt")
    assert r.returncode == 0, r.stderr
    assert "hallo-welt" in r.stdout

    assert db.exists()  # ensure_schema created the DB on first call

    # list
    r = _run(db, "list")
    assert r.returncode == 0, r.stderr
    assert "Hallo Welt" in r.stdout

    # search
    r = _run(db, "search", "Hallo")
    assert r.returncode == 0, r.stderr
    assert "Hallo Welt" in r.stdout

    # today (must not crash without a workday config)
    r = _run(db, "today")
    assert r.returncode == 0, r.stderr

    # done
    r = _run(db, "done", "hallo-welt")
    assert r.returncode == 0, r.stderr
    assert "DONE" in r.stdout

    # done is reflected in list --status erledigt
    r = _run(db, "list", "--status", "erledigt")
    assert r.returncode == 0, r.stderr
    assert "Hallo Welt" in r.stdout


def test_done_event_noop_without_hook(tmp_path):
    """done with an external_id but no hook configured must stay green (no-op)."""
    db = tmp_path / "noop.db"
    r = _run(db, "add", "Linked task", "--external-id", "CRM-001")
    assert r.returncode == 0, r.stderr
    r = _run(db, "done", "linked-task")
    assert r.returncode == 0, r.stderr
    assert "DONE" in r.stdout
