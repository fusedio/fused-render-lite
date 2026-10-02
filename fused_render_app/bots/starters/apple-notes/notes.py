"""Apple Notes on this Mac, through Notes.app's own scripting (JavaScript for
Automation via osascript). No iCloud web, no network: it reads the Notes database
the app already has, including notes from every account the app shows.

main(action=..., ...) returns a JSON dict and never raises; failures come back as
{"error": "...", "action": "..."}. The first call from a new host process makes
macOS ask "FusedRender wants to control Notes": the user must click OK once
(System Settings > Privacy & Security > Automation to change it later).
"""
import json
import subprocess

LIMIT_MAX = 100
SNIPPET = 160
TIMEOUT_S = 55  # the page's runner stops at 60 s

# One JXA program; `run(argv)` gets the request as JSON and prints a JSON result.
JXA = r"""
function run(argv) {
  const p = JSON.parse(argv[0] || "{}"), app = Application("Notes");
  app.includeStandardAdditions = false;
  const iso = d => { try { return d ? new Date(d).toISOString() : ""; } catch (e) { return ""; } };
  const snip = (t, n) => { t = (t || "").replace(/\s+/g, " ").trim(); return t.length > n ? t.slice(0, n) + "…" : t; };
  const folderOf = n => { try { return n.container().name(); } catch (e) { return ""; } };
  const row = (n, full) => { const r = { id: n.id(), title: n.name(), folder: folderOf(n), modified: iso(n.modificationDate()), created: iso(n.creationDate()) };
    if (full) { r.body = n.plaintext(); r.shared = !!n.shared(); r.password_protected = !!n.passwordProtected(); }
    else r.snippet = snip(n.plaintext(), p.snippet || 160);
    return r; };
  const byTitleOrId = () => {
    if (p.id) { const m = app.notes.whose({ id: p.id })(); if (m.length) return m[0]; }
    if (p.title) { let m = app.notes.whose({ name: p.title })(); if (!m.length) m = app.notes.whose({ name: { _contains: p.title } })(); if (m.length) return m[0]; }
    return null; };
  const sortDesc = (a, b) => (b.modified > a.modified ? 1 : b.modified < a.modified ? -1 : 0);
  const limit = Math.max(1, Math.min(p.limit || 20, 100));
  let out;
  switch (p.action) {
    case "status": {
      out = { ok: true, app: app.name(), version: app.version(), accounts: app.accounts().map(a => a.name()), notes: app.notes.length, folders: app.folders.length };
      break; }
    case "folders": {
      out = { folders: app.folders().map(f => { let acct = ""; try { acct = f.container().name(); } catch (e) {}
        return { name: f.name(), account: acct, notes: f.notes.length, id: f.id() }; }) };
      break; }
    case "search": {
      const q = p.query || "";
      let src = p.folder ? app.folders.whose({ name: p.folder })() : null;
      if (p.folder && !src.length) { out = { error: "no folder named " + p.folder }; break; }
      const scope = src ? src[0].notes : app.notes;
      const cond = p.in_body ? { _or: [{ name: { _contains: q } }, { plaintext: { _contains: q } }] } : { name: { _contains: q } };
      const hits = scope.whose(cond)();
      const rows = hits.map(n => row(n, false)).sort(sortDesc);
      out = { query: q, in_body: !!p.in_body, folder: p.folder || "", count: rows.length, notes: rows.slice(0, limit) };
      break; }
    case "read": {
      const n = byTitleOrId(); if (!n) { out = { error: "no note matches id " + (p.id || "") + " or title " + (p.title || "") }; break; }
      out = { note: row(n, true) }; break; }
    case "recent": {
      const days = Math.max(1, p.days || 7), since = new Date(Date.now() - days * 86400000);
      let scope = app.notes;
      if (p.folder) { const f = app.folders.whose({ name: p.folder })(); if (!f.length) { out = { error: "no folder named " + p.folder }; break; } scope = f[0].notes; }
      const hits = scope.whose({ modificationDate: { _greaterThan: since } })();
      const rows = hits.map(n => row(n, false)).sort(sortDesc);
      out = { days, folder: p.folder || "", count: rows.length, notes: rows.slice(0, limit) }; break; }
    case "create": {
      const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const lines = String(p.body || "").split(/\r?\n/);
      const html = "<div><h1>" + esc(p.title || lines[0] || "Note") + "</h1></div>" + lines.map(l => "<div>" + (l ? esc(l) : "<br>") + "</div>").join("");
      let folder = null;
      if (p.folder) { const f = app.folders.whose({ name: p.folder })(); if (!f.length) { out = { error: "no folder named " + p.folder }; break; } folder = f[0]; }
      else folder = app.defaultAccount().defaultFolder();
      const n = app.Note({ body: html }); folder.notes.push(n);
      out = { ok: true, note: row(n, false) }; break; }
    default: out = { error: "unknown action " + p.action };
  }
  return JSON.stringify(out);
}
"""


