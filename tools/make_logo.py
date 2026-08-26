"""Generate the SongSeek logo set.

    pip install "fonttools[woff]" pillow
    python tools/make_logo.py

Concept: "seek" is the media-player term. The mark is a waveform whose bars are
bright to the left of the playhead and dim to the right, so it reads as a
waveform and a playback position at once. It is built in Space Grotesk - the
typeface the app already bundles and uses for display text - and in the app's
own --color-primary, so the logo and the UI share a palette.

Deliberate choices worth keeping if you edit this:

  * The playhead has no knob. A vertical line topped by a ball reads as a pin or
    a stylus, which is the one association this mark must not have: the project
    this forked from is called DroppedNeedle.
  * 16px and 32px use progressively simpler marks. Seven bars plus a tile is an
    unreadable smudge at favicon size.
  * App icons invert the scheme - dark bars on a pale-blue tile - because a dark
    tile with pale bars has too little contrast to read at 16px, and the pale
    tile stands out against both light and dark browser chrome.
  * Everything is drawn at SS times the final size and downsampled with LANCZOS,
    because Pillow does not anti-alias shape drawing.
"""

import pathlib

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
from PIL import Image, ImageDraw, ImageFont

REPO = pathlib.Path(__file__).resolve().parent.parent
BUILD = REPO / "tools" / ".logo-build"
SS = 4  # supersample factor

# --- palette, taken from the app's own tokens (frontend/src/app.css) ---------
PRIMARY = (174, 213, 242, 255)    # --color-primary  #aed5f2
DIM = (74, 107, 128, 255)         # unplayed bars: primary dragged toward the ground
INK_LIGHT = (234, 242, 248, 255)  # wordmark on dark
INK_DARK = (12, 30, 44, 255)      # wordmark on light
MID_BLUE = (52, 118, 168, 255)    # "Seek" on light backgrounds
MID_DIM = (158, 183, 200, 255)
WHITE = (255, 255, 255, 255)

ICON_TILE = PRIMARY
ICON_BAR = (16, 42, 62, 255)
ICON_BAR_DIM = (16, 42, 62, 120)

# waveform envelope: quiet at the edges, loud in the middle
BARS = [0.30, 0.58, 0.92, 0.74, 0.96, 0.52, 0.34]
PLAYED = 3
SMALL_BARS = [0.44, 0.95, 0.72, 0.40]   # <= 32px
SMALL_PLAYED = 2
TINY_BARS = [0.55, 1.00, 0.68]          # <= 16px
TINY_PLAYED = 2


def bold_font(size: int) -> ImageFont.FreeTypeFont:
    """Space Grotesk at weight 700. It ships variable (300-700, default 300),
    so it has to be instanced before Pillow can use a static weight."""
    BUILD.mkdir(parents=True, exist_ok=True)
    static = BUILD / "spacegrotesk-700.ttf"
    if not static.exists():
        woff2 = REPO / "frontend" / "static" / "fonts" / "spacegrotesk-latin.woff2"
        font = TTFont(woff2)  # fontTools reads woff2 directly when brotli is installed
        instancer.instantiateVariableFont(font, {"wght": 700}, inplace=True)
        font.flavor = None
        font.save(static)
    return ImageFont.truetype(str(static), size)


def draw_mark(draw, x, y, w, h, played_col, unplayed_col, playhead_col,
              bars=None, played=None) -> None:
    bars = bars if bars is not None else BARS
    played = played if played is not None else PLAYED
    n = len(bars)
    gap = w * 0.048
    # The playhead gets its own wider gap rather than crowding the tallest bar.
    ph_gap = gap * 2.6
    bar_w = (w - gap * (n - 2) - ph_gap) / n
    cy = y + h / 2

    xs, cursor = [], x
    for i in range(n):
        xs.append(cursor)
        cursor += bar_w + (ph_gap if i == played - 1 else gap)

    for i, frac in enumerate(bars):
        bh = h * frac
        draw.rounded_rectangle(
            [xs[i], cy - bh / 2, xs[i] + bar_w, cy + bh / 2],
            radius=bar_w / 2,
            fill=played_col if i < played else unplayed_col,
        )

    if playhead_col is None:
        return
    ph_w = max(2, bar_w * 0.22)
    ph_cx = xs[played - 1] + bar_w + ph_gap / 2
    tallest = max(bars) * h
    draw.rounded_rectangle(
        [ph_cx - ph_w / 2, cy - tallest / 2 - h * 0.06,
         ph_cx + ph_w / 2, cy + tallest / 2 + h * 0.06],
        radius=ph_w / 2, fill=playhead_col,
    )


