"""Central error logging for the todocore CLIs.

Idea: every invalid invocation (argparse error, unhandled exception) lands in
the `tool_errors` table. The data drives alias decisions empirically instead of
guessing what LLMs tend to confuse.

Usage:
    from tool_errors import log_error, LoggingArgumentParser

    parser = LoggingArgumentParser(description='...')  # instead of argparse.ArgumentParser
    # ... rest of the code as usual ...

    try:
        cmds[args.command](args)
    except Exception as e:
        log_error(tool='todo', command=args.command,
                  error_type=type(e).__name__, error_msg=str(e))
        raise

Source label:
- the value of TODO_AGENT, if set (lets a consumer name itself)
- 'agent' when running under an agent (CLAUDECODE=1, a generic "under an agent" signal)
- 'cli'   otherwise (a human at a shell)
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
    # A consumer can name itself via TODO_AGENT. Otherwise we fall back to a
    # generic "are we running under an agent?" signal (CLAUDECODE), and finally
    # assume a human at a shell.
    agent = os.environ.get('TODO_AGENT')
    if agent:
        return agent
    if os.environ.get('CLAUDECODE') == '1':
        return 'agent'
    return 'cli'


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
    """ArgumentParser that logs to tool_errors on `error()` before calling sys.exit.

    Inherited automatically by subparsers when used as the top-level parser.
    """

    # Tool name is set via attribute (default: script name without .py).
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
