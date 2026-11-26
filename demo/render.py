"""Turn a demo/recording capture into the submission video.

The captured PNGs are pure green with the glyphs carried in the alpha
channel, so compositing them over black at 2x reproduces exactly what the
wearer sees - no recolouring, no redrawing. Captions, a title and an end card
are composited in Pillow and ffmpeg only encodes, which keeps this working on
an ffmpeg built without drawtext.

  python3 demo/render.py [--out demo/vantage-demo.mp4]

Needs ffmpeg on PATH and Pillow (pip3 install --user Pillow). Run it with
plain `python3`, not `python3 -I` - isolated mode drops the user site
directory that `pip3 install --user` puts Pillow in.
"""
import json
import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
REC = os.path.join(HERE, "recording")
WORK = os.path.join(REC, "work")
COMP = os.path.join(WORK, "comp")

W, H = 1280, 720
GW, GH = 1152, 576          # the 576x288 framebuffer at 2x
GX, GY = 64, 40
BG = (11, 11, 13)
GREEN = (59, 255, 110)
GREY = (180, 185, 194)
DIM = (120, 126, 136)
CAP_Y = 648
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_B = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

TITLE_SECS, OUTRO_SECS = 6.0, 12.0
TITLE = [
    ("Vantage", FONT_B, 68, GREEN, 236),
    ("An agent that decides what is worth interrupting you for",
     FONT, 30, GREY, 330),
    ("Even Realities G2   ·   Track 02, Crypto & DeFi", FONT, 24, DIM, 398),
    ("Hyperliquid HIP-4 outcome markets", FONT, 24, DIM, 436),
]
OUTRO = [
    ("Measured, not claimed", FONT_B, 46, GREEN, 150),
    ("Learning lifts relevance 53% → 57% over 1,100 real candidate events,",
     FONT, 26, GREY, 252),
    ("against a frozen-priors control, at the same interruption cost.",
     FONT, 26, GREY, 292),
    ("The trading premise was tested and rejected:", FONT, 26, GREY, 364),
    ("break-even at the mid across 13,588 graded rows.", FONT, 26, GREY, 404),
    ("That is why this routes attention instead of claiming alpha.",
     FONT, 26, DIM, 466),
    ("github.com/cyber-ND/vantage-g2", FONT_B, 30, GREEN, 556),
]

_fonts = {}


def font(path, size):
    key = (path, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(path, size)
    return _fonts[key]


def centre(draw, text, f, y, fill):
    w = draw.textbbox((0, 0), text, font=f)[2]
    draw.text(((W - w) / 2, y), text, font=f, fill=fill)


def wrap(draw, text, f, width):
    """Two short lines read better under the display than one long one."""
    words, lines, cur = text.split(), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if draw.textbbox((0, 0), trial, font=f)[2] <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def card(lines, seconds, name):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    for text, path, size, colour, y in lines:
        centre(d, text, font(path, size), y, colour)
    p = os.path.join(WORK, name)
    img.save(p)
    return p, seconds


def compose():
    times = json.load(open(os.path.join(REC, "times.json")))
    caps = json.load(open(os.path.join(REC, "captions.json")))
    if not times:
        raise SystemExit("no frames captured - run demo/capture.py first")

    os.makedirs(COMP, exist_ok=True)
    for f in os.listdir(COMP):
        os.remove(os.path.join(COMP, f))

    cap_font = font(FONT, 27)
    out = []
    for i, frame in enumerate(times):
        t = frame["t"]
        glass = Image.open(os.path.join(REC, "frames", frame["file"]))
        glass = glass.convert("RGBA").resize((GW, GH), Image.NEAREST)

        img = Image.new("RGB", (W, H), BG)
        img.paste(glass, (GX, GY), glass)   # alpha carries the glyphs
        d = ImageDraw.Draw(img)

        text = ""
        for start, c in caps:
            if t >= start:
                text = c
        if text:
            lines = wrap(d, text, cap_font, W - 120)[:2]
            y = CAP_Y - (len(lines) - 1) * 17
            for line in lines:
                centre(d, line, cap_font, y, GREY)
                y += 34

        name = f"{i:05d}.png"
        img.save(os.path.join(COMP, name))
        nxt = times[i + 1]["t"] if i + 1 < len(times) else t + 0.2
        out.append((os.path.join(COMP, name), max(nxt - t, 0.02)))
    return out, times[-1]["t"]


def main():
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not on PATH (brew install ffmpeg)")
    os.makedirs(WORK, exist_ok=True)
    args = sys.argv[1:]
    out = (args[args.index("--out") + 1] if "--out" in args
           else os.path.join(HERE, "vantage-demo.mp4"))

    body, total = compose()
    parts = [card(TITLE, TITLE_SECS, "title.png")] + body \
        + [card(OUTRO, OUTRO_SECS, "outro.png")]

    listfile = os.path.join(WORK, "parts.txt")
    with open(listfile, "w") as fh:
        for path, secs in parts:
            fh.write(f"file '{path}'\nduration {secs:.3f}\n")
        fh.write(f"file '{parts[-1][0]}'\n")   # concat needs the last repeated

    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
         "-r", "30", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "20",
         "-movflags", "+faststart", out],
        capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-2500:])
        return 1

    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", out],
        capture_output=True, text=True).stdout.strip()
    size = os.path.getsize(out) / 1e6
    print(f"{out}\n  {float(dur):.0f}s total  ({total:.0f}s of glasses "
          f"capture)  {size:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
