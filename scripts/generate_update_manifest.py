"""Emit the signed update manifest (latest.json) the in-app updater polls.

    python3 scripts/generate_update_manifest.py <version> <dmg> <base-url> <output>
    python3 scripts/generate_update_manifest.py keygen

Run from release CI (.github/workflows/release.yml) after the DMG is on S3.
The ed25519 private key (base64 raw 32-byte seed) comes from the
FUSED_RENDER_UPDATE_SIGNING_KEY env var — Render App's own key, not
fused-render's; the matching public key is pinned in
fused_render_app/update/common.py. The signature covers a domain-separated
`version\\nsha256` line (context `render-app-update`, distinct from
fused-render's), so a CDN/bucket
compromise cannot forge a manifest pointing the updater at a different DMG.

Stdlib only: the signing primitive is fused_render_app.update.ed25519, so the
script needs no third-party package on the runner. `keygen` prints a fresh
seed + public key (base64) for a repo that wants its own key; paste the seed
into the secret and the public key into common.PUBLIC_KEY.
"""
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fused_render_app.update import common, ed25519  # noqa: E402

_SCHEMA = 1


def main() -> None:
    if sys.argv[1:] == ["keygen"]:
        seed = ed25519.generate_seed()
        print("FUSED_RENDER_UPDATE_SIGNING_KEY =", base64.b64encode(seed).decode())
        print("PUBLIC_KEY =", base64.b64encode(ed25519.public_key(seed)).decode())
        return
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    version, dmg, base_url, output = sys.argv[1:5]
    key_b64 = os.environ.get("FUSED_RENDER_UPDATE_SIGNING_KEY")
    if not key_b64:
        raise SystemExit("FUSED_RENDER_UPDATE_SIGNING_KEY is not set")
    seed = base64.b64decode(key_b64)

    dmg_path = Path(dmg)
    sha256 = hashlib.sha256(dmg_path.read_bytes()).hexdigest()
    signature = ed25519.sign(seed, common.signing_message(version, sha256))
    # Refuse to publish a manifest the shipped app would not accept.
    if ed25519.public_key(seed) != common.PUBLIC_KEY:
        raise SystemExit("signing key does not match the public key pinned in "
                         "fused_render_app/update/common.py")
    common.verify_signature(version, sha256, base64.b64encode(signature).decode())

    manifest = {
        "schema": _SCHEMA,
        "version": version,
        "url": f"{base_url.rstrip('/')}/{dmg_path.name}",
        "sha256": sha256,
        "signature": base64.b64encode(signature).decode(),
    }
    Path(output).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output}: v{version} -> {manifest['url']}")


if __name__ == "__main__":
    main()
