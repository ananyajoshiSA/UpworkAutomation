"""Generate a placeholder Windows .ico for the packaged app.

This produces ``build_assets/app.ico`` — a clean, neutral compass mark on the
brand blue (#2563EB), matching the app's in-UI page icon (🧭). It is a
PLACEHOLDER: drop a real, designed ``app.ico`` at the same path to override it.

The app's window itself uses an emoji page icon, so this ``.ico`` is only needed
for a Windows desktop shortcut (see DEVELOPER.md). (Re)generate or customize it
with:  ``python build_assets/make_icon.py``

Requires Pillow (already a transitive dependency of the app via
streamlit-paste-button / pdfplumber).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ICO_PATH = Path(__file__).resolve().parent / "app.ico"

# Brand palette (mirrors .streamlit/config.toml primaryColor).
BRAND_BLUE = (37, 99, 235, 255)
WHITE = (255, 255, 255, 255)
NEEDLE_RED = (228, 70, 56, 255)

# Render large, then let Pillow downscale into the multi-resolution .ico so the
# small sizes stay crisp in Explorer, the taskbar, and the title bar.
SIZE = 256
ICO_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]


def _rounded_square(draw: ImageDraw.ImageDraw, size: int) -> None:
    radius = int(size * 0.22)
    draw.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=radius, fill=BRAND_BLUE)


def _compass(draw: ImageDraw.ImageDraw, size: int) -> None:
    cx = cy = size / 2
    ring_r = size * 0.30
    width = max(2, int(size * 0.035))
    # Outer ring.
    draw.ellipse(
        [(cx - ring_r, cy - ring_r), (cx + ring_r, cy + ring_r)],
        outline=WHITE,
        width=width,
    )
    # Compass needle: a north (red) and south (white) kite around the centre.
    tip = size * 0.225
    half = size * 0.085
    draw.polygon(
        [(cx, cy - tip), (cx - half, cy), (cx, cy), (cx + half, cy)], fill=NEEDLE_RED
    )
    draw.polygon(
        [(cx, cy + tip), (cx - half, cy), (cx, cy), (cx + half, cy)], fill=WHITE
    )
    # Hub.
    hub = max(2, int(size * 0.03))
    draw.ellipse([(cx - hub, cy - hub), (cx + hub, cy + hub)], fill=WHITE)


def make_icon(path: Path = ICO_PATH) -> Path:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _rounded_square(draw, SIZE)
    _compass(draw, SIZE)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="ICO", sizes=ICO_SIZES)
    return path


if __name__ == "__main__":
    out = make_icon()
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
