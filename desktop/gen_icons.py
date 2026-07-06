"""One-shot icon generator for the desktop shell (stdlib only, no Pillow).

Draws the app mark — five waveform bars — as:
  - app-icon.png (1024x1024, dark rounded square): source for `tauri icon`,
    which derives src-tauri/icons/* (icns and friends) from it.
  - src-tauri/icons/tray.png (44x44, black on transparent): the menu-bar
    template image; macOS recolors it for light/dark menu bars.

Run from desktop/:  python3 gen_icons.py && npx tauri icon app-icon.png
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

HERE = Path(__file__).parent


def write_png(path: Path, width: int, height: int, pixels: list[list[tuple]]) -> None:
    raw = b"".join(
        b"\x00" + bytes(channel for px in row for channel in px) for row in pixels
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data))
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def coverage(dist: float) -> float:
    """1 inside the shape, 0 outside, linear ramp across one pixel."""
    return max(0.0, min(1.0, 0.5 - dist))


def rounded_rect_dist(x: float, y: float, cx: float, cy: float, hw: float, hh: float, r: float) -> float:
    dx = max(abs(x - cx) - (hw - r), 0.0)
    dy = max(abs(y - cy) - (hh - r), 0.0)
    return (dx * dx + dy * dy) ** 0.5 - r


def vbar_dist(x: float, y: float, bx: float, cy: float, half_h: float, r: float) -> float:
    """Distance to a vertical capsule centered at (bx, cy)."""
    dy = max(abs(y - cy) - (half_h - r), 0.0)
    dx = x - bx
    return (dx * dx + dy * dy) ** 0.5 - r


BAR_OFFSETS = (-2.0, -1.0, 0.0, 1.0, 2.0)  # in units of bar spacing
BAR_HEIGHTS = (0.35, 0.65, 1.0, 0.65, 0.35)  # fraction of the tallest bar


def draw(size: int, background: tuple | None, bar_rgb: tuple,
         spacing: float, half_h_max: float, bar_r: float) -> list[list[tuple]]:
    c = size / 2
    rows = []
    for iy in range(size):
        row = []
        y = iy + 0.5
        for ix in range(size):
            x = ix + 0.5
            r, g, b, a = 0, 0, 0, 0
            if background is not None:
                bg_cov = coverage(
                    rounded_rect_dist(x, y, c, c, c - size * 0.06, c - size * 0.06, size * 0.18)
                )
                if bg_cov > 0:
                    r, g, b = background
                    a = round(255 * bg_cov)
            bar_cov = 0.0
            for off, hfrac in zip(BAR_OFFSETS, BAR_HEIGHTS):
                d = vbar_dist(x, y, c + off * spacing, c, half_h_max * hfrac, bar_r)
                bar_cov = max(bar_cov, coverage(d))
            if bar_cov > 0:
                br, bg_, bb = bar_rgb
                r = round(br * bar_cov + r * (1 - bar_cov))
                g = round(bg_ * bar_cov + g * (1 - bar_cov))
                b = round(bb * bar_cov + b * (1 - bar_cov))
                a = max(a, round(255 * bar_cov))
            row.append((r, g, b, a))
        rows.append(row)
    return rows


def main() -> None:
    write_png(
        HERE / "app-icon.png",
        1024,
        1024,
        draw(1024, background=(15, 23, 42), bar_rgb=(125, 211, 252),
             spacing=96.0, half_h_max=260.0, bar_r=34.0),
    )
    tray_dir = HERE / "src-tauri" / "icons"
    tray_dir.mkdir(parents=True, exist_ok=True)
    write_png(
        tray_dir / "tray.png",
        44,
        44,
        draw(44, background=None, bar_rgb=(0, 0, 0),
             spacing=7.0, half_h_max=14.0, bar_r=2.4),
    )
    print("wrote app-icon.png and src-tauri/icons/tray.png")


if __name__ == "__main__":
    main()
