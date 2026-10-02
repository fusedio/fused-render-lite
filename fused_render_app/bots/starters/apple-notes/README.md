# Apple Notes

Browse and search your Apple Notes from a fused-render page, and let Browser Bots
read them too. The app talks to Notes.app on this Mac through macOS automation
(JavaScript for Automation via `osascript`), so there is no iCloud sign-in and no
network: every account and folder Notes shows is available, including On My Mac.

**Page.** Folders on the left with note counts; a search box (title match, or
"in text" for full-text); Recent lists what changed in the last days; click a
note to read its plain text. Nothing on the page edits a note.

**Bots.** `mcp.toml` exposes `notes_status`, `notes_folders`, `notes_search`,
`notes_read`, `notes_recent` (read, run at once) and `notes_create` (pauses for
your approval). `SKILL.md` documents `notes.py` for the `py` action.

**How it reaches Notes.** It tries Notes.app automation first. macOS only shows
the "control Notes" prompt to apps whose Info.plist carries
`NSAppleEventsUsageDescription`, and FusedRender's does not yet, so automation is
refused silently and the app falls back to reading the Notes database
(`~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite`, read-only;
needs Full Disk Access, which the iMessage bridge needs too). Reads work that way;
the status line on the page says which path is in use. Creating a note runs an
Apple Shortcut named "New Note" (Shortcuts has its own Notes permission): one
action, Create Note, text set to Shortcut Input, in the folder you like. The
`shortcut` argument names a different one. The folder argument is ignored on
that path. Once FusedRender's Info.plist gains the key above, automation takes
over by itself. Password-protected notes come back with an empty
body until unlocked in Notes.
