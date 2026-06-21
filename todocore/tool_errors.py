"""Zentrales Error-Logging für Loki-CLIs.

Idee: Jede invalid-invocation (argparse-Fehler, unbehandelte Exception)
landet in der Tabelle `tool_errors`. Daten treiben Alias-Entscheidungen
datengetrieben — statt blind zu raten, was LLMs verwechseln.

Nutzung:
    from tool_errors import log_error, LoggingArgumentParser

    parser = LoggingArgumentParser(description='...')  # statt argparse.ArgumentParser
    # ... restlicher Code wie gehabt ...

    try:
        cmds[args.command](args)
    except Exception as e:
        log_error(tool='todo', command=args.command,
                  error_type=type(e).__name__, error_msg=str(e))
        raise

Quelle (source):
- 'loki'  wenn CLAUDECODE=1 oder LOKI_AGENT=1 im Environment
- 'human' sonst (best guess)
"""

import argparse
import json
import os
import sys
from datetime import datetime

from todocore.db import open_db


def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tool_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            tool TEXT NOT NULL,
            command TEXT,
            argv_json TEXT,
            error_type TEXT,
            error_msg TEXT,
            source TEXT,
            exit_code INTEGER,
            cwd TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_errors_ts ON tool_errors (ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_errors_tool ON tool_errors (tool)")
    conn.commit()


def _detect_source():
    if os.environ.get('CLAUDECODE') == '1':
        return 'loki'
    if os.environ.get('LOKI_AGENT') == '1':
        return 'loki'
    return 'human'


def log_error(tool, command=None, argv=None, error_type=None, error_msg=None, exit_code=None):
    """Fail-safe: ein Fehler im Logging darf niemals das Hauptprogramm crashen."""
    try:
        conn = open_db()
        _ensure_table(conn)
        conn.execute(
            "INSERT INTO tool_errors (ts, tool, command, argv_json, error_type, error_msg, source, exit_code, cwd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now().isoformat(timespec='seconds'),
                tool,
                command or '',
                json.dumps(argv if argv is not None else sys.argv[1:], ensure_ascii=False),
                error_type or '',
                (error_msg or '')[:2000],
                _detect_source(),
                exit_code,
                os.getcwd(),
            )
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


class LoggingArgumentParser(argparse.ArgumentParser):
    """ArgumentParser, der bei `error()` in tool_errors loggt bevor er sys.exit aufruft.

    Wird automatisch von Subparsern geerbt, wenn als Top-Level-Parser genutzt.
    """

    # Tool-Name wird per Attribut gesetzt (Default: script name ohne .py)
    _tool_name = None

    def error(self, message):
        tool = self._tool_name or os.path.basename(sys.argv[0]).replace('.py', '')
        command = sys.argv[1] if len(sys.argv) > 1 else ''
        log_error(
            tool=tool,
            command=command,
            argv=sys.argv[1:],
            error_type='argparse',
            error_msg=message,
            exit_code=2,
        )
        super().error(message)
