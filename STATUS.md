# fused-render-lite status

Update this file with every release. Sizes come from `bash scripts/build_dmg.sh`
(the `app size` line and the `done:` line), measured on macOS arm64.

## Build

| | value |
| --- | --- |
| version | 0.2.0 |
| DMG | 12 MB (`FusedRenderLite-0.1.0.dmg`, 12,202,154 bytes, ULFO) |
| .app | 25 MB unpacked |
| runtime Python deps | 0 (`rumps` + `pyobjc-framework-Cocoa` in the `[app]` extra, macOS shell only) |
| bundled Python packages | none — each app's `pyproject.toml` builds its own venv via `uv sync` |
| uv | not bundled, downloaded on first use (0.12.13, sha256-verified); `FUSED_RENDER_BUNDLE_UV=1` bundles it |

## fused API

Supported — identical semantics to full fused-render:

| member | notes |
| --- | --- |
| `fused.runPython(py, params, opts?)` | `main(**params)` in the app's own venv; stale-call supersession via `opts.key`, `opts.signal` |
| `fused.params.get / getAll / set / onChange` | URL-backed, reserved `_keys` hidden, batched history writes |
| `fused.readFile(path)` | text |
| `fused.stat(path)` | `{path, name, is_dir, size, mtime, writable}` |
| `fused.writeFile(path, content, opts?)` | `expectedMtime` lock (409 → `type: "conflict"`), `create` (409 → `type: "exists"`), read-only → `type: "readonly"` |
| `fused.rawUrl(path)` | `/api/fs/raw`, Range requests honoured |
| `fused.env` / `fused.device` / `fused.lite` | `"local"` / `"desktop"` / `true` |
| `fused.ai.text({prompt, ...})` | **Claude tier only**, via the local `claude` CLI (one `claude -p` per call). Options: `model` (`haiku` default, `sonnet`, `opus`, `fable`, or a `claude-*` id), `systemPrompt`, `effort` (`low` = no thinking, `medium`/`high`/`xhigh`), `onChunk` (streams NDJSON over chunked HTTP, no socket), `abortSignal` (aborting kills the CLI process). `temperature`/`maxTokens`/`topP` dropped with `warnings[]`; `history`/`raw`/`images` → `bad_request`. Result frame identical to fused-render. Errors: `ai_unavailable` (no `claude` binary), `bad_request`, `unavailable`, `ai_error`, `timeout` (600 s), `cancelled` |
| `fused.ai.models.list()` / `catalog()` | Claude catalog only; `catalog().unsupported` lists the local capabilities |
| `fused.ai.cancel()` | resolves `false` (cancel Claude calls with `abortSignal`) |

Not supported — touching any of these throws `Error("<name> is not supported on fused-render-lite")` with `err.type === "unsupported"`:

| member | full fused-render role |
| --- | --- |
| `fused.ai.image / video / transcribe / embed` | local inference — reject (async) with `type: "unavailable"`, not a sync throw, so `catch` branches keep working |
| `fused.ai.models.load / download / unload` | local model management — reject `unavailable` |
| `fused.ai.text` with `provider: "local"` or `"apple"`, or a repo-id/.gguf model | reject `unavailable` |
| `fused.capture.*` | screen / audio / screenshot |
| `fused.fileIndex.*` | filesystem index queries |
| `fused.daemon.*` | folder background daemons |
| `fused.trackJob`, `fused.watchJob` | job tracking |
| `fused.uploadFile`, `fused.mkdir` | binary upload, directories |
| `fused.autoReload` | live reload on file change |
| `fused.snapshot` | git snapshot resolution |

The non-AI namespaces are Proxies, so `fused.capture.screen` throws on the property read, not only on call. `fused.ai` is a real object: its unsupported verbs reject asynchronously with `unavailable`, matching the fused-render contract pages already handle. An app that needs any of these belongs in full fused-render.

## Server routes

`GET /`, `GET /open?_file=`, `GET /render?path=`, `POST /api/open`, `GET /api/open/status`, `POST /api/drop`, `POST /api/run`, `GET /api/fs/raw`, `GET /api/fs/stat`, `POST /api/fs/write`, `GET /api/health`, `POST /api/ai` (JSON, or chunked NDJSON with `stream: true`), `GET /api/ai/runtime`, `GET /api/ai/catalog`, `POST /api/ai/cancel`. Mutating POSTs require `X-Fused: 1`. Binds 127.0.0.1 only.

## History

| version | DMG | .app | note |
| --- | --- | --- | --- |
| 0.1.0 | 12 MB | 25 MB | first lite build; fused-render's bundled set was ~400 MB installed |
| 0.2.0 | (see Build) | (see Build) | `fused.ai.text` on the Claude CLI tier, streaming; no local inference |
