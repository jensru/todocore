#!/usr/bin/env python3
"""todocore Todo CLI — generischer SQLite-Backend Kern.

Kanonische Befehle:
    todo add "Name" --prio muss --deadline 2026-03-18 --due 2026-03-17 --tag content --ctx "Kontext" --repeat daily --deadline-type hard
    todo list [--status offen] [--prio muss] [--tag content] [--limit 20]
    todo show <ID>
    todo done <ID> [--external-id X] [--crm-followup 2026-07-01] [--crm-days 7]
    todo cancel <ID>
    todo update <ID> --deadline 2026-03-20 --prio sollte --tag produkte --deadline-type soft [--external-id X | --crm X]
    todo.py group <PARENT-ID> <KIND-ID> [<KIND-ID> ...]   (mehrere Todos unter einen Parent haengen)
    todo.py today [--format markdown|table|json]
    todo.py sync-dashboard                    (Dashboard [x] Items → done + Zeilen entfernen)
    todo.py overdue
    todo.py search "suchbegriff"
    todo.py export
    todo.py stale [--days 30]

LLM-Resilienz: Aliase sind stille Rückfallnetze, kanonische Form bleibt First-Class.
    Befehls-Aliase: add (new, create) | list (ls) | show (view, get)
                    done (close, complete, finish) | cancel (rm, remove, delete)
                    update (edit, set)
    Flag-Aliase:    --prio (--priority, -p) | --tag (--cat, --category)
                    --workstream (--ws, --workday) | --due (--scheduled)
                    --body (--note, --notes, --desc, --description)

IDs: Slugs aus dem Namen (z.B. "boerdi-bauen"). Alte TODO-NNN IDs funktionieren weiter.
Status: open|done|cancelled (kanonisch; dt. Aliase offen|erledigt|abgesagt akzeptiert)
Prio: high|medium|low (kanonisch; dt. Aliase muss|sollte|könnte akzeptiert)
Anzeige-Sprache via LOKI_LANG (en default, de = deutsche Status-/Prio-Labels)

Workdays: monday | tuesday | wednesday | thursday | friday | saturday | sunday
    (frueher "Workstream"). Kategorie-first: ein Todo traegt seine category, der
    Workday mappt nur, welche Kategorien an welchem Wochentag aktiv sind.
    Das DB-Feld 'workstream' ist abgeloest und bleibt leer (tot, aber vorhanden).
Config: packs/daily/config/workstreams.yaml (Dateiname bleibt, Schema = workdays.<weekday>.categories)

Done-Event (transport-agnostisch, generisch):
    Ein Todo kann via --external-id X (--external-system Y) mit einem externen System
    verknüpft werden (add/update). --crm X ist ein generischer Alias fuer
    --external-id X --external-system crm. Beim `done` feuert todo ein best-effort
    'done'-Event:
      1. TODO_DONE_WEBHOOK=<url>      -> POST JSON (event, todo, external_id, external_system)
      2. TODO_DONE_HOOK=modul:func    -> in-process Hook(conn, todo, args)
      3. sonst                        -> no-op
    Der Kern selbst kennt KEINE Consumer-Logik (z.B. CRM). Solche Logik lebt im
    Consumer (z.B. dem Loki Personality-Pack) und haengt sich per TODO_DONE_HOOK ein.
    --crm-days / --crm-followup bleiben als generische Hook-Parameter am done erhalten.

Body-Konvention (Dashboard-Rendering):
    Body wird unter dem Todo eingerückt gerendert. Drei Modi:
    - Checklist-Body (`- [ ]` / `- [x]`)         → komplett als Sub-Liste
    - Kurzer Freitext (≤3 Zeilen, ≤200 Zeichen)  → komplett eingerückt
    - Langer Freitext                            → Preview + "_(mehr: todo.py show <id>)_"

    Beispiel Checklist-Body:
        todo.py add "Marketing-Tests" --body "$(printf -- '- [ ] Item A\\n- [ ] Item B')"

    Nutze Checklists statt mehrerer paralleler Todos, wenn die Items zusammen
    erledigt werden sollen (1 ID, 1 Status, 1 Zeile auf dem Dashboard mit Sub-Items).

Parent/Child-Nesting (seit 2026-06-20):
    Echtes Umhaengen, EINE Ebene tief. Jedes Kind behaelt eigene ID, Status, Faelligkeit.
    Der Parent rollt den Fortschritt hoch (show zeigt "Kinder (2/5)").
        todo.py add "Kind" --parent PARENT-ID      (direkt als Kind anlegen)
        todo.py update KIND --parent PARENT-ID      (bestehendes Todo umhaengen)
        todo.py update KIND --parent ""             (aus Gruppe loesen auf Top-Level)
        todo.py group PARENT KIND-A KIND-B ...       (mehrere in einem Rutsch buendeln)
    Guards (Exit 2): Self-Parent, fehlender Parent, mehr als eine Ebene (Kind unter Kind,
    Parent-mit-Kindern verschachteln). Letztes Kind erledigt → Hinweis (kein Auto-Close).
    Parent gecancelt → Kinder werden auf Top-Level geloest, verwaisen nie.
    Wann Nesting vs. Checklist: Checklist, wenn die Items zusammen als 1 Status erledigt
    werden. Nesting, wenn jedes Kind eigene Faelligkeit/eigenen Status braucht aber unter
    einem Sammel-Todo gefuehrt werden soll.

Fehler-Logging: argparse-Fehler und Exceptions landen in tool_errors-Tabelle
(memory.db). Source 'loki' wenn CLAUDECODE=1 gesetzt, sonst 'human'.
Auswertung: SELECT tool, command, error_msg, COUNT(*) FROM tool_errors
            GROUP BY tool, command, error_msg ORDER BY COUNT(*) DESC;
"""

import argparse
import hashlib
import os
import re
import sqlite3
import sys
import yaml
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from todocore.db import open_db as _shared_open_db, DB_PATH
from todocore.tool_errors import LoggingArgumentParser, log_error
from todocore.messages import t as _t_raw
WORKSTREAMS_CONFIG = Path(os.getenv(
    "LOKI_WORKSTREAMS_CONFIG",
    str(Path(__file__).resolve().parent.parent / "packs/daily/config/workstreams.yaml"),
))
# Workday-Keys = Wochentage (frueher freie Workstream-Namen). Fallback, falls die
# YAML-Config fehlt. Die DB-Spalte heisst weiter 'workstream' (tot, NICHT umbenannt).
VALID_WORKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
VALID_WORKSTREAMS = VALID_WORKDAYS  # Rueckwaerts-Alias

# --- i18n: canonical enum values are English; German is accepted as input. ---
# Stored in the DB: status open|done|cancelled, priority high|medium|low.
# Display language for status/priority labels (Loki sets LOKI_LANG=de).
LANG = os.getenv('LOKI_LANG', 'en')

STATUS_OPEN, STATUS_DONE, STATUS_CANCELLED = 'open', 'done', 'cancelled'
VALID_STATUS = [STATUS_OPEN, STATUS_DONE, STATUS_CANCELLED]
PRIO_HIGH, PRIO_MEDIUM, PRIO_LOW = 'high', 'medium', 'low'
VALID_PRIO = [PRIO_HIGH, PRIO_MEDIUM, PRIO_LOW]

