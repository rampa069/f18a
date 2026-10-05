"""F18A test card: the power-on screen (G1 VRAM image for f18a_single_port_ram.vhd)
and the same card for every VDP mode (sim/tests/test_testcard.py).

The card is drawn once at 256 x 192 in TMS9918A color codes:
gray background with a white 16 x 16 grid, a centered circle, the 15
colors as bars, the title and the credits.  The font is the one of the
original F18A power-on screen.

    python3 tools/testcard.py --vhdl   # print the INIT16K constant
"""

import math
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
VERSION = "3.0"
W, H = 256, 192
GRAY, WHITE, BLACK = 14, 15, 1


def original_font():
    """8 x 8 ASCII font from the original F18A power-on VRAM (PG at 0800h)."""
    s = (ROOT / "f18a_single_port_ram.vhd").read_text()
    body = s[s.index("constant INIT16K"):]
    body = body[:body.index(");")]
    vals = [int(v, 16) for v in re.findall(r'x"([0-9A-Fa-f]{2})"', body)]
    return {c: vals[0x800 + c * 8: 0x800 + c * 8 + 8] for c in range(32, 128)}


FONT_FILE = ROOT / "tools" / "testcard_font.bin"


def font():
    if FONT_FILE.exists():
        data = FONT_FILE.read_bytes()
        return {32 + i: list(data[i * 8: i * 8 + 8]) for i in range(96)}
    f = original_font()
    FONT_FILE.write_bytes(bytes(v for c in range(32, 128) for v in f[c]))
    return f


def text(img, x, y, s, fg, bg=None, scale=1):
    f = font()
    for i, ch in enumerate(s):
        rows = f.get(ord(ch), f[32])
        for r in range(8):
            for b in range(8):
                on = rows[r] & (0x80 >> b)
                if on or bg is not None:
                    c = fg if on else bg
                    xs, ys = x + (i * 8 + b) * scale, y + r * scale
                    img[ys: ys + scale, xs: xs + scale] = c


def card():
    """The test card, 256 x 192 color codes."""
    img = np.full((H, W), GRAY, dtype=np.uint8)
    # 16 x 16 grid, lines on the first pixel of each square, frame on the edges.
    img[::16, :] = WHITE
    img[:, ::16] = WHITE
    img[H - 1, :] = WHITE
    img[:, W - 1] = WHITE
    # Circle.
    cx, cy, r = 128, 96, 88
    for a in range(2000):
        t = 2 * math.pi * a / 2000
        x, y = int(round(cx + r * math.cos(t))), int(round(cy + r * math.sin(t)))
        if 0 <= x < W and 0 <= y < H:
            img[y, x] = WHITE
    # Title band (black) with the version.
    img[32:64, 48:208] = BLACK
    text(img, 64, 40, f"F18A-{VERSION}", WHITE, scale=2)
    # Color bars: the 15 colors, 16 x 32.
    for k, c in enumerate(range(1, 16)):
        x = 8 + 16 * k
        img[80:112, x: x + 16] = c
    # Chip line and credits.
    img[128:160, 32:224] = BLACK
    text(img, 72, 132, "TMS9918A V9938", WHITE)
    text(img, 40, 148, "(C)2012-2018 M.HAGERTY", WHITE)
    text(img, 88, 176, "TEST CARD", WHITE, BLACK)
    return img


# -- G1 (power-on screen) ----------------------------------------------------

