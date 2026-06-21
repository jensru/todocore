"""Shared DB module: single source of truth for the todocore SQLite database.

All tools import from here:
    from todocore.db import open_db, DB_PATH

The DB path is ENV-configurable. Resolution order:
    1. TODO_DB_PATH                        (canonical)
    2. ~/.local/share/todocore/todos.db    (default)

The parent directory is created on demand.
"""

import os
import sqlite3
from pathlib import Path

_DEFAULT_DB = Path.home() / ".local/share/todocore/todos.db"


def _resolve_db_path() -> Path:
    """Return TODO_DB_PATH if set, otherwise the default path."""
    env = os.getenv("TODO_DB_PATH")
    return Path(env) if env else _DEFAULT_DB


DB_PATH = _resolve_db_path()


def open_db(ensure_tables=None, foreign_keys=False):
    """Open the todos DB with WAL mode and a Row factory.

    Args:
        ensure_tables: Optional callable(conn) for schema setup/migration.
        foreign_keys: Set PRAGMA foreign_keys=ON.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    if ensure_tables:
        ensure_tables(conn)
    return conn