# Input normalization (case-insensitive): German + English -> canonical English.
STATUS_ALIAS = {
    'open': 'open', 'offen': 'open',
    'done': 'done', 'erledigt': 'done',
    'cancelled': 'cancelled', 'canceled': 'cancelled', 'abgesagt': 'cancelled',
}
PRIO_ALIAS = {
    'high': 'high', 'muss': 'high', 'must': 'high',
    'medium': 'medium', 'sollte': 'medium', 'should': 'medium',
    'low': 'low', 'könnte': 'low', 'koennte': 'low', 'koennen': 'low',
    'koennt': 'low', 'could': 'low',
}

# Display labels per language (canonical value -> shown label).
STATUS_LABELS = {
    'en': {'open': 'open', 'done': 'done', 'cancelled': 'cancelled'},
    'de': {'open': 'offen', 'done': 'erledigt', 'cancelled': 'abgesagt'},
}
PRIO_LABELS = {
    'en': {'high': 'high', 'medium': 'medium', 'low': 'low'},
    'de': {'high': 'muss', 'medium': 'sollte', 'low': 'könnte'},
}

def _resolve_prio(prio):
    """Normalize a priority input (en or de) to the canonical English value."""
    if prio is None:
        return None
    return PRIO_ALIAS.get(prio.lower(), prio.lower())


def _resolve_status(status):
    """Normalize a status input (en or de) to canonical English.

    Passes 'all'/'alle' and None through unchanged (list-filter sentinels).
    """
    if status is None:
        return None
    s = status.lower()
    if s in ('all', 'alle'):
        return s
    return STATUS_ALIAS.get(s, s)


def _t(key, **kwargs):
    """Localized user-facing message in the active LANG (en fallback)."""
    return _t_raw(key, LANG, **kwargs)


def _prio_label(value):
    return PRIO_LABELS.get(LANG, PRIO_LABELS['en']).get(value, value)


def _status_label(value):
    return STATUS_LABELS.get(LANG, STATUS_LABELS['en']).get(value, value)


# --- DB ---

def _open_db():
    return _shared_open_db(ensure_tables=_ensure_table)


def _ensure_table(conn):
    """Schema sicherstellen + additive Laufzeit-Migration (idempotent).

    Zuerst legt ensure_schema() alle Tabellen mit CREATE TABLE IF NOT EXISTS an,
    sodass eine frische Standalone-DB ohne Host-App (kein index.py) funktioniert.
    Danach laufen die additiven ALTER-Migrationen fuer Alt-DBs, die noch ohne
    crm_id/parent_id/external_* angelegt wurden. Der `cols and`-Check verhindert
    ein ALTER auf einer noch gar nicht existierenden Tabelle.
    """
    from todocore.schema import ensure_schema
    ensure_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(todos)").fetchall()}
    if cols and 'crm_id' not in cols:
        conn.execute("ALTER TABLE todos ADD COLUMN crm_id TEXT DEFAULT ''")
        conn.commit()
    if cols and 'parent_id' not in cols:
        conn.execute("ALTER TABLE todos ADD COLUMN parent_id TEXT DEFAULT ''")
        conn.commit()
    # Generischer Link nach aussen (Phase D): external_id + external_system entkoppeln die
    # Done-Rueckkopplung von CRM. crm_id bleibt als Spiegel fuer Backward-Compat befuellt.
    if cols and 'external_id' not in cols:
        conn.execute("ALTER TABLE todos ADD COLUMN external_id TEXT DEFAULT ''")
        conn.commit()
    if cols and 'external_system' not in cols:
        conn.execute("ALTER TABLE todos ADD COLUMN external_system TEXT DEFAULT ''")
        conn.commit()


def _dict_from_row(row):
    """Convert sqlite3.Row to dict for compatibility."""
    if row is None:
        return None
    return dict(row)


# --- Workdays (frueher "Workstreams") ---
# Schema: workdays.<weekday>.categories[]. Der Wochentag-Key IST der Workday.
# Kategorie-first: das Todo traegt seine category, der Workday mappt nur, welche
# Kategorien an dem Tag aktiv sind. Das DB-Feld 'workstream' bleibt tot/leer.

WEEKDAY_KEYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']


def _load_workdays_config():
    if WORKSTREAMS_CONFIG.exists():
        with open(WORKSTREAMS_CONFIG) as f:
            return yaml.safe_load(f)
    return None


# Rueckwaerts-Alias: alter Name, neue Implementierung.
_load_workstreams_config = _load_workdays_config


def _valid_workdays():
    """Gueltige Workday-Keys = Wochentage, aus der Config abgeleitet.

    Faellt auf VALID_WORKDAYS (monday..sunday) zurueck, falls die Config fehlt
    oder leer ist, damit ein generischer Install ohne YAML weiter funktioniert.
    """
    config = _load_workdays_config()
    if config and config.get('workdays'):
        return list(config['workdays'].keys())
    return list(VALID_WORKDAYS)


# Rueckwaerts-Alias.
_valid_workstreams = _valid_workdays


def _today_workday():
    """Heutiger Workday-Key (monday..sunday) anhand date.today().weekday()."""
    config = _load_workdays_config()
    if not config:
        return None
    key = WEEKDAY_KEYS[date.today().weekday()]
    if key in config.get('workdays', {}):
        return key
    return None


# Rueckwaerts-Alias.
_today_workstream = _today_workday


# --- ID ---

_UMLAUT_MAP = str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss',
                              'Ä': 'ae', 'Ö': 'oe', 'Ü': 'ue'})


def _slugify(name, max_len=50):
    """Turn a todo name into a URL-style slug."""
    s = name.translate(_UMLAUT_MAP).lower()
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    # truncate at word boundary
    if len(s) > max_len:
        s = s[:max_len].rsplit('-', 1)[0]
    return s


def _make_slug(conn, name):
    """Generate a unique slug for a new todo."""
    base = _slugify(name)
    if not base:
        base = 'todo'
    slug = base
    n = 2
    while conn.execute("SELECT 1 FROM todos WHERE id = ?", (slug,)).fetchone():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _next_id(conn):
    """Legacy: auto-increment ID. Kept for repeat-todo compat."""
    row = conn.execute("SELECT value FROM counter WHERE key = 'todo_id'").fetchone()
    n = (row['value'] + 1) if row else 1
    conn.execute("INSERT OR REPLACE INTO counter (key, value) VALUES ('todo_id', ?)", (n,))
    return f"TODO-{n:03d}"


# --- Query helpers ---

def _normalize_id(todo_id):
    """Bare numbers → TODO-NNN format."""
    if re.match(r'^\d+$', todo_id):
        return f"TODO-{int(todo_id):03d}"
    return todo_id


def _get_todo(conn, todo_id):
    todo_id = _normalize_id(todo_id)
    # Try exact match first, then uppercase (legacy TODO-NNN)
    row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    if not row:
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id.upper(),)).fetchone()
    return _dict_from_row(row)


def _get_todos(conn, status=None, prio=None, cat=None, workstream=None, limit=None):
    query = "SELECT * FROM todos WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if prio:
        query += " AND priority = ?"
        params.append(prio)
    if cat:
        query += " AND LOWER(category) = ?"
        params.append(cat.lower())
    if workstream:
        query += " AND LOWER(workstream) = ?"
        params.append(workstream.lower())
    query += " ORDER BY created DESC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    return [_dict_from_row(r) for r in conn.execute(query, params).fetchall()]


