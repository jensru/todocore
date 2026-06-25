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


def test_update_parent_and_body_together(tmp_path):
    """Regression: `update --parent X --body "..."` must re-parent AND set the
    body in one call. A reported bug had the combination only detach (parent_id
    cleared) instead of moving to X. Both field orders are checked."""
    db = tmp_path / "parentbody.db"
    for name in ("Parent A", "Parent B"):
        assert _run(db, "add", name).returncode == 0
    assert _run(db, "add", "Kind", "--parent", "parent-a").returncode == 0

    # --parent before --body
    r = _run(db, "update", "kind", "--parent", "parent-b", "--body", "neu")
    assert r.returncode == 0, r.stderr
    show = _run(db, "show", "kind")
    assert "parent-b" in show.stdout, show.stdout   # moved, not detached
    assert "parent-a" not in show.stdout, show.stdout
    assert "neu" in show.stdout, show.stdout         # body applied

    # reverse order: --body before --parent, move back to A
    r = _run(db, "update", "kind", "--body", "neu2", "--parent", "parent-a")
    assert r.returncode == 0, r.stderr
    show = _run(db, "show", "kind")
    assert "parent-a" in show.stdout, show.stdout
    assert "neu2" in show.stdout, show.stdout

    # detach + body together: parent cleared, body still set
    r = _run(db, "update", "kind", "--parent", "", "--body", "weg")
    assert r.returncode == 0, r.stderr
    show = _run(db, "show", "kind")
    assert "weg" in show.stdout, show.stdout
    # no active parent line anymore
    assert "Parent: parent" not in show.stdout, show.stdout


def test_update_repeat_set_and_clear(tmp_path):
    """Recurrence can be set and turned off again. `--repeat ""` clears it;
    an invalid interval is rejected."""
    db = tmp_path / "repeat.db"
    assert _run(db, "add", "Toggle", "--repeat", "weekly").returncode == 0
    assert "weekly" in _run(db, "show", "toggle").stdout

    # turn off
    r = _run(db, "update", "toggle", "--repeat", "")
    assert r.returncode == 0, r.stderr
    assert "weekly" not in _run(db, "show", "toggle").stdout

    # garbage rejected
    r = _run(db, "update", "toggle", "--repeat", "yearly")
    assert r.returncode != 0
