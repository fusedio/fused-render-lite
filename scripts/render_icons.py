"""Render FusedBot's icon PNGs from the hand-written SVGs in fused_render_app/static/.

    uv run --no-project --with resvg-py --with pillow python scripts/render_icons.py

Run it after editing `fusedbot-icon.svg` or `menubar.svg`, and commit the PNGs
it writes (the build does not render SVG; build_dmg.sh only resizes the 1024 px
master into the .icns):

    fusedbot-icon-1024.png   the app icon master (macOS grid, transparent corners)
    fusedbot-icon-64.png     the favicon: the same drawing cropped to the rounded square
    menubar.png              the menu-bar template icon, 36 px (drawn at 20 pt)
    menubar@2x.png           the same at 72 px (macapp._menubar_image pairs them)

resvg (not a browser) so the ground is truly transparent and the output is
byte-stable across machines.
"""
import io
import os
import sys

import resvg_py
from PIL import Image

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fused_render_app", "static")


def render(svg: str, size: int) -> Image.Image:
    png = resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)
    return Image.open(io.BytesIO(bytes(png))).convert("RGBA")


def main() -> int:
    with open(os.path.join(STATIC, "fusedbot-icon.svg"), encoding="utf-8") as f:
        master = f.read()
    with open(os.path.join(STATIC, "menubar.svg"), encoding="utf-8") as f:
        menubar = f.read()
    outputs = {"fusedbot-icon-1024.png": render(master, 1024)}
    # At 64 px every pixel counts: drop the macOS grid's 100 px margin.
    crop = master.replace('viewBox="0 0 1024 1024"', 'viewBox="100 100 824 824"', 1)
    if crop == master:
        sys.exit("fusedbot-icon.svg: expected viewBox=\"0 0 1024 1024\"")
    outputs["fusedbot-icon-64.png"] = render(crop, 64)
    outputs["menubar.png"] = render(menubar, 36)
    outputs["menubar@2x.png"] = render(menubar, 72)
    for name, im in outputs.items():
        path = os.path.join(STATIC, name)
        im.save(path, optimize=True)
        corner = im.getpixel((0, 0))
        if corner[3] != 0:
            sys.exit(f"{name}: corner pixel is not transparent: {corner}")
        print(f"{name}: {im.size[0]}x{im.size[1]}, {os.path.getsize(path)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
