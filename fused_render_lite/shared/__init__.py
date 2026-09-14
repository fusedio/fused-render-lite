"""Stdlib-only helper modules a .fused app's Python side may import — fused-render's
`templates/shared`, copied verbatim. Not importable as a package by apps: the
server publishes this directory's path as `shared` in `<home>/server.json`, and
an app does `sys.path.insert(0, info["shared"])` (fused-render's background-apps
contract). `appenv.py`, `fused_ai.py`, `background_app.py`.
"""