def _update_todo(conn, todo_id, **fields):
    sets = []
    params = []
    for k, v in fields.items():
        if v is not None:
            sets.append(f"{k} = ?")
            if hasattr(v, 'isoformat'):
                params.append(v.isoformat())
            else:
                params.append(str(v) if v else '')
    if not sets:
        return
    # Resolve actual ID (slug or legacy TODO-NNN)
    todo_id = _normalize_id(todo_id)
    actual = conn.execute("SELECT id FROM todos WHERE id = ?", (todo_id,)).fetchone()
    if not actual:
        actual = conn.execute("SELECT id FROM todos WHERE id = ?", (todo_id.upper(),)).fetchone()
    resolved_id = actual['id'] if actual else todo_id
    params.append(resolved_id)
    conn.execute(f"UPDATE todos SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()


# --- Hierarchie (Parent/Child, eine Ebene tief) ---

def _children(conn, parent_id, status=None):
    """Alle Kinder eines Todos, optional nach Status gefiltert, älteste zuerst."""
    q = "SELECT * FROM todos WHERE parent_id = ?"
    params = [parent_id]
    if status:
        q += " AND status = ?"
        params.append(status)
    q += " ORDER BY created"
    return [_dict_from_row(r) for r in conn.execute(q, params).fetchall()]


def _child_progress(conn, parent_id):
    """(erledigt, gesamt) über alle Kinder, oder None wenn kinderlos."""
    kids = _children(conn, parent_id)
    if not kids:
        return None
    done = sum(1 for k in kids if k.get('status') == STATUS_DONE)
    return done, len(kids)


def _validate_parent(conn, child_id, parent_ref):
    """Parent-Referenz auflösen + prüfen. Gibt die aufgelöste Parent-ID zurück.

    Konventionen:
      parent_ref None  → kein Eingriff (Aufrufer ignoriert)
      parent_ref ''    → loslösen auf Top-Level
    Guards (ValueError): Self-Parent, fehlender Parent, mehr als eine Ebene
    (Parent ist selbst Kind / Child hat selbst Kinder).
    """
    if parent_ref is None:
        return None
    if parent_ref == '':
        return ''
    p = _get_todo(conn, parent_ref)
    if not p:
        raise ValueError(f"Parent {parent_ref} nicht gefunden")
    pid = p['id']
    if pid == child_id:
        raise ValueError("Ein Todo kann nicht sein eigener Parent sein")
    if (p.get('parent_id') or ''):
        raise ValueError(f"{pid} ist selbst ein Kind, nur eine Ebene erlaubt")
    if child_id and _children(conn, child_id):
        raise ValueError(f"{child_id} hat selbst Kinder, erst auflösen")
    return pid


# --- Dedup ---

DEDUP_THRESHOLD = 0.90


def _dedup_check(conn, name):
    open_todos = _get_todos(conn, status=STATUS_OPEN)
    name_lower = name.lower()
    best = (0.0, None)
    for t in open_todos:
        existing = t.get('name', '').lower()
        ratio = SequenceMatcher(None, name_lower, existing).ratio()
        if ratio >= DEDUP_THRESHOLD and ratio > best[0]:
            best = (ratio, t)
    if best[1]:
        return best[1], best[0]
    return None, 0.0


# --- Format ---

def _format_table(todos, columns=None):
    if not columns:
        columns = ['id', 'name', 'status', 'priority', 'deadline', 'scheduled', 'category']
    headers = [c.upper() for c in columns]
    rows = []
    for t in todos:
        row = []
        for c in columns:
            val = t.get(c, '')
            if hasattr(val, 'isoformat'):
                val = val.isoformat()
            if c == 'status' and val:
                val = _status_label(val)
            elif c == 'priority' and val:
                val = _prio_label(val)
            row.append(str(val) if val else '')
        rows.append(row)

    if not rows:
        return _t('no_todos_table')

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    fmt = ' | '.join(f'{{:<{w}}}' for w in widths)
    lines = [fmt.format(*headers)]
    lines.append('-+-'.join('-' * w for w in widths))
    for row in rows:
        lines.append(fmt.format(*row))
    return '\n'.join(lines)


def _format_todos(todos, fmt='table'):
    """Dispatcher: table (Default), json, markdown."""
    if fmt == 'json':
        import json
        return json.dumps(todos, ensure_ascii=False, indent=2, default=str)
    if fmt == 'markdown':
        if not todos:
            return _t('no_todos_md')
        lines = ["| ID | Name | Status | Prio | Scheduled |",
                 "|---|---|---|---|---|"]
        for t in todos:
            lines.append(f"| {t.get('id','')} | {t.get('name','')} | "
                         f"{_status_label(t.get('status',''))} | "
                         f"{_prio_label(t.get('priority',''))} | "
                         f"{t.get('scheduled','')} |")
        return '\n'.join(lines)
    return _format_table(todos)


# --- Echtzeit-Sync: Todo → entries (FTS) ---

def _sync_todo_to_entries(conn, todo_id):
    """Sync einzelnen Todo in entries-Tabelle für FTS-Suche."""
    t = _get_todo(conn, todo_id)
    if not t:
        return
    import json
    content = t.get('name', '')
    if t.get('body'):
        content += f"\n{t['body']}"
    if t.get('context'):
        content += f"\n{t['context']}"
    row_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
    now = datetime.now().isoformat()
    metadata = json.dumps({
        "status": t.get('status', ''),
        "priority": t.get('priority', ''),
        "workstream": t.get('workstream', ''),
        "deadline": t.get('deadline', ''),
    })
    conn.execute("""
        INSERT OR REPLACE INTO entries (id, type, source_path, title, content, date, metadata_json, row_hash, indexed_at)
        VALUES (?, 'todo', '', ?, ?, ?, ?, ?, ?)
    """, (todo_id, t.get('name', ''), content, t.get('created', ''), metadata, row_hash, now))
    conn.commit()


# --- Commands ---

def _infer_workday(category, config=None):
    """Leitet den Workday (Wochentag-Key) aus einer Category ab.

    Findet den ersten Wochentag, dessen categories-Liste die Kategorie enthaelt.
    """
    if not category:
        return ''
    if not config:
        config = _load_workdays_config()
    if not config:
        return ''
    for day_key, day_config in config.get('workdays', {}).items():
        if category in (day_config or {}).get('categories', []):
            return day_key
    return ''


# Rueckwaerts-Alias.
_infer_workstream = _infer_workday


def _resolve_workstream(value):
    """Loest den Wert des --workstream/--workday-Flags auf (schreibt die tote DB-Spalte).

    Akzeptiert einen Workday-Key (monday..sunday) ODER eine Kategorie. Gibt
    (workday, kategorie_hint) zurueck. Beispiel: 'content' (Kategorie) ->
    ('monday', 'content'). Unbekanntes -> (value, None) mit Warnung statt Crash.
    """
    if not value:
        return '', None
    v = value.lower()
    if v in _valid_workdays():
        return v, None
    inferred = _infer_workday(v)
    if inferred:
        print(f"Hinweis: '{value}' ist eine Kategorie, Workday → '{inferred}'",
              file=sys.stderr)
        return inferred, v
    print(f"Warnung: '{value}' ist kein bekannter Workday {_valid_workdays()}, "
          f"speichere roh.", file=sys.stderr)
    return v, None


def cmd_add(args):
    conn = _open_db()
    if not getattr(args, 'force', False):
        dup, ratio = _dedup_check(conn, args.name)
        if dup:
            print(
                _t('add_duplicate', ratio=ratio, name=dup.get('name'), id=dup.get('id')),
                file=sys.stderr,
            )
            sys.exit(1)

    todo_id = _make_slug(conn, args.name)
    today = date.today().isoformat()

    scheduled = getattr(args, 'due', None) or args.scheduled
    tag = getattr(args, 'tag', None)

    # Workday-Pin (tote DB-Spalte 'workstream'): explizit (akzeptiert auch Kategorie-Namen)
    # > aus Category ableiten. --workstream/--workday "" = kategorie-rein, kein Auto-Pin
    # (die category steuert ueber den Workday-Mapping den Tag).
    if args.workstream == '':
        workstream = ''
    else:
        ws, cat_hint = _resolve_workstream(args.workstream)
        if not tag and cat_hint:
            tag = cat_hint
        workstream = ws or _infer_workstream(tag)

    try:
        parent_id = _validate_parent(conn, todo_id, getattr(args, 'parent', None)) or ''
    except ValueError as e:
        print(_t('parent_error', error=e), file=sys.stderr)
        sys.exit(2)

    eid, esys = _external_fields_from_args(args)
    eid = eid or ''
    esys = esys or ''
    crm_mirror = eid if esys == 'crm' else ''  # Backward-Compat: crm_id-Spiegel

    conn.execute(
        "INSERT INTO todos (id, name, status, priority, category, deadline, scheduled, "
        "repeat, created, done_date, context, workstream, deadline_type, body, crm_id, "
        "parent_id, external_id, external_system) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (todo_id, args.name, STATUS_OPEN, _resolve_prio(args.prio) or PRIO_MEDIUM,
         tag or '', args.deadline or '', scheduled or '',
         args.repeat or '', today, '', args.ctx or '',
         workstream, args.deadline_type or 'soft',
         args.body or '', crm_mirror, parent_id, eid, esys)
    )
    conn.commit()
    _sync_todo_to_entries(conn, todo_id)
    conn.close()
    print(_t('add_ok', id=todo_id, name=args.name))


