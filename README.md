# todocore

A generic, CLI-first todo core backed by SQLite — with a thin MCP server and a
transport-agnostic done-hook. todocore is the OSS core extracted from a larger
personal-assistant setup; it carries no consumer-specific logic (no CRM, no
dashboards). Those live in *consumers* that build on top of it.

## Features

- **CLI-first.** Every action is a `todo` subcommand. Slugs as ids
  (`hallo-welt`), legacy `TODO-NNN` ids still resolve.
- **SQLite backend.** Single file, WAL mode, FTS5 full-text search. The schema
  is bootstrapped on first use — point it at a fresh path and it just works.
- **Parent/child nesting** (one level deep), repeats, deadlines vs. scheduled
  dates, categories, duplicate detection.
- **Generic done-hook.** On `done`, todocore fires a best-effort event so an
  external system (a CRM, a webhook, your own code) can react. The core itself
  stays agnostic.
- **MCP server** (optional extra) so MCP clients (Claude Desktop, Claude Code)
  can drive it.
- **i18n.** English by default; German status/priority labels via `TODO_LANG=de`.

## Install

```bash
# from PyPI-style install via pipx (recommended once published):
pipx install todocore

# from source:
pip install -e .
# with the MCP server:
pip install -e ".[mcp]"
```

This installs two equivalent console scripts: `todo` and `todocore`.

## Quickstart

```bash
todo add "Write the README" --prio high --tag docs
todo list
todo today
todo done write-the-readme
todo search "readme"
```

## Database path

The DB path is resolved in this order:

1. `TODO_DB_PATH` — canonical, wins if set.
2. `~/.local/share/todocore/todos.db` — default.

The parent directory is created on demand.

## Done-hook

When a todo is completed, todocore fires a best-effort `done` event. Resolution
order (first match wins; a hook failure never rolls back the `done`):

| Env | Behaviour |
|-----|-----------|
| `TODO_DONE_WEBHOOK=<url>` | POST JSON `{event, todo, external_id, external_system}` to the URL. |
| `TODO_DONE_HOOK=module:func` | Import `module`, call `func(conn=, todo=, args=)` in-process. |
| *(neither set)* | no-op. |

Link a todo to an external system with `--external-id X` (and optionally
`--external-system Y`). `--crm X` is a generic shorthand for
`--external-id X --external-system crm`. The `--crm-days` / `--crm-followup`
flags on `done` are passed through to the hook, which decides whether to use
them.

## Recurring todos

A todo can repeat. Set an interval on `add` or `update`:

```bash
todo add "Weekly review" --repeat weekly --scheduled 2026-06-29
todo update weekly-review --repeat daily   # change interval
todo update weekly-review --repeat ""       # turn off (one-shot again)
```

Intervals are `daily` | `weekly` | `monthly`. The model is a treadmill, not a
calendar/cron: when you `done` a recurring todo, the next instance is created
automatically. Its date anchors on the original's `scheduled` (or `deadline`) and
moves forward one period; a recurring todo with no date produces a dateless copy.
Marking the same todo done twice does not duplicate the next instance.

Note: recurrence is reachable via the CLI (and any UI that wraps it). The optional
MCP server does not yet expose the `repeat` field.

## MCP server

```bash
pip install -e ".[mcp]"
python -m todocore.todos_mcp
```

It is a thin layer: it only shells out to the `todocore.todo` CLI, never writing
to the DB directly.

## Consumers

todocore is the engine; consumers build their UX and integrations on top of it.

**Loki** is the author's own personal agent environment, a private
personal-assistant setup (not part of this repo). It uses todocore as its todo
engine and adds its own workday configs, a dashboard, and a CRM done-hook wired
through `TODO_DONE_HOOK`. todocore itself knows nothing about any of that, which is
the point: bring your own consumer (a CLI alias, a web UI, an agent, a cron job).

## About

Built by Jens Rusitschka at [kick & boost](https://kickboost.io).

I design AI workflows for product teams that actually hold up in real work.
Masterclass, workshops, engagements. Part of the design community for 20 years,
personal experiences with writing difficulties. I understood this topic before I
even had to research it.

- 🌐 [kickboost.io](https://kickboost.io)
- 💼 [LinkedIn](https://www.linkedin.com/in/jensru)

If todocore is useful to you, I'd love to hear about it.

## License

MIT.
