# fused-render-lite status

One section per version: what the `fused.*` runtime supports, what it does
not, and what the build weighs. Sizes come from `bash scripts/build_dmg.sh`
(`app size` line and `done:` line), macOS arm64, ad-hoc signed.

## Size by version

Shipped size = the DMG attached to the GitHub release (built by CI on
`macos-14`, python.org framework Python, ad-hoc signed). That is what end
users download.

| version | shipped DMG | Δ vs previous | .app unpacked | what changed |
| --- | --- | --- | --- | --- |
| fused-render (full) | ~hundreds of MB | — | ~400 MB installed packages | reference point |
| 0.1.0 | 17.72 MB (17,723,156 B) | — | 25 MB | first lite build |
| 0.2.0 | 17.73 MB (17,729,146 B) | +5.99 KB | 25 MB | `fused.ai.text` (Claude CLI tier) |
| 0.4.0 | 17.73 MB (17,729,166 B) | +1.79 KB | 25 MB | legacy env for apps without `pyproject.toml`; `autoReload(true)` throws |
| 0.3.0 | 17.73 MB (17,727,372 B) | −1.77 KB | 25 MB | `uploadFile`, `mkdir`, `trackJob`/`watchJob`, `autoReload(false)` no-op, runPython timeout 600 s |

The Claude tier costs nothing beyond one Python module and ~150 lines of
runtime JS: inference runs in the user's own `claude` CLI, which is not
bundled.

A local `bash scripts/build_dmg.sh` on Homebrew's python@3.12 comes out
~5.5 MB smaller (12.2 MB) because the Homebrew bottle is leaner than the
python.org framework; it is not the shipped artifact and runs only on the
building macOS version.

Constant across versions: 0 runtime Python deps (`rumps` + `pyobjc-framework-Cocoa`
only in the `[app]` extra); no bundled data packages (each app's
`pyproject.toml` → `uv sync`); `uv` downloaded on first use (0.12.13,
sha256-verified) unless built with `FUSED_RENDER_BUNDLE_UV=1`.

---

## 0.4.0

Changes from 0.3.0: an app that ships **no `pyproject.toml`** no longer runs on a
stdlib-only Python. It runs in one shared "legacy" venv holding fused-render's
old `[bundled]` set minus the cloud credential chains and the fused engine —
built by `uv sync` on the first such open (~120 MB on disk, once), so `.fused`
files exported before pyproject was required keep working. `fused.autoReload(true)`
now throws (only `autoReload(false)` is a no-op).

Legacy set: `numpy`, `pandas`, `requests`, `pillow`, `openpyxl`, `python-pptx`,
`msgpack>=1.0`, `fpdf2>=2.8.7`, `drain3>=0.9.11`. Not carried over from
`[bundled]`: `botocore`, `google-auth`, `fused`, `mcp`. Override for tests with
`FUSED_RENDER_LITE_LEGACY_DEPS` (comma list). Generated project lives at
`~/.fused-render-lite/legacy/pyproject.toml`; a changed set invalidates the venv.

API table: identical to 0.3.0 except `fused.autoReload(true)` → ❌ throws.

---

## 0.3.0

Changes from 0.2.0: `fused.uploadFile` and `fused.mkdir` land; `fused.trackJob` /
`fused.watchJob` land on an in-process job store (no shell UI, but rows survive a
page reload and a worker can be told to stop via `cancel_requested`);
`fused.autoReload(false)` is accepted as a no-op instead of throwing (a `.fused`
extract never changes under the page); `autoReload(true)` still throws; `runPython` timeout 60 s → 600 s, matching
fused-render. Workers spawned by `runPython` get `FUSED_RENDER_ORIGIN` so a
detached process can `POST /api/jobs`.

| member | status | notes |
| --- | --- | --- |
| `fused.runPython(py, params, opts?)` | ✅ | 600 s timeout; `opts.key` supersession, `opts.signal` |
| `fused.params.get / getAll / set / onChange` | ✅ | |
| `fused.readFile` / `stat` / `writeFile` / `rawUrl` | ✅ | as 0.2.0 |
| `fused.uploadFile(path, blob)` | ✅ new | raw body to `/api/fs/upload?path=&base=`; 403 `readonly` → `err.type` |
| `fused.mkdir(path)` | ✅ new | 409 → `type: "exists"`, 403 → `readonly` |
| `fused.trackJob(spec)` | ✅ new | `update/finish/fail/cancelled`, `cancelRequested`, `state`; fire-and-forget |
| `fused.watchJob(id)` | ✅ new | `get()`, `watch(cb, ms)`, `stop()`, `cancel()` |
| `fused.autoReload(false)` | ✅ no-op | opting out of live reload is accepted; nothing to watch in a .fused extract |
| `fused.autoReload(true)` | ❌ throws | live reload needs a file watcher lite does not have; the app should know |
| `fused.env` / `fused.device` / `fused.lite` | ✅ | |
| `fused.ai.text` | ✅ Claude only | as 0.2.0 |
| `fused.ai.models.list() / catalog()`, `fused.ai.cancel()` | ✅ | as 0.2.0 |
| `fused.ai.text` with `history`/`raw`/`images` | ❌ `bad_request` | |
| `fused.ai.text` with `provider: local/apple`, repo-id/`.gguf` model | ❌ `unavailable` | |
| `fused.ai.image / video / transcribe / embed`, `ai.models.load/download/unload` | ❌ `unavailable` | |
| `fused.capture.*` | ❌ throws | |
| `fused.fileIndex.*` | ❌ throws | |
| `fused.daemon.*` | ❌ throws | |
| `fused.snapshot` | ❌ throws | |

