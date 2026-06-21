#!/usr/bin/env python3
"""
todocore Todos MCP Server.

A thin MCP layer over the todocore CLI. Lets any MCP client (Claude Desktop,
Claude Code, ...) create, list, complete and group todos.

Architecture principle:
  The CLI (todocore.todo) is the hero. This MCP layer ONLY calls it as a
  subprocess, it never writes to the DB directly. Same as any other consumer.

Install the optional MCP extra:  pip install -e ".[mcp]"
Run:                             python -m todocore.todos_mcp

Parent/child nesting (one level deep): add_todo(parent=...) creates a child
directly, group_todos() buckets several existing todos under one parent,
set_parent() re-parents or detaches (parent=""). The parent rolls up child
progress (show displays "children 2/5").

Exposes:
  - Tools:     write + read actions (add/update/done/cancel/... + done_today)
  - Resources: read-only context (todo://workdays, todo://dashboard,
               todo://categories; todo://workstreams kept as a legacy alias)
               -- consumed by Claude Code as @-mentions.
  - Prompts:   reusable workflows (plan-day, triage-overdue, pick-next-todo)
               -- surface as slash commands in Claude Code.

Note: Claude Desktop currently uses Tools reliably; Resources/Prompts barely
(SDK issue #1016 / no prompt UI). They pay off in Claude Code and other clients.
"""

import os
import shlex
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

TZ = ZoneInfo("Europe/Berlin")
_WEEKDAYS = {
    "montag": 0, "mo": 0, "monday": 0, "mon": 0,
    "dienstag": 1, "di": 1, "tuesday": 1, "tue": 1,
    "mittwoch": 2, "mi": 2, "wednesday": 2, "wed": 2,
    "donnerstag": 3, "do": 3, "thursday": 3, "thu": 3,
    "freitag": 4, "fr": 4, "friday": 4, "fri": 4,
    "samstag": 5, "sa": 5, "saturday": 5, "sat": 5,
    "sonntag": 6, "so": 6, "sunday": 6, "sun": 6,
}

# Invoke the CLI in-process-package as a subprocess: `python -m todocore.todo`.
# Uses the same interpreter/venv the MCP server runs in, so the package is found.
PYTHON = sys.executable
CLI_MODULE = "todocore.todo"

# Read-only context sources for resources. ENV overrides; these defaults are
# consumer-specific and simply absent in a bare install.
WORKDAYS_YAML = Path(os.getenv("TODO_WORKDAYS_CONFIG", ""))
WORKSTREAMS_YAML = WORKDAYS_YAML  # backward-compat alias
DASHBOARD_MD = Path(os.getenv("TODO_DASHBOARD_PATH", ""))

mcp = FastMCP("todocore-todos")


def _resolve_date(s: str) -> str:
    """Resolve a relative date expression to YYYY-MM-DD (Europe/Berlin).

    Accepts: empty (-> ""), an existing YYYY-MM-DD (passthrough),
    today/tomorrow/day-after (en + de), weekday names (monday..sunday, en + de),
    "+N" (in N days). Anything unknown is passed through unchanged.
    """
    if not s:
        return ""
    key = s.strip().lower()
    today = datetime.now(TZ).date()
    if key in ("heute", "today"):
        return today.isoformat()
    if key in ("morgen", "tomorrow"):
        return (today + timedelta(days=1)).isoformat()
    if key in ("uebermorgen", "übermorgen", "day-after-tomorrow"):
        return (today + timedelta(days=2)).isoformat()
    if key.startswith("+") and key[1:].isdigit():
        return (today + timedelta(days=int(key[1:]))).isoformat()
    if key in _WEEKDAYS:
        ahead = (_WEEKDAYS[key] - today.weekday()) % 7
        return (today + timedelta(days=ahead)).isoformat()
    return s.strip()


def _today_header() -> str:
    """Date anchor prepended to every read response.

    Forces the model to phrase relative terms ("tomorrow", "next week") against
    the real current date instead of its training cut-off.
    """
    dt = datetime.now(TZ)
    wd = ["Monday", "Tuesday", "Wednesday", "Thursday",
          "Friday", "Saturday", "Sunday"][dt.weekday()]
    return f"<!-- TODAY: {wd}, {dt.date().isoformat()} (Europe/Berlin) -->"


def _run(args: list[str]) -> str:
    """Run the todocore CLI with args, return stdout+stderr."""
    cmd = [PYTHON, "-m", CLI_MODULE, *args]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ},
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: todocore CLI timed out ({shlex.join(cmd)})"
    out = (res.stdout or "").strip()
    err = (res.stderr or "").strip()
    if res.returncode != 0:
        return f"ERROR (exit {res.returncode}): {err or out}"
    return out or err or "(no output)"


# === Tools: write actions ===

