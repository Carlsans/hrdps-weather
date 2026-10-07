"""Draws packaging/icon.ico (multi-resolution). Run: python packaging/make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw


def draw(size=256):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = size / 256
    d.rounded_rectangle((8 * s, 8 * s, 248 * s, 248 * s), 48 * s, fill=(30, 30, 46, 255))
    d.ellipse((120 * s, 40 * s, 212 * s, 132 * s), fill=(249, 226, 175, 255))            # sun
    for box in ((44, 104, 132, 192), (92, 80, 188, 176), (136, 112, 220, 196)):          # cloud
        d.ellipse([c * s for c in box], fill=(205, 214, 244, 255))
    d.rounded_rectangle((70 * s, 150 * s, 200 * s, 196 * s), 22 * s, fill=(205, 214, 244, 255))
    for x in (84, 124, 164):                                                             # rain
        d.rounded_rectangle((x * s, 200 * s, (x + 12) * s, 232 * s), 6 * s, fill=(137, 180, 250, 255))
    return im


if __name__ == "__main__":
    out = Path(__file__).with_name("icon.ico")
    draw().save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(out)
