"""Standalone schema bootstrap for todocore.

ensure_schema(conn) creates every table the CLI needs, idempotently, so a fresh
standalone DB works without any host application (no index.py, no memory.db).

Extracted from the agent-os index.py DDL. Kept in sync with the additive
ALTER-migrations in todo.py._ensure_table (which run afterwards for legacy DBs).
"""

import sqlite3


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create todos / counter / entries / entries_fts (+ triggers) if missing."""
    conn.executescript("""
        -- === TODOS (Primary Store) ===
        CREATE TABLE IF NOT EXISTS todos (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            priority TEXT DEFAULT 'medium',
            category TEXT DEFAULT '',
            deadline TEXT DEFAULT '',
            scheduled TEXT DEFAULT '',
            repeat TEXT DEFAULT '',
            created TEXT NOT NULL,
            done_date TEXT DEFAULT '',
            context TEXT DEFAULT '',
            workstream TEXT DEFAULT '',
            deadline_type TEXT DEFAULT 'soft',
            body TEXT DEFAULT '',
            crm_id TEXT DEFAULT '',
            parent_id TEXT DEFAULT '',
            external_id TEXT DEFAULT '',
            external_system TEXT DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status);
        CREATE INDEX IF NOT EXISTS idx_todos_workstream ON todos(workstream);

        -- === COUNTER (legacy auto-increment ids for repeat todos) ===
        CREATE TABLE IF NOT EXISTS counter (
            key TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        );

        -- === SEARCH INDEX ===
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            source_path TEXT,
            title TEXT,
            content TEXT NOT NULL,
            date TEXT,
            metadata_json TEXT,
            row_hash TEXT NOT NULL,
            indexed_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_entries_type ON entries(type);
        CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date);

        -- === FTS5 ===
        CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
            title, content, type,
            content=entries, content_rowid=rowid,
            tokenize='unicode61 remove_diacritics 2'
        );

        -- FTS sync triggers
        CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
            INSERT INTO entries_fts(rowid, title, content, type)
            VALUES (new.rowid, new.title, new.content, new.type);
        END;

        CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
            INSERT INTO entries_fts(entries_fts, rowid, title, content, type)
            VALUES ('delete', old.rowid, old.title, old.content, old.type);
        END;

        CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
            INSERT INTO entries_fts(entries_fts, rowid, title, content, type)
            VALUES ('delete', old.rowid, old.title, old.content, old.type);
            INSERT INTO entries_fts(rowid, title, content, type)
            VALUES (new.rowid, new.title, new.content, new.type);
        END;
    """)
    conn.commit()