@mcp.tool()
def add_todo(
    name: str,
    prio: str = "sollte",
    workstream: str = "",
    scheduled: str = "",
    deadline: str = "",
    body: str = "",
    tag: str = "",
    crm: str = "",
    external_id: str = "",
    external_system: str = "",
    parent: str = "",
) -> str:
    """Create a new todo.

    Args:
        name: Todo title (required).
        prio: muss | sollte | koennte (high | medium | low). Default sollte.
        workstream: LEGACY/deprecated day-pin (writes the dead `workstream` DB
            column). Leave empty (default) for the category-first model: the
            todo's tag/category decides on which weekday it surfaces (mapped in
            the workday config). Only set this to hard-pin a todo to one workday.
        scheduled: Planned date. Prefer passing relative terms straight through
            ("tomorrow", "monday", "today", "+3"); the server resolves them
            correctly. YYYY-MM-DD also works. Do NOT compute the date yourself.
        deadline: Hard deadline, same rules as scheduled.
        body: Note / checklist body. Checklist lines as "- [ ] ..." (optional).
        tag: Category (e.g. content, distribution, ops). Primary routing field in
            the category-first model — it decides on which workday it surfaces.
        crm: CRM lead id like CRM-024, links the todo to a lead (optional).
            Shorthand for external_id=<id> + external_system="crm".
        external_id: Generic id in an external system this todo is linked to
            (optional). Pair with external_system.
        external_system: Name of the external system (e.g. "crm") the
            external_id belongs to (optional).
        parent: Id/slug of a parent todo. Set -> this todo is created as a child
            (nesting, one level deep). The parent must not itself be a child.
    """
    scheduled = _resolve_date(scheduled)
    deadline = _resolve_date(deadline)
    args = ["add", name, "--prio", prio, "--workstream", workstream]
    if scheduled:
        args += ["--scheduled", scheduled]
    if deadline:
        args += ["--deadline", deadline]
    if body:
        args += ["--body", body]
    if tag:
        args += ["--tag", tag]
    if crm:
        args += ["--crm", crm]
    if external_id:
        args += ["--external-id", external_id]
    if external_system:
        args += ["--external-system", external_system]
    if parent:
        args += ["--parent", parent]
    return _run(args)


@mcp.tool()
def update_todo(
    todo_id: str,
    name: str = "",
    prio: str = "",
    workstream: str | None = None,
    scheduled: str = "",
    deadline: str = "",
    body: str = "",
    tag: str = "",
    external_id: str = "",
    external_system: str = "",
) -> str:
    """Update fields of an existing todo. Only non-empty fields are changed.

    Use this to reschedule, re-prioritize, rename, or edit the body. To move a
    todo under a parent or detach it, use set_parent instead.

    Args:
        todo_id: Id or slug of the todo.
        name: New title (optional).
        prio: New priority muss | sollte | koennte (optional).
        workstream: LEGACY/deprecated day-pin (dead `workstream` DB column).
            Pass "" (empty) to clear the pin so the tag/category drives the
            weekday again. Omit to leave unchanged.
        scheduled: New planned date. Relative terms ("tomorrow", "monday", "+3")
            are resolved by the server. Do NOT compute the date yourself.
        deadline: New hard deadline, same rules as scheduled.
        body: New note / checklist body (optional).
        tag: New category (optional). Primary workday-routing field.
        external_id: New generic id in an external system (optional). Pair
            with external_system.
        external_system: New external system name, e.g. "crm" (optional).
    """
    scheduled = _resolve_date(scheduled)
    deadline = _resolve_date(deadline)
    args = ["update", todo_id]
    if name:
        args += ["--name", name]
    if prio:
        args += ["--prio", prio]
    if workstream is not None:
        args += ["--workstream", workstream]
    if scheduled:
        args += ["--scheduled", scheduled]
    if deadline:
        args += ["--deadline", deadline]
    if body:
        args += ["--body", body]
    if tag:
        args += ["--tag", tag]
    if external_id:
        args += ["--external-id", external_id]
    if external_system:
        args += ["--external-system", external_system]
    return _run(args)


@mcp.tool()
def done(todo_id: str) -> str:
    """Mark a todo as done.

    Args:
        todo_id: Id or slug, e.g. TODO-069 or a slug name.
    """
    return _run(["done", todo_id])


@mcp.tool()
def cancel(todo_id: str) -> str:
    """Cancel a todo (status cancelled, reversible via reopen).

    Use this instead of a hard delete, e.g. to clear a duplicate.

    Args:
        todo_id: Id or slug of the todo.
    """
    return _run(["cancel", todo_id])


@mcp.tool()
def reopen(todo_id: str) -> str:
    """Reopen a done or cancelled todo (back to open).

    Args:
        todo_id: Id or slug of the todo.
    """
    return _run(["reopen", todo_id])


@mcp.tool()
def group_todos(parent: str, children: list[str]) -> str:
    """Bucket several existing todos under one parent (nesting, one level deep).

    Each child keeps its own id, status and due date; the parent rolls up
    progress (show displays "children 2/5"). Use this to group related todos and
    track them together, without merging them into one checklist (which would
    have a single status).

    Args:
        parent: Id/slug of the collecting todo. Must not itself be a child.
        children: List of todo ids/slugs to move under the parent.
    """
    if not children:
        return "ERROR: no child ids given."
    return _run(["group", parent, *children])


