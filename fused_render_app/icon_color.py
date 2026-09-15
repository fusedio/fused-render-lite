"""Recolour an app's ``icon.svg`` for the live theme.

Port of fused-render's ``frontend/src/platform/lib/icon-color.ts``. An icon
picked in fused-render's IconPicker is a lucide glyph stroked in
``currentColor`` with the COLOUR'S NAME on the root (``data-fused-color="red"``)
and a ``prefers-color-scheme`` fallback ``<style>``. The shell substitutes the
theme's hex for ``currentColor`` before the browser sees the file, so the icon
follows the theme even where a ``<img>`` cannot reach the page's CSS. The
menu-bar dock draws icons through ``<img>`` too, so the same swap happens here,
in ``/api/dock/icon?theme=light|dark``.

Files without the marker — emoji glyphs, hand-authored icons — pass through
untouched, exactly as fused-render draws them.

Pure: no I/O, no server imports. Keep the palette in step with
``ICON_COLOR_HEX`` in the fused-render source.
"""
from __future__ import annotations

import re

THEMES = ("light", "dark")

# name -> (light hex, dark hex). The first five are what the picker offers
# today; gray/brown/orange/purple/pink are legacy names an existing icon.svg
# may still declare and must keep following the theme.
ICON_COLOR_HEX: dict[str, tuple[str, str]] = {
    "default": ("#61656c", "#9aa0a6"),
    "gray": ("#787774", "#9b9b9b"),
    "brown": ("#9f6b53", "#ba856f"),
    "yellow": ("#5f7300", "#E5FF44"),
    "orange": ("#d9730d", "#c77d48"),
    "green": ("#448361", "#529e72"),
    "blue": ("#337ea9", "#5e87c9"),
    "purple": ("#9065b0", "#9d68d3"),
    "pink": ("#c14c8a", "#d15796"),
    "red": ("#d44c47", "#df5452"),
}

# The rounded plate behind a picker-written glyph (fused-render #1159):
# written into the file as ``var(--fused-bg)`` and swapped for the literal hex
# alongside currentColor. White on light, black on dark.
ICON_BG_HEX: tuple[str, str] = ("#ffffff", "#000000")

_ROOT = re.compile(r"<svg\b[^>]*>")
_MARKER = re.compile(r'\sdata-fused-color="([a-z]+)"')


def read_icon_color(svg: str) -> str | None:
    """The colour name the svg's ROOT declares, or None (no marker, marker on
    a nested element, unknown name). The file is author-controlled, so an
    unknown value is "no marker", not an error."""
    root = _ROOT.search(svg)
    if not root:
        return None
    m = _MARKER.search(root.group(0))
    if not m or m.group(1) not in ICON_COLOR_HEX:
        return None
    return m.group(1)


def theme_icon_svg(data: bytes, theme: str) -> bytes:
    """``data`` with every ``currentColor`` resolved to the marked colour's hex
    and every ``var(--fused-bg)`` plate to the theme's plate hex. Unchanged
    when there is no marker, the theme is not ``light``/``dark``, or the bytes
    are not UTF-8."""
    if theme not in THEMES:
        return data
    try:
        svg = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    color = read_icon_color(svg)
    if color is None:
        return data
    i = THEMES.index(theme)
    svg = svg.replace("currentColor", ICON_COLOR_HEX[color][i])
    svg = svg.replace("var(--fused-bg)", ICON_BG_HEX[i])
    return svg.encode("utf-8")