def cmd_list(args):
    conn = _open_db()
    tag = getattr(args, 'tag', None)
    ws = _resolve_workstream(args.workstream)[0] if args.workstream else None
    # Limit erst nach den Post-Filtern anwenden, sonst schneidet es zu früh ab.
    status = _resolve_status(args.status)
    if status in ('all', 'alle'):
        status = None
    todos = _get_todos(conn, status=status, prio=_resolve_prio(args.prio),
                       cat=tag, workstream=ws, limit=None)
    conn.close()

    sched = getattr(args, 'scheduled', None)
    if sched:
        todos = [t for t in todos if (t.get('scheduled') or '') == sched]

    done_since = getattr(args, 'done_since', None)
    if done_since:
        todos = [t for t in todos if (t.get('done_date') or '') >= done_since]

    q = getattr(args, 'search', None)
    if q:
        ql = q.lower()
        todos = [t for t in todos if ql in (
            f"{t.get('name','')} {t.get('body','')} {t.get('category','')}").lower()]

    if args.limit:
        todos = todos[:args.limit]

    print(_format_todos(todos, getattr(args, 'format', None) or 'table'))


def _next_repeat_date(current_date, repeat_type):
    if isinstance(current_date, str):
        try:
            current_date = date.fromisoformat(current_date)
        except (ValueError, TypeError):
            current_date = date.today()
    if repeat_type == 'daily':
        return current_date + timedelta(days=1)
    elif repeat_type == 'weekly':
        return current_date + timedelta(weeks=1)
    elif repeat_type == 'monthly':
        month = current_date.month + 1
        year = current_date.year
        if month > 12:
            month = 1
            year += 1
        day = min(current_date.day, 28)
        return date(year, month, day)
    return None


def _create_repeat_todo(conn, original):
    repeat_type = original.get('repeat', '')
    if not repeat_type:
        return None

    base_date = original.get('scheduled') or original.get('deadline') or date.today()
    next_date = _next_repeat_date(base_date, repeat_type)
    if not next_date:
        return None

    todo_id = _make_slug(conn, original.get('name', 'todo'))
    today = date.today().isoformat()

    conn.execute(
        "INSERT INTO todos (id, name, status, priority, category, deadline, scheduled, "
        "repeat, created, done_date, context, workstream, deadline_type, body, crm_id, "
        "external_id, external_system) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (todo_id, original.get('name', ''), STATUS_OPEN,
         original.get('priority', PRIO_MEDIUM), original.get('category', ''),
         next_date.isoformat() if original.get('deadline') else '',
         next_date.isoformat() if original.get('scheduled') else '',
         repeat_type, today, '', original.get('context', ''),
         original.get('workstream', ''), original.get('deadline_type', 'soft'),
         original.get('body', ''), original.get('crm_id', ''),
         original.get('external_id', ''), original.get('external_system', ''))
    )
    conn.commit()

    return {
        'id': todo_id,
        'name': original.get('name', ''),
        'scheduled': next_date.isoformat() if original.get('scheduled') else '',
        'deadline': next_date.isoformat() if original.get('deadline') else '',
    }


def _resolve_external_id(t):
    """Generische External-ID eines Todos (reine external_id-Spalte, keine Heuristik)."""
    return (t.get('external_id') or '').strip().upper()


def _external_fields_from_args(args):
    """(external_id, external_system) aus CLI-Args aufloesen.

    --crm X ist ein Alias fuer --external-id X --external-system crm. Liefert (None, None)
    wenn weder --crm noch --external-id gesetzt wurden (= Feld unangetastet lassen).
    Leerer Wert ('') = loeschen.
    """
    crm = getattr(args, 'crm', None)
    eid = getattr(args, 'external_id', None)
    esys = getattr(args, 'external_system', None)
    if crm is not None:
        cv = (crm or '').upper()
        return cv, ('crm' if cv else '')
    if eid is not None:
        ev = (eid or '').upper()
        sysv = (esys or '').lower()
        if ev and not sysv:
            sysv = 'crm' if ev.startswith('CRM-') else ''
        return ev, sysv
    return None, None


# --- Done-Event (transport-agnostisch) ---
# Beim Erledigen feuert todo.py ein best-effort 'done'-Event. Reihenfolge:
#   1. TODO_DONE_WEBHOOK  -> POST JSON an eine URL (Cloud-DB-faehig)
#   2. TODO_DONE_HOOK     -> 'modul:func', in-process aufgerufen (teilt die offene Connection)
#   3. sonst              -> no-op (der generische Kern kennt keine Consumer-Logik wie CRM)
# Ein Hook-Fehler darf das erledigt NIE zurueckrollen, daher alles in try/except.

