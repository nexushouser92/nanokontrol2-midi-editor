"""Draws the app icon: assets/icon.png (window header) and assets/icon.ico (window and exe).

A mixer: rounded square with a violet-to-yellow diagonal gradient, a header row of five knobs and
four faders, drawn with dark violet lines. Drawn 4x larger and scaled down, so the edges are smooth.
"""
import os

from PIL import Image, ImageDraw

SIZE = 1024
SCALE = 4
TOP_LEFT, MIDDLE, BOTTOM_RIGHT = (196, 55, 231), (226, 128, 140), (249, 232, 28)
INK = (35, 21, 43, 255)

BODY = (0.06, 0.06, 0.94, 0.94)
RADIUS = 0.09
LINE = 0.028
HEADER_Y = 0.24
DOTS_Y, DOTS_R, DOT_LINE, DOTS_X = 0.15, 0.034, 0.017, (0.22, 0.36, 0.50, 0.64, 0.78)
FADERS = ((0.20, 0.41), (0.40, 0.52), (0.60, 0.75), (0.80, 0.54))      # (x, knob y)
FADER_TOP, FADER_BOTTOM = 0.32, 0.87
KNOB_W, KNOB_H, KNOB_R, KNOB_LINE = 0.14, 0.068, 0.034, 0.02


def gradient(size):
    """Diagonal gradient through MIDDLE, top-left to bottom-right."""
    small = Image.new("RGB", (256, 256))
    pixels = small.load()
    for y in range(256):
        for x in range(256):
            t = (x + y) / 510
            a, b, f = (TOP_LEFT, MIDDLE, t * 2) if t < 0.5 else (MIDDLE, BOTTOM_RIGHT, t * 2 - 1)
            pixels[x, y] = tuple(round(a[i] + (b[i] - a[i]) * f) for i in range(3))
    return small.resize((size, size), Image.BICUBIC)


def draw(size=SIZE):
    n = size * SCALE
    px = lambda v: v * n
    line = round(px(LINE))
    fill = gradient(n)
    image = Image.new("RGBA", (n, n), (0, 0, 0, 0))

    def paste_fill(shape, **kw):
        mask = Image.new("L", (n, n), 0)
        getattr(ImageDraw.Draw(mask), shape[0])(shape[1], fill=255, **kw)
        image.paste(fill, (0, 0), mask)

    box = tuple(px(v) for v in BODY)
    paste_fill(("rounded_rectangle", box), radius=px(RADIUS))
    d = ImageDraw.Draw(image)
    d.line((box[0], px(HEADER_Y), box[2], px(HEADER_Y)), fill=INK, width=line)
    for x in DOTS_X:
        r = px(DOTS_R)
        d.ellipse((px(x) - r, px(DOTS_Y) - r, px(x) + r, px(DOTS_Y) + r), outline=INK, width=round(px(DOT_LINE)))
    for x, _ in FADERS:
        d.line((px(x), px(FADER_TOP), px(x), px(FADER_BOTTOM)), fill=INK, width=line)
        r = line / 2
        for y in (FADER_TOP, FADER_BOTTOM):
            d.ellipse((px(x) - r, px(y) - r, px(x) + r, px(y) + r), fill=INK)
    for x, y in FADERS:
        knob = (px(x - KNOB_W / 2), px(y - KNOB_H / 2), px(x + KNOB_W / 2), px(y + KNOB_H / 2))
        paste_fill(("rounded_rectangle", knob), radius=px(KNOB_R))
        ImageDraw.Draw(image).rounded_rectangle(knob, radius=px(KNOB_R), outline=INK, width=round(px(KNOB_LINE)))
    return image.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    assets = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
    icon = draw()
    icon.save(os.path.join(assets, "icon.png"))
    icon.save(os.path.join(assets, "icon.ico"), sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("wrote icon.png and icon.ico in", assets)
