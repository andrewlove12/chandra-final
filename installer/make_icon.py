"""Draws installer/app.ico (a notebook page with red transcription lines). Run once; the .ico is committed."""
import os

from PIL import Image, ImageDraw


def draw(size: int) -> Image.Image:
    s = size / 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # notebook page
    d.rounded_rectangle([40 * s, 16 * s, 216 * s, 240 * s], radius=14 * s, fill=(250, 250, 245, 255),
                        outline=(60, 70, 90, 255), width=max(1, round(6 * s)))
    # spiral binding
    for y in range(40, 230, 34):
        d.ellipse([28 * s, (y - 7) * s, 52 * s, (y + 7) * s], outline=(60, 70, 90, 255), width=max(1, round(5 * s)))
    # grey "handwriting" lines with red transcription lines over them
    for i, y in enumerate(range(62, 220, 38)):
        w = (128, 104, 120, 84, 112)[i % 5]
        d.line([70 * s, y * s, (70 + w) * s, y * s], fill=(120, 125, 135, 255), width=max(1, round(7 * s)))
        d.line([70 * s, (y + 13) * s, (70 + w * 0.8) * s, (y + 13) * s], fill=(215, 35, 35, 255),
               width=max(1, round(7 * s)))
    return img


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
    big = draw(256)
    big.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", out)
