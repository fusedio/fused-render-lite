# fused-render-lite status

One section per version: what the `fused.*` runtime supports, what it does
not, and what the build weighs. Sizes come from `bash scripts/build_dmg.sh`
(`app size` line and `done:` line), macOS arm64, ad-hoc signed.

## Size by version

| version | DMG | .app | Δ DMG vs previous | what changed |
| --- | --- | --- | --- | --- |
| fused-render (full) | ~hundreds of MB | ~400 MB installed packages | — | reference point |
| 0.1.0 | 12.20 MB (12,202,154 B) | 25 MB | — | first lite build |
| 0.2.0 | 12.22 MB (12,215,018 B) | 25 MB | +12.9 KB | `fused.ai.text` (Claude CLI tier) |

The Claude tier costs nothing beyond one Python module and ~150 lines of
runtime JS: inference runs in the user's own `claude` CLI, which is not
bundled.

Constant across versions: 0 runtime Python deps (`rumps` + `pyobjc-framework-Cocoa`
only in the `[app]` extra); no bundled data packages (each app's
`pyproject.toml` → `uv sync`); `uv` downloaded on first use (0.12.13,
sha256-verified) unless built with `FUSED_RENDER_BUNDLE_UV=1`.

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