def to_g1(img, nt=0x0000, ct=0x0400, pg=0x0800, sat=0x0500):
    """G1 VRAM (16 KB) for the image: 768 cells, each a pattern with two
    colors; the 256 patterns come in 32 groups of 8 with one color pair."""
    cells = {}
    for ty in range(24):
        for tx in range(32):
            blk = img[ty * 8: ty * 8 + 8, tx * 8: tx * 8 + 8]
            colors = sorted(set(blk.flatten().tolist()))
            if len(colors) > 2:
                raise ValueError(f"cell {tx},{ty} has colors {colors}")
            if len(colors) == 1:
                fg = bg = colors[0]
            else:
                bg, fg = (colors[0], colors[1]) if colors[0] != WHITE else (colors[1], colors[0])
                if fg != WHITE and bg == GRAY:
                    fg, bg = bg, fg
            pat = tuple(int(sum((1 << (7 - b)) for b in range(8) if blk[r, b] == fg and fg != bg))
                        for r in range(8))
            if fg == bg:
                pat = (0xFF,) * 8
            cells[(tx, ty)] = ((fg, bg), pat)
    # Group the patterns by color pair.
    by_pair = {}
    for pair, pat in cells.values():
        by_pair.setdefault(pair, [])
        if pat not in by_pair[pair]:
            by_pair[pair].append(pat)
    groups = sum((len(p) + 7) // 8 for p in by_pair.values())
    if groups > 32:
        raise ValueError(f"{groups} pattern groups needed: {[(k, len(v)) for k, v in by_pair.items()]}")
    vram = bytearray(0x4000)
    index = {}
    g = 0
    for pair, pats in sorted(by_pair.items()):
        for i, pat in enumerate(pats):
            if i % 8 == 0:
                vram[ct + g] = (pair[0] << 4) | pair[1]
                g += 1
            n = (g - 1) * 8 + i % 8
            index[(pair, pat)] = n
            vram[pg + n * 8: pg + n * 8 + 8] = bytes(pat)
    for (tx, ty), (pair, pat) in cells.items():
        vram[nt + ty * 32 + tx] = index[(pair, pat)]
    vram[sat] = 0xD0                         # no sprites
    return vram, {k: len(v) for k, v in by_pair.items()}, groups


def vhdl_init(vram):
    lines = []
    for i in range(0, len(vram), 32):
        lines.append(",".join(f'x"{v:02X}"' for v in vram[i: i + 32]))
    return ",\n".join(lines)


if __name__ == "__main__":
    img = card()
    vram, pairs, groups = to_g1(img)
    print(f"G1: {groups} pattern groups, {pairs}", file=sys.stderr)
    if "--vhdl" in sys.argv:
        print(vhdl_init(vram))


# -- The card in every mode (sim/tests/test_testcard.py) ----------------------

def to_g2(img, pg=0x0000, ct=0x2000, nt=0x3800):
    """G2 / G3: pattern and colors for every 8 x 1 line, names 0-255 in each
    third."""
    vram = bytearray(0x10000)
    for ty in range(24):
        for tx in range(32):
            name = (ty % 8) * 32 + tx
            vram[nt + ty * 32 + tx] = name
            base = (ty // 8) * 0x800 + name * 8
            for r in range(8):
                row = img[ty * 8 + r, tx * 8: tx * 8 + 8].tolist()
                colors = sorted(set(row))
                assert len(colors) <= 2, (tx, ty, r, colors)
                fg = WHITE if WHITE in colors else colors[-1]
                bg = [c for c in colors if c != fg][0] if len(colors) == 2 else fg
                vram[pg + base + r] = sum(0x80 >> b for b in range(8) if row[b] == fg and fg != bg)
                vram[ct + base + r] = (fg << 4) | bg
    return vram


def to_mc(img, pg=0x0000, nt=0x0800):
    """Multicolor: 64 x 48 blocks of 4 x 4, the most frequent color that is
    not the gray background (so the grid and the circle show)."""
    vram = bytearray(0x4000)
    for ty in range(24):
        for tx in range(32):
            vram[nt + ty * 32 + tx] = (ty // 4) * 32 + tx
    for by in range(48):
        for bx in range(64):
            blk = img[by * 4: by * 4 + 4, bx * 4: bx * 4 + 4].flatten().tolist()
            cnt = {c: blk.count(c) for c in set(blk)}
            others = {c: n for c, n in cnt.items() if c != GRAY}
            c = max(others, key=others.get) if others and sum(others.values()) >= 4 else GRAY
            ty, yb = by // 2, by % 2
            name = (ty // 4) * 32 + bx // 2
            a = pg + name * 8 + (ty % 4) * 2 + yb
            if bx % 2 == 0:
                vram[a] = (vram[a] & 0x0F) | (c << 4)
            else:
                vram[a] = (vram[a] & 0xF0) | c
    return vram


TEXT_LINES = [
    "F18A-{v}",
    "",
    "TMS9918A  V9938",
    "",
    "(C)2012-2018 M.HAGERTY",
    "",
    "TEST CARD - {mode}",
    "",
    "0123456789 ABCDEFGHIJKLMNOPQRSTUVWXYZ",
]


def narrow(rows):
    """6 pixel version of an 8 pixel glyph for the text modes: drop the
    columns that lose the fewest pixels until it fits in the 6 shown."""
    cols = [[(r >> (7 - c)) & 1 for r in rows] for c in range(8)]
    while len(cols) > 6:
        used = [k for k in range(len(cols)) if any(cols[k])]
        if len(used) < len(cols):
            # Drop an empty column at the right first, keeping one at the left.
            k = max(k for k in range(1, len(cols)) if k not in used) if any(
                k not in used for k in range(1, len(cols))) else len(cols) - 1
        else:
            k = min(range(1, len(cols) - 1), key=lambda k: sum(cols[k]))
        cols.pop(k)
    return [sum(cols[c][r] << (7 - c) for c in range(6)) for r in range(8)]


def to_text(cols, mode, pg, nt):
    """Text modes: a framed text card with the font."""
    vram = bytearray(0x4000 if cols == 40 else 0x10000)
    f = font()
    for c in range(32, 128):
        vram[pg + c * 8: pg + c * 8 + 8] = bytes(narrow(f[c]))
    rows = [" " * cols for _ in range(24)]
    rows[0] = "+" + "-" * (cols - 2) + "+"
    rows[23] = rows[0]
    for r in range(1, 23):
        rows[r] = "|" + " " * (cols - 2) + "|"
    for i, line in enumerate(TEXT_LINES):
        s = line.format(v=VERSION, mode=mode)
        x = (cols - len(s)) // 2
        r = 7 + i
        rows[r] = rows[r][:x] + s + rows[r][x + len(s):]
    for r, s in enumerate(rows):
        vram[nt + r * cols: nt + r * cols + cols] = s.encode()
    return vram


def g7_code(rgb3):
    """Nearest G7 color (blue only has the levels 0, 2, 4, 7); grays stay
    gray."""
    r, g, b = rgb3
    bl = min(range(4), key=lambda k: abs((0, 2, 4, 7)[k] - b))
    if r == g == b:
        r = g = (0, 2, 4, 7)[bl]
    return (g << 5) | (r << 2) | bl


V9938_DEFAULT = [
    (0, 0, 0), (0, 0, 0), (1, 6, 1), (3, 7, 3), (1, 1, 7), (2, 3, 7), (5, 1, 1), (2, 6, 7),
    (7, 1, 1), (7, 3, 3), (6, 6, 1), (6, 6, 4), (1, 4, 1), (6, 2, 5), (5, 5, 5), (7, 7, 7)]


def planar(a):
    return ((a << 16) | (a >> 1)) & 0x1FFFF


def to_bitmap(img, mode):
    """G4-G7, 192 lines, page 0."""
    vram = bytearray(0x20000)
    for y in range(H):
        row = img[y].tolist()
        if mode == "G4":
            for x in range(0, W, 2):
                vram[y * 128 + x // 2] = (row[x] << 4) | row[x + 1]
        elif mode == "G5":
            m = {GRAY: 0, WHITE: 1, BLACK: 2}
            px = [m.get(c, 3) for c in row for _ in (0, 1)]
            for x in range(0, 512, 4):
                vram[y * 128 + x // 4] = (px[x] << 6) | (px[x + 1] << 4) | (px[x + 2] << 2) | px[x + 3]
        elif mode == "G6":
            for x in range(W):
                vram[planar(y * 256 + x)] = (row[x] << 4) | row[x]
        else:
            for x in range(W):
                vram[planar(y * 256 + x)] = g7_code(V9938_DEFAULT[row[x]])
    return vram


def mode_scene(mode):
    """(vram, regs, palette or None, chip) of the card in a mode."""
    img = card()
    if mode == "G1":
        vram, _, _ = to_g1(img)
        return vram, [0x00, 0xC0, 0x00, 0x10, 0x01, 0x0A, 0x02, 0x01], None, "tms"
    if mode == "G2":
        return to_g2(img)[:0x4000], [0x02, 0xC0, 0x0E, 0xFF, 0x03, 0x76, 0x03, 0x01], None, "tms"
    if mode == "MC":
        return to_mc(img), [0x00, 0xC8, 0x02, 0x00, 0x00, 0x36, 0x07, 0x01], None, "tms"
    if mode == "T1":
        return to_text(40, "TEXT 1", pg=0x0000, nt=0x0800), [0x00, 0xD0, 0x02, 0x00, 0x00, 0x00, 0x00, 0xF4], None, "tms"
    if mode == "G3":
        vram = to_g2(img)
        regs = [0x04, 0x40, 0x0E, 0xFF, 0x03, 0x7F, 0x07, 0x01, 0x0A, 0x00, 0x00, 0x00]  # R8: VR, SPD
        return vram, regs, None, "v9938"
    if mode == "T2":
        regs = [0x04, 0x50, 0x0B, 0x00, 0x00, 0x00, 0x00, 0xF4, 0x08, 0x00]
        return to_text(80, "TEXT 2 (80 COLUMNS)", pg=0x0000, nt=0x2000), regs, None, "v9938"
    vram = to_bitmap(img, mode)
    r0 = {"G4": 0x06, "G5": 0x08, "G6": 0x0A, "G7": 0x0E}[mode]
    regs = [r0, 0x40, 0x1F, 0x00, 0x00, 0xEF, 0x0F, 0x01, 0x0A, 0x00, 0x00, 0x00]   # R8: VR, SPD
    pal = None
    if mode == "G5":
        pal = list(V9938_DEFAULT)
        pal[0], pal[1], pal[2], pal[3] = (5, 5, 5), (7, 7, 7), (0, 0, 0), (7, 1, 1)
        regs[7] = 0x0A                       # border: black / black
        regs[8] |= 0x20                      # TP: color 0 is the gray, not the border
    if mode == "G7":
        regs[7] = 0x00                       # border: black
    return vram, regs, pal, "v9938"


MODES = ["G1", "G2", "MC", "T1", "G3", "G4", "G5", "G6", "G7", "T2"]
