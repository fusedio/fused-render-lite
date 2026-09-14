# fused-render-lite

Opens a `.fused` single-file app. Nothing else.

Double-click a `.fused` in Finder (or drop one onto the placeholder page) and
the app's entry page renders in your browser. The URL carries the file:

```
http://127.0.0.1:8765/open?_file=/Users/you/Downloads/app.fused&n=80
```

Everything after `_file` is the app's own `fused.params` state.

## What it supports

The page runtime exposes these `fused.*` members:

| API | Notes |
| --- | --- |
| `fused.runPython(py, params, opts?)` | runs `main(**params)` from the app's own venv, 600 s cap |
| `fused.params.get/getAll/set/onChange` | URL-backed state, same semantics as fused-render |
| `fused.readFile(path)` | text |
| `fused.stat(path)` | `{path, name, is_dir, size, mtime, writable}` |
| `fused.writeFile(path, content, opts?)` | optimistic lock + create-only, as in fused-render |
| `fused.rawUrl(path)` | bytes URL, Range requests honoured |
| `fused.ai.text / image / video / transcribe / embed`, `fused.ai.models.*`, `fused.ai.cancel` | fused-render's AI subsystem: Claude CLI tier + local runners (see AI below) |
| `fused.uploadFile(path, blob)` / `fused.mkdir(path)` | binary save, directories |
| `fused.trackJob(spec)` / `fused.watchJob(id)` | in-process job rows; survive a reload, cancellable |
| `fused.autoReload(false)` | accepted, no-op; `autoReload(true)` throws (no live reload) |

Every other member the full fused-render runtime has (`capture`,
`fileIndex`, `daemon`, `snapshot`)
is **not supported**. There are no stubs: calling one, or reading any
property of `fused.capture` / `fused.fileIndex` / `fused.daemon`,
throws `<name> is not supported on fused-render-lite` and logs it to the
console. An app that needs those belongs in full fused-render.

## AI

`fused.ai.*` is fused-render's AI subsystem, copied in. Two tiers:

- **Claude** (`haiku`/`sonnet`/`opus`/`fable`, the default): runs `claude -p`
  from Claude Code, so the machine needs the `claude` CLI installed and logged
  in. One warm process, reset between calls.
- **Local** (a Hugging Face repo id or `.gguf`, or `provider: "local"`): text,
  image, video, transcribe, embed. Each backend is a runner folder under
  `fused_render_lite/ai/runners/` with its own `pyproject.toml`; the first call
  builds its venv with `uv sync`, downloads the model into the Hugging Face
  cache and spawns a worker process the server talks HTTP to. Nothing ML ships
  in the DMG. Pages get `model_loading` + a `jobId` to `watchJob` while that
  happens, then retry — fused-render's contract, unchanged.

The Apple-Intelligence tier needs a Swift helper the lite build does not
compile; it answers `unavailable`. Streaming is NDJSON over chunked HTTP.

## Python environments

No packages are bundled. The DMG ships one CPython 3.12 (py2app's real
interpreter at `Contents/MacOS/python`, whole stdlib, self-locating through a
`Contents/lib` symlink — packaged exactly as fused-render's FusedRender.app).
Every environment is built on it. Each `.fused` app carries its own
`pyproject.toml`; on open, `uv sync --python <that interpreter>` builds a venv
for it under `~/.fused-render-lite/venvs/` and `runPython` runs inside it. An app without a `pyproject.toml` runs in one
shared "legacy" venv holding fused-render's old bundled set (numpy, pandas,
requests, pillow, openpyxl, python-pptx, msgpack, fpdf2, drain3), also built
on first use, so older `.fused` exports keep working.

`uv` is looked for at `FUSED_RENDER_LITE_UV`, next to the app, on `PATH`,
and failing those is downloaded once (pinned version, sha256-verified) into
`~/.fused-render-lite/bin/`.

Version, DMG/app size and the full supported/unsupported API table live in
[STATUS.md](STATUS.md).

## Run from source

```
pip install -e ".[dev]"
fused-render-lite ~/Downloads/app.fused
pytest
```

## Build the macOS app

```
pip install ".[app]"            # rumps + pyobjc, for the menu-bar shell
bash scripts/build_dmg.sh       # dist/FusedRenderLite-<version>.dmg
```

The DMG is ad-hoc signed by default (runs on the building machine; other
Macs need right-click → Open). `FUSED_RENDER_SIGN=1` or a
`FUSED_RENDER_CODESIGN_IDENTITY` switches to Developer ID signing with the
hardened runtime, and `FUSED_RENDER_NOTARY_PROFILE` (a `notarytool`
keychain profile) additionally notarizes and staples.

### Release pipeline (GitHub Actions)

Pushing a `v*` tag runs `.github/workflows/release.yml`, the same flow as
fusedio/fused-render: an ephemeral keychain gets the Developer ID cert and an
App Store Connect API key, `build_dmg.sh` signs + notarizes + staples, the
ticket is verified, and the DMG lands on the GitHub Release. It needs these
repository secrets (copy them from fusedio/fused-render):

| secret | what |
| --- | --- |
| `CODESIGN_CERT_P12` | base64 of the Developer ID Application `.p12` |
| `CODESIGN_CERT_PASSWORD` | its password |
| `CODESIGN_IDENTITY` | the cert's SHA-1 (`security find-identity -v -p codesigning`) |
| `KEYCHAIN_PASSWORD` | any string; unlocks the ephemeral keychain |
| `NOTARY_API_KEY_P8` | App Store Connect API key (`.p8` contents) |
| `NOTARY_API_KEY_ID` / `NOTARY_API_ISSUER_ID` | its key id and issuer id |

Without them the workflow still runs and publishes an ad-hoc-signed DMG.

## Layout

```
fused_render_lite/
  appfile.py      open a .fused (v2 container or legacy v1 zip) into ~/.fused-render-lite/apps
  container.py    the FUSEDAPP v2 format (stdlib)
  env.py          uv lookup/download, per-app `uv sync`, running a .py in its venv
  server.py       the HTTP surface (http.server; binds 127.0.0.1)
  cli.py          `fused-render-lite [file] [--port] [--no-browser]`
  macapp.py       menu-bar shell; Finder open events -> browser
  _child.py       worker: import the .py, call main(**params), print JSON
  static/         runtime.js, placeholder (index.html), open page (open.html)
```

State lives in `~/.fused-render-lite/` (override with `FUSED_RENDER_LITE_HOME`).