def _fire_done_event(conn, todo, args):
    webhook = os.getenv('TODO_DONE_WEBHOOK')
    hook = os.getenv('TODO_DONE_HOOK')
    try:
        if webhook:
            _post_done_webhook(webhook, todo)
        elif hook:
            _call_done_hook(hook, conn, todo, args)
        # sonst: no-op. Consumer (z.B. Loki) setzen TODO_DONE_HOOK fuer ihre Logik.
    except Exception as e:
        print(_t('done_hook_warn', error=e), file=sys.stderr)


def _post_done_webhook(url, todo):
    """Fire-and-forget POST. Ein nicht erreichbarer Empfaenger darf das done nicht kippen."""
    import json as _json
    import urllib.request
    payload = _json.dumps({
        'event': 'done',
        'todo': todo,
        'external_id': _resolve_external_id(todo),
        'external_system': (todo.get('external_system') or ''),
    }, ensure_ascii=False, default=str).encode('utf-8')
    req = urllib.request.Request(
        url, data=payload,
        headers={'Content-Type': 'application/json'}, method='POST')
    try:
        urllib.request.urlopen(req, timeout=3)
        print(_t('done_webhook', url=url))
    except Exception as e:
        print(_t('done_webhook_warn', error=e), file=sys.stderr)


def _call_done_hook(spec, conn, todo, args):
    """In-process Hook via 'modul:func'. Bekommt die offene Connection fuer Cross-Table-Touch."""
    import importlib
    mod_name, sep, func_name = spec.partition(':')
    if not (mod_name and sep and func_name):
        print(_t('done_hook_invalid', spec=spec), file=sys.stderr)
        return
    mod = importlib.import_module(mod_name)
    getattr(mod, func_name)(conn=conn, todo=todo, args=args)


def cmd_done(args):
    conn = _open_db()
    t = _get_todo(conn, args.id)
    if not t:
        print(_t('not_found', id=args.id), file=sys.stderr)
        sys.exit(1)

    # Vorher-Status merken (für Idempotenz: kein erneuter CRM-Touch bei Doppel-done)
    already_done = (t.get('status') == STATUS_DONE)

    _update_todo(conn, args.id, status=STATUS_DONE, done_date=date.today().isoformat())
    note = getattr(args, 'note', None)
    if note:
        existing = t.get('body', '') or ''
        stamp = date.today().isoformat()
        new_body = (f"{existing}\n[{stamp} erledigt] {note}").strip()
        _update_todo(conn, args.id, body=new_body)
    _sync_todo_to_entries(conn, args.id)
    print(_t('done', id=t['id'], name=t['name']))

    # Done-Event (transport-agnostisch): verknuepfter Lead/External-System wird best-effort
    # benachrichtigt. Laeuft NACH dem erledigt-Commit, kippt das erledigt nie zurueck.
    # Idempotent: bei Doppel-done (already_done) kein erneutes Feuern.
    if not already_done:
        t['status'] = STATUS_DONE
        t['done_date'] = date.today().isoformat()
        _fire_done_event(conn, t, args)

    if t.get('repeat'):
        new = _create_repeat_todo(conn, t)
        if new:
            _sync_todo_to_entries(conn, new['id'])
            next_date = new.get('scheduled') or new.get('deadline')
            print(_t('repeat', id=new['id'], name=new['name'], next=next_date))

    # Wenn dies das letzte offene Kind war → Parent kann geschlossen werden (kein Auto-Close).
    parent_id = (t.get('parent_id') or '')
    if parent_id:
        prog = _child_progress(conn, parent_id)
        if prog and prog[0] == prog[1]:
            p = _get_todo(conn, parent_id)
            if p and p.get('status') == STATUS_OPEN:
                print(_t('all_children_done', parent=parent_id))

    conn.close()


def cmd_cancel(args):
    conn = _open_db()
    t = _get_todo(conn, args.id)
    if not t:
        print(_t('not_found', id=args.id), file=sys.stderr)
        sys.exit(1)
    # Parent mit Kindern: Kinder auf Top-Level loslösen statt verwaisen lassen.
    kids = _children(conn, t['id'])
    if kids:
        for k in kids:
            _update_todo(conn, k['id'], parent_id='')
            _sync_todo_to_entries(conn, k['id'])
        print(_t('children_freed', n=len(kids)), file=sys.stderr)
    _update_todo(conn, args.id, status=STATUS_CANCELLED)
    _sync_todo_to_entries(conn, args.id)
    conn.close()
    print(_t('cancelled', id=t['id'], name=t['name']))


def cmd_reopen(args):
    conn = _open_db()
    t = _get_todo(conn, args.id)
    if not t:
        print(_t('not_found', id=args.id), file=sys.stderr)
        sys.exit(1)
    _update_todo(conn, args.id, status=STATUS_OPEN, done_date='')
    _sync_todo_to_entries(conn, args.id)
    conn.close()
    print(_t('reopened', id=t['id'], name=t['name']))


def cmd_update(args):
    conn = _open_db()
    t = _get_todo(conn, args.id)
    if not t:
        print(_t('not_found', id=args.id), file=sys.stderr)
        sys.exit(1)

    fields = {}
    if args.deadline is not None:
        fields['deadline'] = args.deadline
    due = getattr(args, 'due', None)
    if due is not None:
        fields['scheduled'] = due
    elif args.scheduled is not None:
        fields['scheduled'] = args.scheduled
    if args.prio:
        fields['priority'] = _resolve_prio(args.prio)
    tag = getattr(args, 'tag', None)
    if tag:
        fields['category'] = tag
    if args.ctx:
        fields['context'] = args.ctx
    if args.name:
        fields['name'] = args.name
    if args.repeat:
        fields['repeat'] = args.repeat
    if args.workstream is not None:
        if args.workstream == '':
            # Explizit leeren: category steuert den Tag (Workday-Mapping), nicht das tote Pin
            fields['workstream'] = ''
        else:
            ws, cat_hint = _resolve_workstream(args.workstream)
            fields['workstream'] = ws
            if cat_hint and not getattr(args, 'tag', None):
                fields['category'] = cat_hint
    if args.deadline_type:
        fields['deadline_type'] = args.deadline_type
    if args.body is not None:
        fields['body'] = args.body
    eid, esys = _external_fields_from_args(args)
    if eid is not None:
        fields['external_id'] = eid
        fields['external_system'] = esys or ''
        # crm_id-Spiegel fuer Backward-Compat (Reader die noch crm_id lesen).
        fields['crm_id'] = eid if (esys or '') == 'crm' else ''
    if getattr(args, 'parent', None) is not None:
        try:
            fields['parent_id'] = _validate_parent(conn, t['id'], args.parent) or ''
        except ValueError as e:
            print(_t('parent_error', error=e), file=sys.stderr)
            sys.exit(2)

    if fields:
        _update_todo(conn, args.id, **fields)
        _sync_todo_to_entries(conn, args.id)

    conn.close()
    print(_t('updated', id=t['id'], name=t.get('name', '')))


