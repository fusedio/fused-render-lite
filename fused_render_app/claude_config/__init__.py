"""The slice of fused-render's `claude_config` package the copied
`routes/claude_sessions.py` touches: `preferences.main("patch", ...)` to write
the global model / effort defaults into Claude Code's own settings file, and
`lib.load_catalog()` for the model vocabulary. fused-render's full package is
a settings editor with git history; Render App only needs the two calls."""