# ---- fallback: read the Notes database directly ------------------------------
# macOS only shows the "control Notes" prompt to apps whose Info.plist carries
# NSAppleEventsUsageDescription; FusedRender's does not (as of 2026-10), so Apple
# Events are refused with -1743 and no prompt. The store at
# ~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite is readable
# when the host has Full Disk Access (the iMessage bridge needs the same), so
# reads fall back to it: folders, search, read, recent. Bodies are gzip +
# protobuf (Document.2 Note.3 .2 = the text). Nothing is ever written there.
import os
import re
import sqlite3
import time
import zlib

STORE = os.path.expanduser("~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite")
CD_EPOCH = 978307200  # Core Data dates count from 2001-01-01
PLIST_FIX = ("Creating needs Notes automation, which macOS refuses for FusedRender because its Info.plist has no "
             "NSAppleEventsUsageDescription (so no permission prompt is ever shown). Add that key to the app and reinstall; "
             "reads work without it through the Notes database.")
SHORTCUT = "New Note"  # an Apple Shortcut: Create Note (Shortcut Input) — Shortcuts has its own Notes permission
SHORTCUT_HOWTO = (f"Creating a note goes through an Apple Shortcut named \"{SHORTCUT}\" (Shortcuts has its own permission to Notes). "
                  "Make one in Shortcuts.app: one action, Create Note, with its text set to Shortcut Input and the folder you want; "
                  "name it exactly that and retry.")


def _shortcut_create(req, c=None):
    """Create a note by piping 'title\nbody' into `shortcuts run <SHORTCUT>`; the
    Create Note action takes the first line as the title. The folder argument is
    ignored (the shortcut's own folder applies). Returns the new row from the
    database once it shows up, or an error."""
    name = req.get("shortcut") or SHORTCUT
    try:
        have = subprocess.run(["shortcuts", "list"], capture_output=True, text=True, timeout=20).stdout.splitlines()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        have = []
    if name not in have:
        return {"error": f"no Shortcut named {name!r} on this Mac. " + SHORTCUT_HOWTO}
    title = (req["title"] or req["body"].splitlines()[0] if req["body"].strip() else "Note").strip() or "Note"
    text = title + ("\n" + req["body"] if req["body"].strip() else "")
    try:
        r = subprocess.run(["shortcuts", "run", name, "--output-type", "public.plain-text"], input=text, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        return {"error": f"the {name!r} shortcut did not finish in 45 s (is Shortcuts waiting on a dialog?)"}
    if r.returncode != 0:
        return {"error": f"the {name!r} shortcut failed: {(r.stderr or r.stdout).strip()[:300]}"}
    for _ in range(10):
        try:
            db = _db()
            rows = _rows(db, " and n.ZTITLE1 = ?", (title,))
            db.close()
        except Exception:
            rows = []
        if rows:
            rows[0].pop("_pk", None)
            return {"ok": True, "backend": "shortcut", "shortcut": name, "note": rows[0],
                    "note_on_folder": "the shortcut's folder was used" if req["folder"] else ""}
        time.sleep(0.5)
    return {"ok": True, "backend": "shortcut", "shortcut": name, "note": {"title": title, "folder": "", "id": ""},
            "warning": "the shortcut ran but the note was not found in the database yet"}


def _pb_fields(buf):
    """Yield (field, wire_type, value) for one protobuf message; value is bytes for
    length-delimited fields, int for varints; other wire types are skipped."""
    i, n = 0, len(buf)
    while i < n:
        tag, i = _varint(buf, i)
        f, wt = tag >> 3, tag & 7
        if wt == 0:
            v, i = _varint(buf, i)
            yield f, wt, v
        elif wt == 2:
            ln, i = _varint(buf, i)
            yield f, wt, buf[i:i + ln]
            i += ln
        elif wt == 1:
            i += 8
        elif wt == 5:
            i += 4
        else:
            return


def _varint(buf, i):
    out, shift = 0, 0
    while i < len(buf):
        b = buf[i]
        i += 1
        out |= (b & 0x7f) << shift
        if not b & 0x80:
            break
        shift += 7
    return out, i


def _note_text(blob):
    """Plain text of a note from its ZICNOTEDATA blob, or "" (locked or unknown layout)."""
    if not blob:
        return ""
    try:
        raw = zlib.decompress(blob, 16 + zlib.MAX_WBITS)
    except zlib.error:
        return ""
    cur = raw
    for want in (2, 3, 2):  # Document.note (2) -> .3 -> text (2)
        nxt = None
        for f, wt, v in _pb_fields(cur):
            if f == want and wt == 2:
                nxt = v
                break
        if nxt is None:
            break
        cur = nxt
    else:
        try:
            return cur.decode("utf-8")
        except UnicodeDecodeError:
            pass
    # Layout changed: take the longest UTF-8 string two levels down.
    best = ""
    for _, wt, v in _pb_fields(raw):
        if wt != 2:
            continue
        for _, wt2, v2 in _pb_fields(v):
            if wt2 != 2:
                continue
            for _, wt3, v3 in _pb_fields(v2):
                if wt3 == 2:
                    try:
                        t = v3.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    if len(t) > len(best) and t.isprintable() or "\n" in t:
                        best = t if len(t) > len(best) else best
    return best


def _iso(cd):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(cd + CD_EPOCH)) if cd else ""


