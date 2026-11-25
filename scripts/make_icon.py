"""Draw the tidytabs extension icon: a happy browser window with tabs and a sparkle.

Run from the project folder:
    uv run --with pillow python scripts/make_icon.py

The script draws at 1024 px and scales down, so the small sizes stay smooth.
It writes extension/icons/icon{16,32,48,128}.png.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
TEAL_TOP = (20, 184, 166)
TEAL_BOTTOM = (14, 116, 144)
WHITE = (255, 255, 255)
MINT = (167, 243, 228)
INK = (19, 78, 74)
YELLOW = (253, 224, 71)
CHEEK = (251, 164, 175)
OUT = Path(__file__).resolve().parents[1] / "extension" / "icons"


def gradient_background() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    fill = Image.new("RGBA", (S, S))
    px = ImageDraw.Draw(fill)
    for y in range(S):
        t = y / (S - 1)
        px.line([(0, y), (S, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TEAL_TOP, TEAL_BOTTOM)))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=230, fill=255)
    img.paste(fill, (0, 0), mask)
    return img


def tab(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], color) -> None:
    """A browser tab: rounded at the top, square at the bottom."""
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=44, fill=color)
    draw.rectangle((x0, y1 - 44, x1, y1), fill=color)


def sparkle(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color) -> None:
    """A four-point star."""
    points = []
    for i in range(8):
        angle = math.pi / 2 + i * math.pi / 4
        radius = r if i % 2 == 0 else r * 0.26
        points.append((cx + radius * math.cos(angle), cy - radius * math.sin(angle)))
    draw.polygon(points, fill=color)


def draw_icon() -> Image.Image:
    img = gradient_background()
    d = ImageDraw.Draw(img)
    # tabs: two background tabs, then the active tab that joins the window body
    tab(d, (420, 250, 640, 400), MINT)
    tab(d, (652, 250, 860, 400), MINT)
    tab(d, (164, 236, 410, 400), WHITE)
    # window body
    d.rounded_rectangle((164, 380, 860, 840), radius=70, fill=WHITE)
    d.rectangle((164, 380, 410, 460), fill=WHITE)
    # happy face
    d.ellipse((350, 520, 430, 600), fill=INK)
    d.ellipse((594, 520, 674, 600), fill=INK)
    d.ellipse((290, 620, 360, 670), fill=CHEEK)
    d.ellipse((664, 620, 734, 670), fill=CHEEK)
    d.arc((400, 520, 624, 740), start=25, end=155, fill=INK, width=40)
    # sparkles: tidy!
    sparkle(d, 872, 176, 150, YELLOW)
    sparkle(d, 712, 118, 62, WHITE)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw_icon()
    for size in (16, 32, 48, 128):
        big.resize((size, size), Image.LANCZOS).save(OUT / f"icon{size}.png")
        print(f"wrote {OUT / f'icon{size}.png'}")


if __name__ == "__main__":
    main()