def render(width, height, *, wordmark, song_col, seek_col, played_col,
           unplayed_col, playhead_col, tile=None, pad_frac=0.0,
           simple=False, tiny=False) -> Image.Image:
    W, H = width * SS, height * SS
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if tile is not None:
        d.rounded_rectangle([0, 0, W, H], radius=W * 0.22, fill=tile)

    inset = H * pad_frac
    mark_h = (H - inset * 2) * 0.72

    if wordmark:
        font = bold_font(int(H * 0.42))
        song_w = d.textlength("Song", font=font)
        seek_w = d.textlength("Seek", font=font)
        mark_w = mark_h * 1.42
        gap = H * 0.16
        x = (W - (mark_w + gap + song_w + seek_w)) / 2
        draw_mark(d, x, (H - mark_h) / 2, mark_w, mark_h,
                  played_col, unplayed_col, playhead_col)
        d.text((x + mark_w + gap, H / 2), "Song", font=font, fill=song_col, anchor="lm")
        d.text((x + mark_w + gap + song_w, H / 2), "Seek", font=font, fill=seek_col, anchor="lm")
    else:
        mark_w = (W - inset * 2) * (0.94 if simple else 0.86)
        mark_h = min(mark_h, (H - inset * 2) * (0.80 if simple else 0.62))
        draw_mark(d, (W - mark_w) / 2, (H - mark_h) / 2, mark_w, mark_h,
                  played_col, unplayed_col, None if simple else playhead_col,
                  bars=(TINY_BARS if tiny else SMALL_BARS) if simple else None,
                  played=(TINY_PLAYED if tiny else SMALL_PLAYED) if simple else None)

    return img.resize((width, height), Image.LANCZOS)


def save(img: Image.Image, *paths: str) -> None:
    for rel in paths:
        dest = REPO / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest)
        print(f"  {rel:44} {img.size[0]}x{img.size[1]}")


def main() -> None:
    print("wide wordmark 1274x334")
    save(render(1274, 334, wordmark=True, song_col=INK_LIGHT, seek_col=PRIMARY,
                played_col=PRIMARY, unplayed_col=DIM, playhead_col=INK_LIGHT),
         "frontend/static/logo_wide.png", "frontend/static/logo.png",
         "Images/logo_wide.png")

    save(render(1274, 334, wordmark=True, song_col=INK_DARK, seek_col=MID_BLUE,
                played_col=MID_BLUE, unplayed_col=MID_DIM, playhead_col=INK_DARK),
         "Images/logo_wide_dark.png")

    save(render(1274, 334, wordmark=True, song_col=WHITE, seek_col=WHITE,
                played_col=WHITE, unplayed_col=(255, 255, 255, 110), playhead_col=WHITE),
         "frontend/static/logo_wide_white.png")

    print("mark only 641x334")
    save(render(641, 334, wordmark=False, song_col=None, seek_col=None,
                played_col=PRIMARY, unplayed_col=DIM, playhead_col=INK_LIGHT),
         "frontend/static/logo_icon.png", "Images/logo_icon.png")

    print("app icons")
    for size, names in [
        (512, ["frontend/static/android-chrome-512x512.png"]),
        (192, ["frontend/static/android-chrome-192x192.png",
               "frontend/static/apple-touch-icon.png"]),
        (32, ["frontend/static/favicon-32x32.png"]),
        (16, ["frontend/static/favicon-16x16.png"]),
    ]:
        small = size <= 32
        save(render(size, size, wordmark=False, song_col=None, seek_col=None,
                    played_col=ICON_BAR, unplayed_col=ICON_BAR_DIM, playhead_col=ICON_BAR,
                    tile=ICON_TILE, pad_frac=0.04 if small else 0.16,
                    simple=small, tiny=size <= 16),
             *names)

    print("favicon.ico")
    ico = render(256, 256, wordmark=False, song_col=None, seek_col=None,
                 played_col=ICON_BAR, unplayed_col=ICON_BAR_DIM, playhead_col=ICON_BAR,
                 tile=ICON_TILE, pad_frac=0.16)
    ico.save(REPO / "frontend/static/favicon.ico",
             sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("  frontend/static/favicon.ico                  multi-size")


if __name__ == "__main__":
    main()