def _snip(t, n):
    t = re.sub(r"\s+", " ", t or "").strip()
    return t[:n] + "…" if len(t) > n else t


def _db():
    if not os.path.exists(STORE):
        raise FileNotFoundError("Notes database not found at " + STORE)
    try:
        return sqlite3.connect(f"file:{STORE}?mode=ro", uri=True, timeout=5)
    except sqlite3.OperationalError as e:
        raise PermissionError("cannot open the Notes database (Full Disk Access for FusedRender is needed): " + str(e))


_NOTE_SQL = """select n.Z_PK, n.ZIDENTIFIER, n.ZTITLE1, n.ZSNIPPET, n.ZMODIFICATIONDATE1, coalesce(n.ZCREATIONDATE1, n.ZCREATIONDATE3, n.ZCREATIONDATE), f.ZTITLE2, n.ZISPASSWORDPROTECTED, f.ZFOLDERTYPE
  from ZICCLOUDSYNCINGOBJECT n left join ZICCLOUDSYNCINGOBJECT f on f.Z_PK = n.ZFOLDER
  where n.ZTITLE1 is not null and ifnull(n.ZMARKEDFORDELETION, 0) = 0 and ifnull(f.ZMARKEDFORDELETION, 0) = 0
    and ifnull(f.ZTITLE2, '') <> 'Recently Deleted' """


def _rows(c, where="", params=(), body=False):
    out = []
    for pk, ident, title, snip, mod, cre, folder, locked, _ in c.execute(_NOTE_SQL + where + " order by n.ZMODIFICATIONDATE1 desc", params):
        r = {"id": ident or str(pk), "title": title or "", "folder": folder or "", "modified": _iso(mod), "created": _iso(cre)}
        if body:
            blob = c.execute("select ZDATA from ZICNOTEDATA where ZNOTE = ?", (pk,)).fetchone()
            r["body"] = "" if locked else _note_text(blob[0] if blob else b"")
            r["password_protected"] = bool(locked)
            r["shared"] = False
        else:
            r["snippet"] = _snip(snip or "", SNIPPET)
            r["_pk"] = pk
        out.append(r)
    return out


