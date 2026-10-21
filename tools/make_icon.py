"""Draws assets/icon.ico (the logo shown in the window header)."""
import os

from PIL import Image, ImageDraw

SIZE = 1024
ACCENT = "#5b8cff"


def draw():
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=int(SIZE * 0.28), fill=ACCENT)
    for x, knob in ((0.30, 0.42), (0.50, 0.60), (0.70, 0.50)):
        cx = SIZE * x
        d.line((cx, SIZE * 0.22, cx, SIZE * 0.78), fill="white", width=int(SIZE * 0.045))
        r = SIZE * 0.075
        cy = SIZE * knob
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill="white")
    return image


if __name__ == "__main__":
    target = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon.ico")
    draw().save(target, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("wrote", target)