Server routes added: `POST /api/fs/upload`, `POST /api/fs/mkdir`, `GET/POST /api/jobs`, `POST /api/jobs/<id>/cancel|dismiss`, `POST /api/jobs/clear`.

---

## 0.2.0

| member | status | notes |
| --- | --- | --- |
| `fused.runPython(py, params, opts?)` | ✅ | `main(**params)` in the app's own venv; `opts.key` supersession, `opts.signal` |
| `fused.params.get / getAll / set / onChange` | ✅ | URL-backed, `_keys` reserved, batched history writes |
| `fused.readFile(path)` | ✅ | text |
| `fused.stat(path)` | ✅ | `{path, name, is_dir, size, mtime, writable}` |
| `fused.writeFile(path, content, opts?)` | ✅ | `expectedMtime` → 409 `conflict`; `create` → 409 `exists`; 403 `readonly` |
| `fused.rawUrl(path)` | ✅ | Range requests honoured |
| `fused.env` / `fused.device` / `fused.lite` | ✅ | `"local"` / `"desktop"` / `true` |
| `fused.ai.text({prompt, ...})` | ✅ Claude only | one `claude -p` per call. `model`: `haiku` (default), `sonnet`, `opus`, `fable`, `claude-*`. `systemPrompt`, `effort` (`low` = no thinking, `medium`, `high`, `xhigh`), `onChunk` (NDJSON over chunked HTTP), `abortSignal` (kills the CLI). `temperature`/`maxTokens`/`topP` → `warnings[]`. Errors: `ai_unavailable`, `bad_request`, `unavailable`, `ai_error`, `timeout` 600 s, `cancelled` |
| `fused.ai.models.list() / catalog()` | ✅ | Claude catalog; `catalog().unsupported` names the local capabilities |
| `fused.ai.cancel()` | ✅ | resolves `false`; use `abortSignal` |
| `fused.ai.text` with `history` / `raw` / `images` | ❌ `bad_request` | need a local model |
| `fused.ai.text` with `provider: "local"` / `"apple"`, repo-id or `.gguf` model | ❌ `unavailable` | no local inference |
| `fused.ai.image / video / transcribe / embed` | ❌ `unavailable` | async rejection, `catch` branches keep working |
| `fused.ai.models.load / download / unload` | ❌ `unavailable` | |
| `fused.capture.*` | ❌ throws | screen / audio / screenshot |
| `fused.fileIndex.*` | ❌ throws | filesystem index |
| `fused.daemon.*` | ❌ throws | folder daemons |
| `fused.trackJob`, `fused.watchJob` | ❌ throws | jobs |
| `fused.uploadFile`, `fused.mkdir` | ❌ throws | |
| `fused.autoReload` | ❌ throws | live reload |
| `fused.snapshot` | ❌ throws | git snapshots |

"throws" = `Error("<name> is not supported on fused-render-lite")`, `err.type === "unsupported"`, on property read for namespaces (Proxy) or on call.

Server routes: `GET /`, `/open?_file=`, `/render?path=`, `/api/health`, `/api/fs/raw`, `/api/fs/stat`, `/api/open/status`, `/api/ai/runtime`, `/api/ai/catalog`; `POST /api/open`, `/api/drop`, `/api/run`, `/api/fs/write`, `/api/ai` (JSON or chunked NDJSON), `/api/ai/cancel`; other `POST /api/ai/*` → 409 `unavailable`. POSTs need `X-Fused: 1`. 127.0.0.1 only.

Requires the `claude` CLI on the machine for the AI tier (`FUSED_RENDER_LITE_CLAUDE_BIN`, PATH, `~/.claude/local`, `~/.local/bin`, `~/.bun/bin`, Homebrew).

---

## 0.1.0

| member | status | notes |
| --- | --- | --- |
| `fused.runPython` | ✅ | as above |
| `fused.params.*` | ✅ | as above |
| `fused.readFile` / `stat` / `writeFile` / `rawUrl` | ✅ | as above |
| `fused.env` / `fused.device` / `fused.lite` | ✅ | |
| `fused.ai.*` (all verbs, `models`, `cancel`) | ❌ throws | whole namespace a Proxy |
| `fused.capture.*`, `fused.fileIndex.*`, `fused.daemon.*` | ❌ throws | |
| `fused.trackJob`, `watchJob`, `uploadFile`, `mkdir`, `autoReload`, `snapshot` | ❌ throws | |

Server routes: as 0.2.0 minus every `/api/ai*` route.

---

## Removed from fused-render (never in lite)

Explorer shell (React/Vite), ~50 preview templates and their vendored JS,
file index, LAN sharing, background daemons, jobs, capture, git snapshots,
Claude chat sidebar, local/Apple AI runners (mlx, llama.cpp, whisper, image
and video models), bookmarks, drafts, updater, Windows/Linux/iOS shells,
bundled data stack (numpy, pandas, pyarrow, duckdb, botocore, fused engine…).
