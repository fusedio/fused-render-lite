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
| `fused.ai.text({prompt, ...})` | Claude tier only, through the local `claude` CLI; streams with `onChunk` |
| `fused.uploadFile(path, blob)` / `fused.mkdir(path)` | binary save, directories |
| `fused.trackJob(spec)` / `fused.watchJob(id)` | in-process job rows; survive a reload, cancellable |
| `fused.autoReload(false)` | accepted, no-op; `autoReload(true)` throws (no live reload) |

Every other member the full fused-render runtime has (`capture`,
`fileIndex`, `daemon`, `snapshot`)
is **not supported**. There are no stubs: calling one, or reading any
property of `fused.capture` / `fused.fileIndex` / `fused.daemon`,
throws `<name> is not supported on fused-render-lite` and logs it to the
console. An app that needs those belongs in full fused-render.

## Python environments

Nothing is bundled. Each `.fused` app carries its own `pyproject.toml`; on
open, `uv sync` builds a venv for it under `~/.fused-render-lite/venvs/` and
`runPython` runs inside it. An app without a `pyproject.toml` runs on a
stdlib-only Python and its imports will fail with a message saying so.

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
Macs need right-click → Open). `FUSED_RENDER_SIGN=1` switches to Developer ID
signing with the hardened runtime, and `FUSED_RENDER_NOTARY_PROFILE` also
notarizes and staples. Not wired up for now.

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
