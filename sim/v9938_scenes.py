"""Deterministic V9938 scenes: (vram, regs, palette) for every display mode.

Used to check v9938_model against openMSX and, later, the RTL against the
model.  VRAM is random (seeded) so every table bit is exercised; the
palette has 16 distinct colors so screenshots decode unambiguously.
"""

import random

import v9938_model as vm


def unique_palette(seed):
    rng = random.Random(seed)
    colors = rng.sample(range(512), 16)
    return [((c >> 6) & 7, (c >> 3) & 7, c & 7) for c in colors]


def rand_vram(seed):
    rng = random.Random(seed)
    return bytearray(rng.getrandbits(8) for _ in range(vm.VRAM_SIZE))


def sprite_table(vram, sat_attr, sat_color, entries, logical=lambda a: a):
    """Write sprite mode 2 attributes (y, x, name) and per line colors."""
    for i, (y, x, name, colors) in enumerate(entries):
        for k, v in enumerate((y, x, name, 0)):
            vram[logical(sat_attr + i * 4 + k)] = v
        for line in range(16):
            vram[logical(sat_color + i * 16 + line)] = colors[line % len(colors)]


# Sprite mode 2 layout: CC / IC / EC, overlaps, nine on a line, terminator.
SPRITES2 = [
    (20, 40, 0, [0x0F]),
    (24, 48, 4, [0x46]),             # CC: ORs into sprite 0
    (60, 100, 8, [0x2A, 0x23]),      # IC, color changes per line
    (60, 10, 12, [0x85]),            # EC, partly off the left edge
] + [(120, 20 * k, 16 + k * 4, [k + 1]) for k in range(9)] + [
    (216, 0, 0, [0]),                # terminator
    (30, 30, 0, [0x0F]),             # never shown
]


def scene(mode, seed, lines212=False, scroll=0, tp=False, mag=False):
    vram = rand_vram(seed)
    pal = unique_palette(seed)
    r8 = 0x08 | (0x20 if tp else 0)          # VR = 64K chips, TP
    r9 = 0x80 if lines212 else 0
    r1 = 0x40 | 0x02 | (0x01 if mag else 0)   # BL, 16x16 sprites
    if mode == "G1":
        regs = [0x00, r1, 0x06, 0x80, 0x00, 0x36, 0x07, 0x04, r8, r9]
    elif mode == "G2":
        regs = [0x02, r1, 0x0E, 0xFF, 0x03, 0x76, 0x03, 0x05, r8, r9]
    elif mode == "MC":
        regs = [0x00, r1 | 0x08, 0x02, 0x00, 0x00, 0x20, 0x03, 0x0E, r8, r9]
    elif mode == "T1":
        regs = [0x00, 0x50, 0x02, 0x00, 0x00, 0x00, 0x00, 0xF4, r8, r9]
    elif mode == "T2":
        regs = [0x04, 0x50, 0x03, 0x00, 0x00, 0x00, 0x00, 0xF4, r8, r9]
    elif mode == "G3":
        # SAT at 0x7600 (colors 0x7400), SPG 0x7800, CT 0x2000, PG 0x0000, NT 0x1800.
        regs = [0x04, r1, 0x06, 0xFF, 0x03, 0xEF, 0x0F, 0x05, r8, r9, 0, 0]
        sprite_table(vram, 0x7600, 0x7400, SPRITES2)
    elif mode == "G4":
        regs = [0x06, r1, 0x1F, 0, 0, 0xEF, 0x0F, 0x04, r8, r9, 0, 0]
        sprite_table(vram, 0x7600, 0x7400, SPRITES2)
    elif mode == "G5":
        regs = [0x08, r1, 0x1F, 0, 0, 0xEF, 0x0F, 0x04, r8, r9, 0, 0]
        sprite_table(vram, 0x7600, 0x7400, SPRITES2)
    elif mode == "G6":
        regs = [0x0A, r1, 0x1F, 0, 0, 0xF7, 0x1E, 0x04, r8, r9, 0, 1]
        sprite_table(vram, 0xFA00, 0xF800, SPRITES2, vm.planar)
    elif mode == "G7":
        regs = [0x0E, r1, 0x1F, 0, 0, 0xF7, 0x1E, 0x04, r8, r9, 0, 1]
        sprite_table(vram, 0xFA00, 0xF800, SPRITES2, vm.planar)
    else:
        raise ValueError(mode)
    regs = regs + [0] * (24 - len(regs))
    regs[23] = scroll
    return vram, regs, pal


def page1(sc):
    """Show bitmap page 1 (R2 bit 5): the page 0 picture copied there."""
    vram, regs, pal = sc
    vram[0x8000:0x8000 + 0x6A00] = bytes(reversed(vram[0x0000:0x6A00]))
    regs[2] = 0x3F
    return vram, regs, pal


