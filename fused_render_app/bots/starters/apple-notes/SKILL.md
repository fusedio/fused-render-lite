---
name: apple-notes
description: Read and search the user's Apple Notes on this Mac through Notes.app itself (no iCloud web, no network) — folders, title or full-text search, read a note's plain text, notes edited recently — and create a new note.
approve: [notes.py]
---

# Apple Notes

One Python file beside `index.html`, `notes.py`, with one `main(**params)`.
It prefers Notes.app automation (osascript) and, when macOS refuses that, reads
the Notes database directly (read-only; results then carry `"backend": "sqlite"`).
Either way it sees every account and folder the app shows. It returns a JSON
dict and never raises: failures come back as `{"error": "...", "action": "..."}`,
so check `error` first. As of October 2026 FusedRender cannot be granted Notes
automation (its Info.plist lacks NSAppleEventsUsageDescription), so reads come
from the database and `create` runs the user's Apple Shortcut named "New Note"
(Create Note from Shortcut Input), which has its own Notes permission; results
then carry `"backend": "shortcut"` and the `folder` argument is ignored. If that
shortcut is missing, `create` returns an `error` telling the user how to make it;
do not retry until they have.

Password-protected notes return an empty body until the user unlocks them in
Notes. Note bodies come back as plain text (checklists as lines, tables flattened).

## notes.py

`main(action=..., query="", folder="", id="", title="", body="", days=7, limit=20, in_body=False)`

- **Changes:** only `create` writes (a new note, saved at once; through automation or the "New Note" Shortcut, see above). Everything else is read-only.
- **Args:** `action` (default `status`); `query` for `search`; `folder` to narrow `search`/`recent` or to place a `create`; `id` or `title` for `read`; `title` and `body` (plain text, newlines kept) for `create`; `days` for `recent`; `limit` 1-100; `in_body: bool` makes `search` also match note text (slower on big libraries).

| action | params | returns |
| --- | --- | --- |
| `status` | — | `{ok, app, version, accounts:[…], notes, folders}` |
| `folders` | — | `{folders:[{name, account, notes, id}]}` |
| `search` | `query`, `folder`, `in_body`, `limit` | `{query, count, notes:[{id, title, folder, modified, created, snippet}]}` newest first |
| `read` | `id` or `title` | `{note:{id, title, folder, modified, created, body, shared, password_protected}}` |
| `recent` | `days`, `folder`, `limit` | `{days, count, notes:[{id, title, folder, modified, created, snippet}]}` newest first |
| `create` | `title`, `body`, `folder` | `{ok, note:{id, title, folder, modified, created, snippet}}` |

Examples: `{"action":"search","query":"packing list"}` · `{"action":"read","id":"x-coredata://…"}` ·
`{"action":"recent","days":3,"limit":10}` · `{"action":"create","title":"Groceries","body":"milk\neggs","folder":"Notes"}`.
