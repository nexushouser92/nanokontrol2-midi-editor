"""Anti-aliased shape sprites.

Tk canvases draw jagged edges, so rounded rectangles, discs and arrows are rendered
four times larger with Pillow and scaled down, then drawn as images with soft edges.
"""
from PIL import Image, ImageDraw

SUPERSAMPLE = 4

_QUADRANT_OFFSETS = {"tl": (0, 0), "tr": (-1, 0), "bl": (0, -1), "br": (-1, -1)}


def render(kind, w, h, r, color, bg=None):
    """Image of `kind` ("round", "oval", "chevron" or a corner: "tl", "tr", "bl", "br").

    With `bg` the shape is flattened onto that colour: an opaque image paints much faster
    than a translucent one, which matters for large shapes. A colour written "fill|border"
    gives a "round" shape a 1 px border (like a macOS push button).
    """
    s = SUPERSAMPLE
    color, _, border = color.partition("|")
    rgb = ImageDraw.ImageColor.getrgb(color)[:3]
    if kind in _QUADRANT_OFFSETS:
        w = h = r
    w, h = max(1, w), max(1, h)
    image = Image.new("RGBA", (w * s, h * s), rgb + (0,))
    draw = ImageDraw.Draw(image)
    box = (0, 0, w * s - 1, h * s - 1)
    if kind == "round" and border:
        draw.rounded_rectangle(box, radius=r * s, fill=border)
        inner = (s, s, box[2] - s, box[3] - s)
        draw.rounded_rectangle(inner, radius=max(0, r - 1) * s, fill=color)
    elif kind == "round":
        draw.rounded_rectangle(box, radius=r * s, fill=color)
    elif kind == "oval":
        draw.ellipse(box, fill=color)
    elif kind == "chevron":
        draw.polygon([(0, 0), (box[2], 0), (box[2] / 2, box[3])], fill=color)
    else:
        size = r * s
        dx, dy = _QUADRANT_OFFSETS[kind]
        draw.ellipse((dx * size, dy * size, dx * size + 2 * size - 1, dy * size + 2 * size - 1), fill=color)
    image = image.reduce(s)
    if bg is not None:
        flat = Image.new("RGBA", image.size, bg)
        flat.alpha_composite(image)
        return flat.convert("RGB")
    return image
