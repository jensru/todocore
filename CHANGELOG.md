# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-06-21

First public release. todocore is a generic, CLI-first todo core extracted from a
larger personal-assistant setup, with no consumer-specific logic.

### Added
- **CLI** (`todo` / `todocore`) backed by SQLite with WAL mode and FTS5 full-text
  search. The schema is bootstrapped on first use, so a fresh `TODO_DB_PATH` just
  works.
- **Parent/child nesting** (one level deep), repeats, deadlines vs. scheduled
  dates, categories, and duplicate detection.
- **Generic done-hook**: on `done`, todocore fires a best-effort event so an
  external system can react, via `TODO_DONE_WEBHOOK` (POST JSON), a
  `TODO_DONE_HOOK=module:func` in-process callable, or no-op. A failing hook never
  rolls back the completion.
- **Generic external link**: `--external-id` / `--external-system` (with `--crm` as
  a shorthand) to associate a todo with an outside system.
- **MCP server** (optional `[mcp]` extra) so MCP clients can drive the core.
- **i18n**: English by default, German labels via `TODO_LANG=de`.
- Workday config (`TODO_WORKDAYS_CONFIG`) mapping weekday to active categories for
  the `today` view.
- MIT license, CI (lint + tests on 3.10-3.12), and a tag-driven release workflow.