@mcp.tool()
def set_parent(todo_id: str, parent: str = "") -> str:
    """Re-parent a todo or detach it from its group.

    Args:
        todo_id: Id/slug of the todo to move.
        parent: Target parent (id/slug). Empty ("") detaches to top level.
            Guards: no self-parent, no parent that is itself a child, no second
            level (a todo with children cannot become a child).
    """
    return _run(["update", todo_id, "--parent", parent])


# === Tools: read actions ===

@mcp.tool()
def list_todos(status: str = "offen", workstream: str = "") -> str:
    """List todos.

    Args:
        status: offen | erledigt | abgesagt | alle (open | done | cancelled | all).
            Default offen.
        workstream: LEGACY filter on the dead workstream day-pin column. Usually
            leave empty; routing is category-first.
    """
    args = ["list", "--status", status]
    if workstream:
        args += ["--workstream", workstream]
    return f"{_today_header()}\n{_run(args)}"


@mcp.tool()
def today() -> str:
    """Today's todos (appointments + today's workday category pool) as markdown."""
    return f"{_today_header()}\n{_run(['today', '--format', 'markdown'])}"


@mcp.tool()
def done_today() -> str:
    """Todos completed today (live from the DB, via done_date).

    Use this to recover mid-day state: what has already been finished today,
    independent of any dashboard file.
    """
    iso = datetime.now(TZ).date().isoformat()
    return (f"{_today_header()}\n"
            f"{_run(['list', '--status', 'erledigt', '--done-since', iso])}")


@mcp.tool()
def overdue() -> str:
    """Overdue todos (deadline/scheduled in the past)."""
    return f"{_today_header()}\n{_run(['overdue'])}"


@mcp.tool()
def show(todo_id: str) -> str:
    """Show a full todo entry including body, parent breadcrumb and children.

    Args:
        todo_id: Id or slug of the todo.
    """
    return f"{_today_header()}\n{_run(['show', todo_id])}"


@mcp.tool()
def search(query: str) -> str:
    """Full-text search across todos.

    Args:
        query: Search term.
    """
    return f"{_today_header()}\n{_run(['search', query])}"


@mcp.tool()
def now() -> str:
    """Current date + time (Europe/Berlin) + weekday.

    Important before any scheduling: ALWAYS resolve relative terms like "monday",
    "tomorrow", "next week" against this date, never guess.
    """
    dt = datetime.now(TZ)
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday"]
    tomorrow = (dt.date() + timedelta(days=1)).isoformat()
    return (
        f"Today is {weekdays[dt.weekday()]}, {dt.strftime('%Y-%m-%d')}, "
        f"{dt.strftime('%H:%M')} (Europe/Berlin). Tomorrow = {tomorrow}."
    )


# === Resources: read-only context ===

@mcp.resource("todo://workdays")
def res_workdays() -> str:
    """Workday config (weekday -> active categories) as YAML.

    Schema: workdays.<weekday>.categories[]. Category-first routing — a todo
    carries its category, the workday only maps which categories are active on
    which weekday.
    """
    try:
        return WORKDAYS_YAML.read_text(encoding="utf-8")
    except OSError as e:
        return f"ERROR: cannot read workday config: {e}"


@mcp.resource("todo://workstreams")
def res_workstreams() -> str:
    """Deprecated alias of todo://workdays (kept for backward compatibility)."""
    return res_workdays()


@mcp.resource("todo://dashboard")
def res_dashboard() -> str:
    """Today's dashboard (goals, workday pool, signals) as markdown."""
    try:
        return DASHBOARD_MD.read_text(encoding="utf-8")
    except OSError as e:
        return f"ERROR: cannot read dashboard: {e}"


@mcp.resource("todo://categories")
def res_categories() -> str:
    """Distinct categories currently in use, one per line."""
    return _run(["categories"])


# === Prompts: reusable workflows ===

@mcp.prompt()
def plan_day() -> str:
    """Plan today's work, revenue-first."""
    return (
        "Plan my day. Use the `today` and `overdue` tools to load today's todos "
        "and anything overdue. Then propose a focused, revenue-first plan of 5-7 "
        "doable items for today, ordered by impact. Call out anything overdue "
        "that must not slip. Keep it short."
    )


@mcp.prompt()
def triage_overdue() -> str:
    """Triage every overdue todo into reschedule / done / cancel."""
    return (
        "Triage my overdue todos. Use the `overdue` tool. For each item, "
        "recommend exactly one action: reschedule (with a concrete date), mark "
        "done, or cancel. Be decisive and give a one-line reason each."
    )


@mcp.prompt()
def pick_next_todo() -> str:
    """Pick the single best next todo to work on."""
    return (
        "What should I do next? Use the `today` tool (and `list` with "
        "status=offen if needed) to see open must-do items and today's "
        "workday. Recommend exactly ONE todo to do next, with a one-line "
        "reason why it beats the alternatives."
    )


if __name__ == "__main__":
    mcp.run()
