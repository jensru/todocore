#!/usr/bin/env python3
"""i18n message catalog for the Loki Todo CLI.

Keys are canonical English slugs. Values are .format()-templates per language.
The German ('de') values are the historically-shipped strings, byte-for-byte
(emojis, umlauts, colons, em-dashes, format placeholders) — dev==prod for the
daily driver depends on this. English ('en') values are clean equivalents.

Use t(key, lang, **kwargs). Fallback chain: MESSAGES[lang][key] -> MESSAGES['en'][key].
"""

MESSAGES = {
    'en': {
        # cmd_add
        'add_ok': "OK: {id} — {name}",
        'add_duplicate': "DUPLICATE @ {ratio:.2f}: '{name}' ({id}). Use --force to add anyway.",
        'parent_error': "PARENT-ERROR: {error}",
        # cmd_done
        'not_found': "NOT FOUND: {id}",
        'done': "DONE: {id} — {name}",
        'repeat': "REPEAT: {id} — {name} (next: {next})",
        'all_children_done': "NOTE: all children of {parent} done → todo.py done {parent}",
        # done-event hooks
        'done_hook_warn': "DONE-HOOK-WARN: {error}. Todo stays done.",
        'done_webhook': "DONE-WEBHOOK: {url}",
        'done_webhook_warn': "DONE-WEBHOOK-WARN: {error}",
        'done_hook_invalid': "DONE-HOOK-WARN: invalid TODO_DONE_HOOK '{spec}' (expected module:func)",
        # _builtin_crm_done
        'crm_warn_notfound': "CRM-WARN: {crm_id} not found, no touch.",
        'crm_skip': "CRM-SKIP: {crm_id} is {state}, unchanged.",
        'crm_touched': "CRM: {id} → ball=them, follow-up {followup}",
        # cmd_cancel
        'children_freed': "  {n} children freed to top-level.",
        'cancelled': "CANCELLED: {id} — {name}",
        # cmd_reopen
        'reopened': "REOPENED: {id} — {name}",
        # cmd_update
        'updated': "UPDATED: {id} — {name}",
        # cmd_group
        'group_parent_is_child': "PARENT-ERROR: {id} is itself a child, only one level allowed.",
        'group_skip_notfound': "  SKIP: {ref} not found",
        'group_skip_is_parent': "  SKIP: {id} is the parent",
        'group_skip_has_children': "  SKIP: {id} has children of its own",
        'group_result': "GROUP: {n} todos under {id} — {name}",
        'group_moved_item': "  + {id}",
        # cmd_workdays
        'workdays_header': "WORKDAY      CATEGORIES",
        # cmd_today
        'today_workday': "Workday today: {wd}",
        'today_workday_none': "(none)",
        'today_no_todos': "Workday today: {wd} — no todos.",
        'today_warn_header': "\n⚠ {n} todos >{warn_days} days overdue (other category):",
        'today_warn_item': "  {id} — {name} (deadline {dl}, cat={cat})",
        # _print_today_markdown
        'md_workday_comment': "<!-- workday: {wd} -->",
        'md_workday_none': "none",
        'md_empty': "*(none)*",
        'md_tag_deadline': "Deadline {dl}",
        'md_tag_carryover': "carry-over",
        'md_carryover_header': "\n---\n\n> **Carry-over warning:** {n} todos overdue (other category)",
        'md_carryover_item': "> - {id} — {name} (deadline {dl}, category={cat})",
        # cmd_overdue
        'overdue_none': "No overdue todos.",
        # _format_table / _format_todos
        'no_todos_table': "No todos found.",
        'no_todos_md': "_No todos._",
        # cmd_stale
        'stale_none': "No COULD todos older than {days} days.",
        'stale_header': "COULD todos older than {days} days ({n}):",
        # cmd_show
        'show_parent': "\n↑ Parent: {id} — {name}",
        'show_children': "\nChildren ({done}/{total}):",
    },
    'de': {
        # cmd_add
        'add_ok': "OK: {id} — {name}",
        'add_duplicate': "DUPLICATE @ {ratio:.2f}: '{name}' ({id}). Use --force to add anyway.",
        'parent_error': "PARENT-FEHLER: {error}",
        # cmd_done
        'not_found': "NOT FOUND: {id}",
        'done': "DONE: {id} — {name}",
        'repeat': "REPEAT: {id} — {name} (nächstes: {next})",
        'all_children_done': "HINWEIS: Alle Kinder von {parent} erledigt → todo.py done {parent}",
        # done-event hooks
        'done_hook_warn': "DONE-HOOK-WARN: {error}. Todo bleibt erledigt.",
        'done_webhook': "DONE-WEBHOOK: {url}",
        'done_webhook_warn': "DONE-WEBHOOK-WARN: {error}",
        'done_hook_invalid': "DONE-HOOK-WARN: ungueltiges TODO_DONE_HOOK '{spec}' (erwartet modul:func)",
        # _builtin_crm_done
        'crm_warn_notfound': "CRM-WARN: {crm_id} nicht gefunden, kein Touch.",
        'crm_skip': "CRM-SKIP: {crm_id} ist {state}, unverändert.",
        'crm_touched': "CRM: {id} → ball=them, Follow-up {followup}",
        # cmd_cancel
        'children_freed': "  {n} Kinder auf Top-Level gelöst.",
        'cancelled': "CANCELLED: {id} — {name}",
        # cmd_reopen
        'reopened': "REOPENED: {id} — {name}",
        # cmd_update
        'updated': "UPDATED: {id} — {name}",
        # cmd_group
        'group_parent_is_child': "PARENT-FEHLER: {id} ist selbst ein Kind, nur eine Ebene erlaubt.",
        'group_skip_notfound': "  SKIP: {ref} nicht gefunden",
        'group_skip_is_parent': "  SKIP: {id} ist der Parent",
        'group_skip_has_children': "  SKIP: {id} hat selbst Kinder",
        'group_result': "GROUP: {n} Todos unter {id} — {name}",
        'group_moved_item': "  + {id}",
        # cmd_workdays
        'workdays_header': "WORKDAY      KATEGORIEN",
        # cmd_today
        'today_workday': "Workday heute: {wd}",
        'today_workday_none': "(keiner)",
        'today_no_todos': "Workday heute: {wd} — keine Todos.",
        'today_warn_header': "\n⚠ {n} Todos >{warn_days} Tage überfällig (andere Kategorie):",
        'today_warn_item': "  {id} — {name} (deadline {dl}, cat={cat})",
        # _print_today_markdown
        'md_workday_comment': "<!-- workday: {wd} -->",
        'md_workday_none': "keiner",
        'md_empty': "*(keine)*",
        'md_tag_deadline': "Deadline {dl}",
        'md_tag_carryover': "carry-over",
        'md_carryover_header': "\n---\n\n> **Carry-Over-Warnung:** {n} Todos überfällig (andere Kategorie)",
        'md_carryover_item': "> - {id} — {name} (deadline {dl}, category={cat})",
        # cmd_overdue
        'overdue_none': "Keine überfälligen Todos.",
        # _format_table / _format_todos
        'no_todos_table': "Keine Todos gefunden.",
        'no_todos_md': "_Keine Todos._",
        # cmd_stale
        'stale_none': "Keine KÖNNTE-Todos älter als {days} Tage.",
        'stale_header': "KÖNNTE-Todos älter als {days} Tage ({n} Stück):",
        # cmd_show
        'show_parent': "\n↑ Parent: {id} — {name}",
        'show_children': "\nKinder ({done}/{total}):",
    },
}


def t(key, lang, **kwargs):
    """Resolve a localized message. Falls back to English if lang/key missing."""
    table = MESSAGES.get(lang, MESSAGES['en'])
    template = table.get(key, MESSAGES['en'][key])
    return template.format(**kwargs)