def _sqlite(req):
    a = req["action"]
    c = _db()
    try:
        if a == "status":
            notes = c.execute(_NOTE_SQL.replace("select n.Z_PK, n.ZIDENTIFIER, n.ZTITLE1, n.ZSNIPPET, n.ZMODIFICATIONDATE1, coalesce(n.ZCREATIONDATE1, n.ZCREATIONDATE3, n.ZCREATIONDATE), f.ZTITLE2, n.ZISPASSWORDPROTECTED, f.ZFOLDERTYPE", "select count(*)")).fetchone()[0]
            folders = c.execute("select count(*) from ZICCLOUDSYNCINGOBJECT where ZTITLE2 is not null and ifnull(ZMARKEDFORDELETION,0)=0").fetchone()[0]
            return {"ok": True, "app": "Notes (database, read-only)", "backend": "sqlite", "notes": notes, "folders": folders, "accounts": [], "create_via": "shortcut", "note": PLIST_FIX + " " + SHORTCUT_HOWTO}
        if a == "folders":
            rows = c.execute("""select f.Z_PK, f.ZTITLE2, (select count(*) from ZICCLOUDSYNCINGOBJECT n where n.ZFOLDER = f.Z_PK and n.ZTITLE1 is not null and ifnull(n.ZMARKEDFORDELETION,0)=0)
                                from ZICCLOUDSYNCINGOBJECT f where f.ZTITLE2 is not null and f.ZTITLE2 <> 'Recently Deleted' and ifnull(f.ZMARKEDFORDELETION,0)=0 order by f.ZTITLE2""").fetchall()
            return {"backend": "sqlite", "folders": [{"name": t, "account": "", "notes": n, "id": str(pk)} for pk, t, n in rows]}
        if a in ("search", "recent"):
            where, params = "", []
            if req["folder"]:
                where += " and f.ZTITLE2 = ?"
                params.append(req["folder"])
            if a == "recent":
                where += " and n.ZMODIFICATIONDATE1 > ?"
                params.append(time.time() - CD_EPOCH - req["days"] * 86400)
            else:
                where += " and (n.ZTITLE1 like ? or n.ZSNIPPET like ?)" if not req["in_body"] else ""
                if not req["in_body"]:
                    params += [f"%{req['query']}%", f"%{req['query']}%"]
            rows = _rows(c, where, tuple(params))
            if a == "search" and req["in_body"]:
                q = req["query"].lower()
                hits = []
                for r in rows[:2000]:
                    if q in r["title"].lower() or q in r["snippet"].lower():
                        hits.append(r)
                        continue
                    blob = c.execute("select ZDATA from ZICNOTEDATA where ZNOTE = ?", (r["_pk"],)).fetchone()
                    if blob and q in _note_text(blob[0]).lower():
                        hits.append(r)
                rows = hits
            for r in rows:
                r.pop("_pk", None)
            out = {"backend": "sqlite", "folder": req["folder"], "count": len(rows), "notes": rows[:req["limit"]]}
            if a == "search":
                out.update(query=req["query"], in_body=req["in_body"])
            else:
                out["days"] = req["days"]
            return out
        if a == "read":
            rid = req["id"]
            m = re.search(r"/p(\d+)$", rid or "")
            if rid:
                rows = _rows(c, " and (n.ZIDENTIFIER = ? or n.Z_PK = ?)", (rid, int(m.group(1)) if m else (int(rid) if rid.isdigit() else -1)), body=True)
            else:
                rows = _rows(c, " and n.ZTITLE1 = ?", (req["title"],), body=True) or _rows(c, " and n.ZTITLE1 like ?", (f"%{req['title']}%",), body=True)
            if not rows:
                return {"error": f"no note matches id {rid!r} or title {req['title']!r}"}
            return {"backend": "sqlite", "note": rows[0]}
        if a == "create":
            return _shortcut_create(req)
        return {"error": "unknown action " + a}
    finally:
        c.close()


def run(action="status", query="", folder="", id="", title="", body="", days=7, limit=20, in_body=False, snippet=SNIPPET, backend="", shortcut=""):
    req = {"action": action, "query": query, "folder": folder, "id": id, "title": title, "body": body, "shortcut": shortcut,
           "days": int(days or 7), "limit": max(1, min(int(limit or 20), LIMIT_MAX)), "in_body": bool(in_body), "snippet": int(snippet or SNIPPET)}
    if action == "search" and not query.strip():
        return {"error": "search needs a query", "action": action}
    if action == "read" and not (id or title):
        return {"error": "read needs an id or a title", "action": action}
    if action == "create" and not (title.strip() or body.strip()):
        return {"error": "create needs a title or a body", "action": action}
    if backend == "sqlite":
        try:
            return _sqlite(req) | {"action": action}
        except Exception as e:
            return {"error": str(e), "action": action}
    try:
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", JXA, json.dumps(req)],
                           capture_output=True, text=True, timeout=TIMEOUT_S)
    except FileNotFoundError:
        return {"error": "osascript not found: this only works on macOS", "action": action}
    except subprocess.TimeoutExpired:
        return {"error": f"Notes did not answer within {TIMEOUT_S} s (a huge library or a password prompt?)", "action": action}
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        if "-1743" in err or "not allowed" in err.lower():
            try:
                return _sqlite(req) | {"action": action}  # reads from the database; create through the Shortcut
            except Exception as e:
                return {"error": f"automation refused (-1743) and the fallback failed: {e}", "action": action}
        elif "-600" in err or "isn't running" in err.lower():
            err = "Notes.app could not be launched: " + err
        return {"error": err[:600], "action": action}
    try:
        out = json.loads(r.stdout.strip())
    except ValueError:
        return {"error": "Notes returned something that is not JSON: " + r.stdout[:200], "action": action}
    out.setdefault("action", action)
    return out


def main(action="status", query="", folder="", id="", title="", body="", days=7, limit=20, in_body=False, snippet=SNIPPET, backend="", shortcut=""):
    try:
        return run(action, query, folder, id, title, body, days, limit, in_body, snippet, backend, shortcut)
    except Exception as e:  # never raise: the page and the bots read `error`
        return {"error": f"{type(e).__name__}: {e}", "action": action}


if __name__ == "__main__":
    import sys
    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(main(**args), indent=2, ensure_ascii=False))