def cmd_group(args):
    """Mehrere Todos in einem Rutsch unter einen Parent hängen (umhängen)."""
    conn = _open_db()
    parent = _get_todo(conn, args.parent)
    if not parent:
        print(_t('not_found', id=args.parent), file=sys.stderr)
        sys.exit(1)
    if (parent.get('parent_id') or ''):
        print(_t('group_parent_is_child', id=parent['id']), file=sys.stderr)
        sys.exit(2)
    moved = []
    for cref in args.children:
        c = _get_todo(conn, cref)
        if not c:
            print(_t('group_skip_notfound', ref=cref), file=sys.stderr)
            continue
        if c['id'] == parent['id']:
            print(_t('group_skip_is_parent', id=c['id']), file=sys.stderr)
            continue
        if _children(conn, c['id']):
            print(_t('group_skip_has_children', id=c['id']), file=sys.stderr)
            continue
        _update_todo(conn, c['id'], parent_id=parent['id'])
        _sync_todo_to_entries(conn, c['id'])
        moved.append(c['id'])
    conn.close()
    print(_t('group_result', n=len(moved), id=parent['id'], name=parent['name']))
    for m in moved:
        print(_t('group_moved_item', id=m))


def cmd_workdays(args):
    """Listet die Workdays (Wochentage) + ihre Kategorien (verhindert Workday/Kategorie-Verwechslung)."""
    config = _load_workdays_config() or {}
    rows = []
    for day_key in _valid_workdays():
        cfg = config.get('workdays', {}).get(day_key, {}) or {}
        cats = ','.join(cfg.get('categories', []))
        rows.append(f"{day_key:<12} {cats}")
    print(_t('workdays_header'))
    print('\n'.join(rows))


# Rueckwaerts-Alias (CLI-Subcommand 'workstreams' bleibt nutzbar).
cmd_workstreams = cmd_workdays


def cmd_categories(args):
    """List distinct non-empty categories (read-only). For the MCP categories resource."""
    conn = _open_db()
    rows = conn.execute(
        "SELECT DISTINCT category FROM todos WHERE category != '' ORDER BY category"
    ).fetchall()
    conn.close()
    cats = [r['category'] for r in rows]
    if getattr(args, 'format', 'table') == 'json':
        import json as _json
        print(_json.dumps(cats, ensure_ascii=False))
    else:
        print('\n'.join(cats))


def cmd_overdue(args):
    conn = _open_db()
    today_iso = date.today().isoformat()
    rows = conn.execute(
        "SELECT * FROM todos WHERE status = 'open' AND deadline != '' AND deadline < ? ORDER BY deadline",
        (today_iso,)
    ).fetchall()
    conn.close()
    todos = [_dict_from_row(r) for r in rows]
    if todos:
        print(_format_table(todos))
    else:
        print(_t('overdue_none'))


def cmd_search(args):
    conn = _open_db()
    todos = _get_todos(conn, status=None)
    query = args.query.lower()
    results = []
    for t in todos:
        name = t.get('name', '').lower()
        ctx = t.get('context', '').lower()
        cat = t.get('category', '').lower()
        if query in name or query in ctx or query in cat:
            results.append(t)
        elif SequenceMatcher(None, query, name).ratio() > 0.5:
            results.append(t)
    conn.close()
    print(_format_table(results))


def _workday_categories(config, day_key):
    """Gibt die categories-Liste fuer einen Workday (Wochentag-Key) zurueck."""
    if not config or not day_key:
        return []
    day_config = config.get('workdays', {}).get(day_key, {}) or {}
    return day_config.get('categories', [])


# Rueckwaerts-Alias.
_ws_categories = _workday_categories


def cmd_today(args):
    conn = _open_db()
    today_date = date.today()
    today_iso = today_date.isoformat()
    wd = _today_workday()  # heutiger Workday-Key (monday..sunday)

    config = _load_workdays_config()
    always_visible = config.get('always_visible', []) if config else []
    wd_cats = _workday_categories(config, wd)
    # Schwelle fuer die Ueberfaellig-Warnung (config-getrieben, Default 7). Phase E: raus
    # aus dem Hardcode, generisch konfigurierbar.
    warn_days = int(config.get('overdue_warning_days', 7)) if config else 7

    open_todos = _get_todos(conn, status=STATUS_OPEN)
    conn.close()

    selected = []
    for t in open_todos:
        reasons = []
        t_ws = t.get('workstream', '')  # tote DB-Spalte, i.d.R. leer
        t_cat = t.get('category', '')
        t_sched = str(t.get('scheduled', ''))
        t_dl = str(t.get('deadline', ''))
        t_dl_type = t.get('deadline_type', 'soft')

        # Scheduled in der Zukunft → nicht über den Workday reinziehen
        future_scheduled = False
        if t_sched:
            try:
                future_scheduled = date.fromisoformat(t_sched) > today_date
            except (ValueError, TypeError):
                pass

        # Workday-Match: explizit gepinntes (totes) Feld ODER category faellt in den heutigen Workday
        if wd and t_ws == wd and not future_scheduled:
            reasons.append('workday')
        elif wd and not t_ws and t_cat and t_cat in wd_cats and not future_scheduled:
            reasons.append('workday_by_category')
        if t_sched == today_iso:
            reasons.append('scheduled')
        if t_dl:
            try:
                dl_date = date.fromisoformat(t_dl)
                if dl_date <= today_date and t_dl_type == 'hard':
                    reasons.append('hard_deadline')
                elif dl_date == today_date:
                    reasons.append('deadline_today')
            except (ValueError, TypeError):
                pass
        # Carry-over: Todo gehoert per Kategorie zum heutigen Workday, ist aber mit einem
        # scheduled-Datum in der Vergangenheit liegengeblieben. (Kategorie-first; das tote
        # workstream-Feld spielt keine Rolle mehr.)
        if t_sched and t_sched < today_iso and wd and not t_ws and t_cat and t_cat in wd_cats:
            reasons.append('overdue_workday')

        if reasons:
            t['_reasons'] = reasons
            # Layer-Zuordnung: termin > crosscutting > pool
            if any(r in reasons for r in ('scheduled', 'deadline_today', 'hard_deadline')):
                t['_layer'] = 'termin'
            elif 'overdue_workday' in reasons:
                t['_layer'] = 'crosscutting'
            else:
                t['_layer'] = 'pool'
            selected.append(t)

    prio_order = {PRIO_HIGH: 0, PRIO_MEDIUM: 1, PRIO_LOW: 2}
    selected.sort(key=lambda t: prio_order.get(t.get('priority', PRIO_MEDIUM), 1))

    warnings = []
    for t in open_todos:
        if t in selected:
            continue
        t_dl = str(t.get('deadline', ''))
        t_cat = t.get('category', '')
        t_sched = str(t.get('scheduled', ''))
        # Skip wenn explizit in die Zukunft gescheduled — dann ist es nicht vergessen
        if t_sched:
            try:
                if date.fromisoformat(t_sched) > today_date:
                    continue
            except (ValueError, TypeError):
                pass
        # Ueberfaellig auf einem ANDEREN Workday: Kategorie heute nicht aktiv, Deadline > N Tage.
        if t_dl and t_cat and t_cat not in wd_cats:
            try:
                dl_date = date.fromisoformat(t_dl)
                days_overdue = (today_date - dl_date).days
                if days_overdue > warn_days:
                    warnings.append(t)
            except (ValueError, TypeError):
                pass

    fmt = getattr(args, 'format', 'table')
    if fmt == 'json':
        import json as _json
        output = {
            'workday': wd or '',
            'workstream': wd or '',  # Rueckwaerts-Alias fuer alte Konsumenten
            'todos': selected,
            'warnings': warnings,
        }
        print(_json.dumps(output, ensure_ascii=False, default=str))
    elif fmt == 'markdown':
        _print_today_markdown(selected, wd, warnings)
    else:
        wd_label = wd or _t('today_workday_none')
        if selected:
            print(_t('today_workday', wd=wd_label))
            print()
            print(_format_table(selected))
        else:
            print(_t('today_no_todos', wd=wd_label))
        if warnings:
            print(_t('today_warn_header', n=len(warnings), warn_days=warn_days))
            for t in warnings:
                dl = t.get('deadline', '')
                print(_t('today_warn_item', id=t['id'], name=t['name'], dl=dl, cat=t.get('category')))


