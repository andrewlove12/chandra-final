"""Draws the icons; run once, the .ico files are committed.

installer/app.ico        the app (shortcuts, window, uninstall entry): a spiral notebook
                         with grey handwriting and red transcription lines.
installer/installer.ico  Setup.exe: the same notebook with a green download badge.

Each size is drawn separately (not shrunk from 256 px) so the small sizes used by
the taskbar, Start menu and Explorer stay sharp: less detail, thicker lines.
"""
import os

from PIL import Image, ImageDraw

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
INK = (45, 55, 75, 255)
PAGE = (252, 252, 246, 255)
GREY = (125, 130, 140, 255)
RED = (214, 34, 34, 255)
GREEN = (30, 150, 70, 255)
HERE = os.path.dirname(os.path.abspath(__file__))


def notebook(size: int) -> Image.Image:
    # Draw at 4x and downsample for smooth edges; coordinates are on a 256 grid.
    k = 4
    big = size * k
    s = big / 256
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    small = size <= 32
    outline = max(k, round((14 if small else 8) * s))
    left = 46 if small else 52
    d.rounded_rectangle([left * s, 12 * s, 236 * s, 244 * s], radius=18 * s, fill=PAGE, outline=INK, width=outline)

    if small:
        # Spiral as a few solid dots; two thick line pairs.
        for y in (58, 128, 198):
            d.ellipse([22 * s, (y - 16) * s, 70 * s, (y + 16) * s], fill=INK)
        rows = [(70, 130), (150, 100)]
        line_w = 26
        gap = 34
        x0 = 88
    else:
        for y in range(44, 230, 30):
            d.ellipse([30 * s, (y - 9) * s, 70 * s, (y + 9) * s], outline=INK, width=max(k, round(7 * s)))
        rows = [(54, 132), (96, 108), (138, 124), (180, 92)]
        line_w = 12
        gap = 17
        x0 = 88
    for y, w in rows:
        d.line([x0 * s, y * s, (x0 + w) * s, y * s], fill=GREY, width=round(line_w * s))
        d.line([x0 * s, (y + gap) * s, (x0 + w * 0.8) * s, (y + gap) * s], fill=RED, width=round(line_w * s))
    return img.resize((size, size), Image.LANCZOS)


def with_badge(size: int) -> Image.Image:
    img = notebook(size)
    k = 4
    big = img.resize((size * k, size * k), Image.LANCZOS)
    s = size * k / 256
    d = ImageDraw.Draw(big)
    # Green circle with a white down arrow, bottom-right.
    cx, cy, r = 188, 188, 66
    d.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s], fill=GREEN,
              outline=(255, 255, 255, 255), width=round(10 * s))
    shaft = 22 if size > 32 else 30
    d.rectangle([(cx - shaft / 2) * s, (cy - 40) * s, (cx + shaft / 2) * s, (cy + 4) * s], fill=(255, 255, 255, 255))
    d.polygon([((cx - 36) * s, (cy - 4) * s), ((cx + 36) * s, (cy - 4) * s), (cx * s, (cy + 40) * s)],
              fill=(255, 255, 255, 255))
    return big.resize((size, size), Image.LANCZOS)


def save_ico(draw, name: str) -> None:
    images = [draw(n) for n in SIZES]
    out = os.path.join(HERE, name)
    images[-1].save(out, format="ICO", sizes=[(n, n) for n in SIZES], append_images=images[:-1])
    print("wrote", out)


if __name__ == "__main__":
    save_ico(notebook, "app.ico")
    save_ico(with_badge, "installer.ico")