def t2_blink(sc, r12, r13):
    """T2 with the blink color table at 0x0A00 and R#12 / R#13 (R#13 with
    an off time of 0 keeps the blink state fixed)."""
    vram, regs, pal = sc
    regs[3], regs[10], regs[12], regs[13] = 0x2F, 0, r12, r13
    return vram, regs, pal


def bitmap_page_flip(sc, r9_or=0, r13=0):
    """Page 1 (R#2) with page 0 shown by the R#9 even / odd alternation (even
    field) or the R#13 blink state."""
    vram, regs, pal = page1(sc)
    regs[9] |= r9_or
    regs[13] = r13
    return vram, regs, pal


def default_palette(sc):
    """Leave the palette as it is after a reset (the V9938 one)."""
    vram, regs, _ = sc
    return vram, regs, None


# Scenes the core shows in V9938 mode (every mode but T2, f18a-5pv.1.6).
DISPLAY_SCENES = {
    "g1": lambda: scene("G1", 1),
    "g1_212_scroll": lambda: scene("G1", 21, lines212=True, scroll=37),
    "g2_212": lambda: scene("G2", 22, lines212=True),
    "g2_scroll": lambda: scene("G2", 23, scroll=200),
    "mc_212_scroll": lambda: scene("MC", 24, lines212=True, scroll=9),
    "t1": lambda: scene("T1", 4),
    "t2_212": lambda: scene("T2", 5, lines212=True),
    "t1_scroll": lambda: scene("T1", 34, scroll=13),
    "t2_212_scroll": lambda: scene("T2", 35, lines212=True, scroll=203),
    "t2_blink_on": lambda: t2_blink(scene("T2", 31), 0x4E, 0xF0),
    "t2_blink_fg0": lambda: t2_blink(scene("T2", 32, lines212=True), 0x0B, 0x30),
    "t2_blink_off": lambda: t2_blink(scene("T2", 33), 0x4E, 0x0F),
    "g3": lambda: scene("G3", 6),
    "g3_212_scroll": lambda: scene("G3", 7, lines212=True, scroll=37),
    "g3_mag": lambda: scene("G3", 25, mag=True),
    "g4_212": lambda: scene("G4", 8, lines212=True),
    "g4_scroll": lambda: scene("G4", 26, scroll=77),
    "g4_mag_tp": lambda: scene("G4", 9, tp=True, mag=True),
    "g4_page1": lambda: page1(scene("G4", 27, lines212=True)),
    "g4_blink_page": lambda: bitmap_page_flip(scene("G4", 36), r13=0xF0),
    "g5_212": lambda: scene("G5", 10, lines212=True),
    "g6_212": lambda: scene("G6", 11, lines212=True),
    "g6_scroll": lambda: scene("G6", 28, scroll=150),
    "g7_212": lambda: scene("G7", 12, lines212=True),
    "g7_scroll": lambda: scene("G7", 13, scroll=100),
    "g1_default_pal": lambda: default_palette(scene("G1", 29)),
    "g4_default_pal": lambda: default_palette(scene("G4", 30, tp=True)),
}

SCENES = {
    "g1": lambda: scene("G1", 1),
    "g2": lambda: scene("G2", 2),
    "mc": lambda: scene("MC", 3),
    "t1": lambda: scene("T1", 4),
    "t2_212": lambda: scene("T2", 5, lines212=True),
    "t1_scroll": lambda: scene("T1", 34, scroll=13),
    "t2_212_scroll": lambda: scene("T2", 35, lines212=True, scroll=203),
    "t2_blink_on": lambda: t2_blink(scene("T2", 31), 0x4E, 0xF0),
    "t2_blink_fg0": lambda: t2_blink(scene("T2", 32, lines212=True), 0x0B, 0x30),
    "t2_blink_off": lambda: t2_blink(scene("T2", 33), 0x4E, 0x0F),
    "g3": lambda: scene("G3", 6),
    "g3_212_scroll": lambda: scene("G3", 7, lines212=True, scroll=37),
    "g4_212": lambda: scene("G4", 8, lines212=True),
    "g4_mag_tp": lambda: scene("G4", 9, tp=True, mag=True),
    "g4_page1": lambda: page1(scene("G4", 27, lines212=True)),
    "g4_blink_page": lambda: bitmap_page_flip(scene("G4", 36), r13=0xF0),
    "g5_212": lambda: scene("G5", 10, lines212=True),
    "g6_212": lambda: scene("G6", 11, lines212=True),
    "g6_scroll": lambda: scene("G6", 28, scroll=150),
    "g7_212": lambda: scene("G7", 12, lines212=True),
    "g7_scroll": lambda: scene("G7", 13, scroll=100),
}