def _print_today_markdown(todos, workday, warnings=None):
    groups = {PRIO_HIGH: [], PRIO_MEDIUM: [], PRIO_LOW: []}
    for t in todos:
        prio = t.get('priority', PRIO_MEDIUM)
        groups.get(prio, groups[PRIO_MEDIUM]).append(t)

    print(_t('md_workday_comment', wd=workday or _t('md_workday_none')))
    for key in [PRIO_HIGH, PRIO_MEDIUM, PRIO_LOW]:
        print(f"\n### {_prio_label(key).upper()}\n")
        if groups[key]:
            for t in groups[key]:
                tid = t.get('id', '')
                name = t.get('name', '')
                reasons = t.get('_reasons', [])
                tags = []
                if 'hard_deadline' in reasons:
                    dl = t.get('deadline', '')
                    tags.append(_t('md_tag_deadline', dl=dl))
                if 'overdue_workday' in reasons:
                    tags.append(_t('md_tag_carryover'))
                tag_str = f" ({', '.join(tags)})" if tags else ''
                print(f"- [ ] {name}{tag_str} ({tid})")
        else:
            print(_t('md_empty'))

    if warnings:
        print(_t('md_carryover_header', n=len(warnings)))
        for t in warnings:
            dl = t.get('deadline', '')
            cat = t.get('category', '')
            print(_t('md_carryover_item', id=t['id'], name=t['name'], dl=dl, cat=cat))


def cmd_sync_dashboard(args):
    dashboard = Path(os.getenv(
        "LOKI_DASHBOARD_PATH",
        str(Path.home() / "SynologyDrive/Daily/Dashboard.md"),
    ))
    if not dashboard.exists():
        print("Dashboard.md nicht gefunden.", file=sys.stderr)
        sys.exit(1)

    text = dashboard.read_text(encoding='utf-8')
    done_ids = []
    open_ids = []
    # Greedy bis zur LETZTEN Klammer (Slug-ID steht am Zeilenende, nicht z.B. "(carry-over)")
    todo_pattern = re.compile(r'- \[([ x])\] .*\(([a-z][a-z0-9_-]+)\)\s*$', re.IGNORECASE)

    for line in text.split('\n'):
        m = todo_pattern.search(line)
        if m:
            checked = m.group(1) == 'x'
            todo_id = m.group(2)
            if checked:
                done_ids.append(todo_id)
            else:
                open_ids.append(todo_id)

    if not done_ids and not open_ids:
        print("Keine Todo-Referenzen im Dashboard gefunden.")
        return

    conn = _open_db()
    synced = 0
    done_for_removal = set()
    for tid in done_ids:
        t = _get_todo(conn, tid)
        if not t:
            continue
        if t.get('status') == STATUS_OPEN:
            _update_todo(conn, tid, status=STATUS_DONE, done_date=date.today().isoformat())
            _sync_todo_to_entries(conn, tid)
            print(f"  DONE: {tid} — {t['name']}")
            synced += 1
        # Auch schon erledigte [x]-Zeilen aus dem Dashboard ausräumen
        done_for_removal.add(tid)

    conn.close()

    removed = 0
    if done_for_removal:
        lines = text.split('\n')
        out = []
        i = 0
        while i < len(lines):
            line = lines[i]
            m = todo_pattern.search(line)
            if m and m.group(1).lower() == 'x' and m.group(2) in done_for_removal:
                i += 1
                # Folgende eingerückte Body-Zeilen mit-entfernen
                while i < len(lines) and lines[i] and (lines[i].startswith('  ') or lines[i].startswith('\t')):
                    i += 1
                removed += 1
                continue
            out.append(line)
            i += 1
        new_text = '\n'.join(out)
        # 3+ Leerzeilen kollabieren
        new_text = re.sub(r'\n{3,}', '\n\n', new_text)
        if new_text != text:
            dashboard.write_text(new_text, encoding='utf-8')

    msg = f"\nSync: {synced} Todos erledigt"
    if removed:
        msg += f", {removed} Zeilen aus Dashboard entfernt"
    msg += f", {len(open_ids)} noch offen."
    print(msg)


def cmd_export(args):
    conn = _open_db()
    open_todos = _get_todos(conn, status=STATUS_OPEN)
    conn.close()
    for t in open_todos:
        dl = t.get('deadline', '')
        prio = _prio_label(t.get('priority', ''))
        sched = t.get('scheduled', '')
        sched_str = f" | scheduled: {sched}" if sched else ''
        print(f"- [ ] **{t['name']}** | {prio} | {dl}{sched_str} | {t.get('id')}")


def cmd_show(args):
    conn = _open_db()
    t = _get_todo(conn, args.id)
    if not t:
        conn.close()
        print(_t('not_found', id=args.id), file=sys.stderr)
        sys.exit(1)
    label_order = ['id', 'name', 'status', 'priority', 'workstream', 'category',
                   'deadline', 'deadline_type', 'scheduled', 'repeat',
                   'created', 'done_date', 'context', 'body']
    skip = set(label_order) | {'parent_id'}  # parent_id wird unten als Breadcrumb gerendert
    width = max(len(k) for k in label_order) + 2
    for k in label_order:
        v = t.get(k, '')
        if v in (None, ''):
            continue
        if k == 'status':
            v = _status_label(v)
        elif k == 'priority':
            v = _prio_label(v)
        print(f"{k.ljust(width)}{v}")
    for k, v in t.items():
        if k in skip or v in (None, ''):
            continue
        print(f"{k.ljust(width)}{v}")

    # Hierarchie
    if (t.get('parent_id') or ''):
        p = _get_todo(conn, t['parent_id'])
        if p:
            print(_t('show_parent', id=p['id'], name=p['name']))
    kids = _children(conn, t['id'])
    if kids:
        done = sum(1 for k in kids if k.get('status') == STATUS_DONE)
        print(_t('show_children', done=done, total=len(kids)))
        for k in kids:
            st = k.get('status')
            mark = '✓' if st == STATUS_DONE else ('✗' if st == STATUS_CANCELLED else ' ')
            print(f"  [{mark}] {k['id']} — {k['name']}")
    conn.close()


def cmd_stale(args):
    """Listet offene KÖNNTE-Todos die älter als N Tage sind."""
    conn = _open_db()
    cutoff = (date.today() - timedelta(days=args.days)).isoformat()
    rows = conn.execute(
        "SELECT id, name, workstream, category, created FROM todos "
        "WHERE status = 'open' AND priority = 'low' AND created <= ? "
        "ORDER BY created ASC", (cutoff,)
    ).fetchall()
    conn.close()

    if not rows:
        print(_t('stale_none', days=args.days))
        return

    print(_t('stale_header', days=args.days, n=len(rows)) + "\n")
    for r in rows:
        age = (date.today() - date.fromisoformat(r['created'])).days
        ws = r['workstream'] or '-'
        print(f"  {r['id']}  |  {r['name']}  |  {ws}  |  {age}d alt  |  erstellt {r['created']}")


