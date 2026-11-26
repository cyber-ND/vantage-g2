"""Generate the project logo.

The mark is the G2 itself with one card in view: both lenses drawn at equal
weight, the right one lit and carrying a single bright line. That is the
product in one shape — the agent is quiet, and then exactly one thing appears
where you are already looking.

Earlier drafts were a bare panel with three stacked bars, which at avatar size
read as a hamburger menu, and a lone "V", which said nothing about the device.
Temples were tried and dropped: at small sizes they read as stray marks
rather than as part of the frame.

  python3 demo/logo.py        # needs Pillow: pip3 install --user Pillow
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))

S = 1024                      # DoraHacks downscales, so start big
BG = (11, 11, 13)
GREEN = (59, 255, 110)
LIT = (21, 54, 34)            # the right lens, powered

LENS_W, LENS_H, RADIUS = 384, 292, 74
GAP, STROKE = 52, 42
PILL_W, PILL_H = 196, 48


def build():
    img = Image.new("RGB", (S, S), BG)
    d = ImageDraw.Draw(img)

    total = LENS_W * 2 + GAP
    lx, cy = (S - total) // 2, S // 2
    top, bot = cy - LENS_H // 2, cy + LENS_H // 2

    # bridge, sitting high the way eyewear does rather than on the centreline
    by = cy - 70
    d.rounded_rectangle([lx + LENS_W - 10, by - 17,
                         lx + LENS_W + GAP + 10, by + 17],
                        radius=17, fill=GREEN)

    for i in range(2):
        x = lx + i * (LENS_W + GAP)
        box = [x, top, x + LENS_W, bot]
        if i == 1:
            d.rounded_rectangle(box, radius=RADIUS, fill=LIT)
        d.rounded_rectangle(box, radius=RADIUS, outline=GREEN, width=STROKE)

    cx = lx + LENS_W + GAP + LENS_W // 2
    d.rounded_rectangle([cx - PILL_W // 2, cy - PILL_H // 2,
                         cx + PILL_W // 2, cy + PILL_H // 2],
                        radius=PILL_H // 2, fill=GREEN)
    return img


def main():
    img = build()
    img.save(os.path.join(HERE, "logo.png"))
    img.resize((256, 256), Image.LANCZOS).save(os.path.join(HERE, "logo-256.png"))
    print("demo/logo.png, demo/logo-256.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
