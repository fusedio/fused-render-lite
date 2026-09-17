---
name: making-a-release
description: Use when cutting a new fused-render-app (Render App) release, bumping the version, or creating a release tag — bumps __version__, pushes to origin main, tags vX.Y.Z to trigger the DMG build/release workflow, then records the shipped DMG size in STATUS.md.
---

# Making a Release

## Overview

A release is: bump `__version__`, land it on `main` of `origin`
(fusedio/fused-render-lite), tag that commit `vX.Y.Z`, push the tag. The tag
push triggers `.github/workflows/release.yml`: `prepare-release` creates the
GitHub Release, then the macos-26 job builds, signs, (notarizes), uploads
`RenderApp-X.Y.Z.dmg` to S3 (`fused-render` bucket, `render-app-dmgs/`
prefix, served at `https://d2ic19jpchjovp.cloudfront.net/render-app-dmgs/`)
and attaches DMG + wheel to the Release, and `bump-homebrew` pushes the new
version + sha256 (with the CDN url) into `Casks/render-app.rb` of
fusedio/homebrew-tap (needs the `TAP_PUSH_TOKEN` repo secret). Afterwards, add the shipped DMG
size to the table in `STATUS.md` — every version has a row.

**Remotes.** `origin` is fusedio/fused-render-lite. `main` is not protected:
direct pushes are fine, no bump PR needed.

**Single source of truth:** the version lives ONLY in
`fused_render_app/__init__.py`. `pyproject.toml` derives it (`[tool.hatch.version]`).

**Invariant:** tag name == `__version__`. `v0.6.1` ⟺ `__version__ = "0.6.1"`.
`release.yml` also refuses a tag whose commit is not on `main`.

## Steps

1. **Start clean, current.**
   ```bash
   git switch main
   git pull --ff-only origin main
   git status --porcelain            # must be empty
   ```
2. **Pick the version.**
   ```bash
   grep __version__ fused_render_app/__init__.py
   git tag --sort=-creatordate | head -1
   ```
3. **Bump** `__version__` in `fused_render_app/__init__.py` (only file). Write a
   `## X.Y.Z` section in `STATUS.md` describing what changed (size row comes later).
4. **Test.** `.venv/bin/python -m pytest -q` — all green before tagging.
5. **Commit, tag, push** (branch first, then tag: the tag guard checks the commit is on main).
   ```bash
   git commit -am "X.Y.Z: <what changed>"
   git tag vX.Y.Z
   git push origin main
   git push origin vX.Y.Z
   ```
6. **Watch the release.**
   ```bash
   RUN=$(gh run list -R fusedio/fused-render-lite --workflow release --limit 1 --json databaseId -q '.[0].databaseId')
   gh run watch -R fusedio/fused-render-lite "$RUN" --exit-status
   gh release view vX.Y.Z -R fusedio/fused-render-lite --json assets -q '.assets[] | "\(.name) \(.size)"'
   ```
   Build log lines worth checking: `checked 245, missing 0` (stdlib probes),
   `highest minos in the bundle: 11.0 (floor 14.0)`, `uv 0.12.x` (bundled uv smoke).
7. **Record the size.** Add a row at the TOP of the `Size by version` table in
   `STATUS.md` (newest first): `| X.Y.Z | NN.NN MB (N,NNN,NNN B) | ±Δ | NN MB | what changed |`.
   The unpacked .app size is in the log (`==> app size:`). Commit `STATUS: vX.Y.Z shipped DMG size`, push.

## Rebuilding an existing tag

Tags are immutable but a release can be rebuilt (e.g. after adding signing
secrets) without a new version:

```bash
gh workflow run release -R fusedio/fused-render-lite --ref vX.Y.Z -f tag=vX.Y.Z
```

`--ref` must be the tag: the workflow checks out the ref it runs on.
The upload uses `--clobber`, so the DMG asset is replaced in place.

## Signing / notarization

The DMG is **ad-hoc signed** unless the repo has the 7 secrets
(`CODESIGN_CERT_P12`, `CODESIGN_CERT_PASSWORD`, `CODESIGN_IDENTITY`,
`KEYCHAIN_PASSWORD`, `NOTARY_API_KEY_P8`, `NOTARY_API_KEY_ID`,
`NOTARY_API_ISSUER_ID`). GitHub secrets are write-only: copy them from the
originals, never from fused-render's repo via API. Check with
`gh secret list -R fusedio/fused-render-lite`. With secrets present the job
also verifies the stapled ticket (`stapler validate` + `spctl`).

## In-app update manifest

The packaged app polls `https://d2ic19jpchjovp.cloudfront.net/render-app-dmgs/latest.json`
(`fused_render_app/update/mac.py`) and shows the launcher page's update
banner when it names a newer version. The release job's "Publish signed
update manifest" step writes it (`scripts/generate_update_manifest.py`, signed
with the **8th secret** `FUSED_RENDER_UPDATE_SIGNING_KEY` — Render App's own
Ed25519 key, NOT fused-render's; the public half is pinned in
`fused_render_app/update/common.py`). Without the secret the step logs a
warning and skips: the release still ships, but installed apps never learn
about it. GitHub secrets are write-only, so the seed must also live in the
team password manager. To rotate: `python3 scripts/generate_update_manifest.py keygen`,
paste the seed into the secret and the public key into `common.PUBLIC_KEY`,
and ship that in a release the OLD key still signs (installed apps verify
with the key they were built with).
Check after a release: `curl -s https://d2ic19jpchjovp.cloudfront.net/render-app-dmgs/latest.json`.

## Quick Reference

| Thing | Value |
|-------|-------|
| Version source | `fused_render_app/__init__.py` → `__version__` |
| Tag format | `vX.Y.Z` (== `__version__`, commit on main) |
| Remote | `origin` = fusedio/fused-render-lite |
| Release trigger | tag push → `release.yml`; rebuild with `gh workflow run release --ref vX.Y.Z -f tag=vX.Y.Z` |
| Artifacts | `RenderApp-X.Y.Z.dmg`, `fused_render_app-X.Y.Z-py3-none-any.whl` on the Release; DMG also at `https://d2ic19jpchjovp.cloudfront.net/render-app-dmgs/RenderApp-X.Y.Z.dmg` (S3 via OIDC role `github_render_app_role`) |
| Homebrew | `bump-homebrew` job → fusedio/homebrew-tap `Casks/render-app.rb` (url = CDN copy); check `brew update && brew info --cask fusedio/tap/render-app` |
| Update manifest | `render-app-dmgs/latest.json` on the CDN, signed with `FUSED_RENDER_UPDATE_SIGNING_KEY` (skipped with a warning if unset) |
| After release | size row in `STATUS.md` |
| Do NOT edit | `pyproject.toml` version |

## Common Mistakes

- **Pushing the tag before the branch.** The tag guard fails: commit not on main.
- **Tag ≠ `__version__`.** Bump and commit before tagging.
- **Forgetting the STATUS size row**, or inserting it out of order (table is newest first).
- **Raising `FUSED_RENDER_MACOS_FLOOR` in CI** to get past a minos failure. Fix the source; the local Homebrew-python override is dev-only.