# --- Main ---

def main():
    parser = LoggingArgumentParser(description='Loki Todo CLI')
    parser._tool_name = 'todo'
    sub = parser.add_subparsers(dest='command')

    p_add = sub.add_parser('add', aliases=['new', 'create'])
    p_add.add_argument('name')
    p_add.add_argument('--prio', '--priority', '-p', dest='prio')
    p_add.add_argument('--deadline')
    p_add.add_argument('--due', dest='due')
    p_add.add_argument('--scheduled')
    p_add.add_argument('--tag', '--cat', '--category', dest='tag')
    p_add.add_argument('--ctx')
    p_add.add_argument('--repeat', choices=['daily', 'weekly', 'monthly'])
    p_add.add_argument('--workstream', '--ws', '--workday', dest='workstream')
    p_add.add_argument('--deadline-type', dest='deadline_type', choices=['hard', 'soft'])
    p_add.add_argument('--body', '--note', '--notes', '--desc', '--description', dest='body')
    p_add.add_argument('--external-id', dest='external_id', help='Generische External-ID (z.B. CRM-024). Beim done feuert der konfigurierte Hook.')
    p_add.add_argument('--external-system', dest='external_system', help='External-System (z.B. crm). Leer = aus External-ID abgeleitet.')
    p_add.add_argument('--crm', dest='crm', help='Alias fuer --external-id X --external-system crm. Beim done flippt der Lead auf ball=them.')
    p_add.add_argument('--parent', dest='parent', help='Parent-Todo-ID — dieses Todo als Kind anlegen (eine Ebene tief).')
    p_add.add_argument('--force', action='store_true', help='Skip duplicate check')

    p_list = sub.add_parser('list', aliases=['ls'])
    p_list.add_argument('--status')
    p_list.add_argument('--prio', '--priority', '-p', dest='prio')
    p_list.add_argument('--tag', '--cat', '--category', dest='tag')
    p_list.add_argument('--workstream', '--ws', '--workday', dest='workstream')
    p_list.add_argument('--scheduled')
    p_list.add_argument('--done-since', dest='done_since',
                        help='Only todos done on/after this date (YYYY-MM-DD), by done_date.')
    p_list.add_argument('--search', '-q', dest='search')
    p_list.add_argument('--format', choices=['table', 'json', 'markdown'], default='table')
    p_list.add_argument('--limit', type=int)

    p_show = sub.add_parser('show', aliases=['view', 'get'])
    p_show.add_argument('id')

    p_done = sub.add_parser('done', aliases=['close', 'complete', 'finish'])
    p_done.add_argument('id')
    p_done.add_argument('--note', '--body', '--notes', dest='note')
    p_done.add_argument('--crm-followup', dest='crm_followup', help='Festes Follow-up-Datum für den CRM-Touch (YYYY-MM-DD)')
    p_done.add_argument('--crm-days', dest='crm_days', type=int, help='Wartefenster in Tagen für den CRM-Touch (Default 7)')

    p_cancel = sub.add_parser('cancel', aliases=['rm', 'remove', 'delete'])
    p_cancel.add_argument('id')

    p_reopen = sub.add_parser('reopen', aliases=['undo', 'restore'])
    p_reopen.add_argument('id')

    p_update = sub.add_parser('update', aliases=['edit', 'set'])
    p_update.add_argument('id')
    p_update.add_argument('--deadline')
    p_update.add_argument('--due', dest='due')
    p_update.add_argument('--scheduled')
    p_update.add_argument('--prio', '--priority', '-p', dest='prio')
    p_update.add_argument('--tag', '--cat', '--category', dest='tag')
    p_update.add_argument('--ctx')
    p_update.add_argument('--name')
    p_update.add_argument('--repeat', choices=['daily', 'weekly', 'monthly'])
    p_update.add_argument('--workstream', '--ws', '--workday', dest='workstream')
    p_update.add_argument('--deadline-type', dest='deadline_type', choices=['hard', 'soft'])
    p_update.add_argument('--body', '--note', '--notes', '--desc', '--description', dest='body')
    p_update.add_argument('--external-id', dest='external_id', help='External-ID verknüpfen/ändern (leer = lösen)')
    p_update.add_argument('--external-system', dest='external_system', help='External-System (z.B. crm)')
    p_update.add_argument('--crm', dest='crm', help='Alias fuer --external-id X --external-system crm (leer = lösen)')
    p_update.add_argument('--parent', dest='parent', help='Unter Parent hängen (Umhängen). Leer ("") = auf Top-Level loslösen.')

    p_group = sub.add_parser('group')
    p_group.add_argument('parent', help='Parent-Todo-ID')
    p_group.add_argument('children', nargs='+', help='Eine oder mehrere Kind-Todo-IDs')

    p_today = sub.add_parser('today')
    p_today.add_argument('--format', choices=['markdown', 'table', 'json'], default='table')

    p_overdue = sub.add_parser('overdue')

    p_search = sub.add_parser('search')
    p_search.add_argument('query')

    p_sync = sub.add_parser('sync-dashboard')

    p_export = sub.add_parser('export')

    p_stale = sub.add_parser('stale')
    p_stale.add_argument('--days', type=int, default=30, help='Alter in Tagen (Default: 30)')

    sub.add_parser('workdays', aliases=['workday', 'workstreams', 'workstream'])

    p_categories = sub.add_parser('categories', aliases=['cats'])
    p_categories.add_argument('--format', choices=['table', 'json'], default='table')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {
        'add': cmd_add, 'new': cmd_add, 'create': cmd_add,
        'list': cmd_list, 'ls': cmd_list,
        'show': cmd_show, 'view': cmd_show, 'get': cmd_show,
        'done': cmd_done, 'close': cmd_done, 'complete': cmd_done, 'finish': cmd_done,
        'cancel': cmd_cancel, 'rm': cmd_cancel, 'remove': cmd_cancel, 'delete': cmd_cancel,
        'reopen': cmd_reopen, 'undo': cmd_reopen, 'restore': cmd_reopen,
        'update': cmd_update, 'edit': cmd_update, 'set': cmd_update,
        'today': cmd_today, 'overdue': cmd_overdue, 'search': cmd_search,
        'sync-dashboard': cmd_sync_dashboard, 'export': cmd_export,
        'stale': cmd_stale, 'group': cmd_group,
        'workdays': cmd_workdays, 'workday': cmd_workdays,
        'workstreams': cmd_workdays, 'workstream': cmd_workdays,
        'categories': cmd_categories, 'cats': cmd_categories,
    }
    try:
        cmds[args.command](args)
    except (SystemExit, BrokenPipeError):
        raise
    except Exception as e:
        log_error(
            tool='todo',
            command=args.command,
            error_type=type(e).__name__,
            error_msg=str(e),
        )
        raise


if __name__ == '__main__':
    try:
        main()
    except BrokenPipeError:
        # Downstream-Pipe (z.B. | head) geschlossen — sauber beenden statt Stacktrace.
        try:
            sys.stdout.close()
        except Exception:
            pass
        os._exit(0)
