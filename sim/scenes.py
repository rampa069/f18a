"""Deterministic VRAM images and register sets for the render tests."""

import random

VRAM_SIZE = 16384


def _rand_bytes(rng, n):
    return bytes(rng.getrandbits(8) for _ in range(n))


def _sprites(rng, vram, sat, spg, entries):
    """Place sprite attribute entries (y, x, name, color) and random patterns."""
    vram[spg: spg + 2048] = _rand_bytes(rng, 2048)
    for i, e in enumerate(entries):
        vram[sat + i * 4: sat + i * 4 + 4] = bytes(e)


# Sprite layout shared by the graphics scenes: overlaps, early clock, edges,
# a row with more than four sprites, and the 0xD0 terminator.
SPRITE_ENTRIES = [
    (20, 40, 0, 0x0F),
    (24, 48, 4, 0x06),          # overlaps sprite 0 -> collision
    (100, 10, 8, 0x8B),         # early clock, partly off the left edge
    (100, 250, 12, 0x03),       # partly off the right edge
    (150, 30, 16, 0x0D),        # five sprites on the same lines
    (150, 70, 20, 0x09),
    (150, 110, 24, 0x05),
    (150, 150, 28, 0x0A),
    (150, 190, 32, 0x0C),       # 5th sprite -> not shown
    (0xF8, 128, 36, 0x0E),      # negative y, partly above the top
    (185, 200, 40, 0x00),       # transparent color, still collides
    (0xD0, 0, 0, 0),            # terminator
    (60, 60, 44, 0x0F),         # after the terminator -> never shown
]


def graphics1(seed=1):
    rng = random.Random(seed)
    vram = bytearray(_rand_bytes(rng, VRAM_SIZE))
    # NT 0x1800, CT 0x2000, PG 0x0000, SAT 0x1B00, SPG 0x3800
    regs = [0x00, 0xE2, 0x06, 0x80, 0x00, 0x36, 0x07, 0x04]
    _sprites(rng, vram, 0x1B00, 0x3800, SPRITE_ENTRIES)
    return vram, regs


def graphics2(seed=2, mag=False):
    rng = random.Random(seed)
    vram = bytearray(_rand_bytes(rng, VRAM_SIZE))
    # NT 0x3800, CT 0x2000 (full), PG 0x0000 (full), SAT 0x3B00, SPG 0x1800
    r1 = 0xE2 | (0x01 if mag else 0)
    regs = [0x02, r1, 0x0E, 0xFF, 0x03, 0x76, 0x03, 0x01]
    _sprites(rng, vram, 0x3B00, 0x1800, SPRITE_ENTRIES)
    return vram, regs


def graphics2_masked(seed=3):
    """Graphics II with the table mirroring masks of R3/R4 in use."""
    rng = random.Random(seed)
    vram = bytearray(_rand_bytes(rng, VRAM_SIZE))
    # CT mask 0x9F -> 0x2000 with 2 KB mirrored, PG mask 0x00 -> 0x0000 with 2 KB mirrored.
    regs = [0x02, 0xC0, 0x0E, 0x9F, 0x00, 0x76, 0x03, 0x0C]
    vram[0x3B00] = 0xD0
    return vram, regs


def multicolor(seed=4):
    rng = random.Random(seed)
    vram = bytearray(_rand_bytes(rng, VRAM_SIZE))
    # NT 0x0800, PG 0x0000, SAT 0x1000, SPG 0x1800, 8x8 sprites magnified
    regs = [0x00, 0xC9, 0x02, 0x00, 0x00, 0x20, 0x03, 0x0E]
    nt = 0x0800
    for i in range(768):
        vram[nt + i] = ((i // 128) * 32 + (i % 32)) & 0xFF  # MC name table layout
    _sprites(rng, vram, 0x1000, 0x1800, SPRITE_ENTRIES[:6] + [(0xD0, 0, 0, 0)])
    return vram, regs


def text1(seed=5):
    rng = random.Random(seed)
    vram = bytearray(_rand_bytes(rng, VRAM_SIZE))
    # NT 0x0800, PG 0x0000, white on dark blue
    regs = [0x00, 0xD0, 0x02, 0x00, 0x00, 0x00, 0x00, 0xF4]
    return vram, regs


def blanked(seed=6):
    vram, regs = graphics1(seed)
    regs[1] &= ~0x40
    regs[7] = 0x06
    return vram, regs


def sprite_edges(mag=False, size16=False):
    """Only sprites (transparent tiles) crossing the screen edges."""
    rng = random.Random(7)
    vram = bytearray(VRAM_SIZE)
    # G1: NT 0x1800 (all name 0), CT 0x2000 = 0x00 (transparent), PG 0x0000.
    r1 = 0xC0 | (0x02 if size16 else 0) | (0x01 if mag else 0)
    regs = [0x00, r1, 0x06, 0x80, 0x00, 0x36, 0x07, 0x04]
    entries = []
    # Early clock sprites starting at x = -1 .. -15 (one per row band).
    for i, x in enumerate([31, 30, 29, 27, 24, 20, 17]):
        entries.append((i * 24, x, 0, 0x80 | (8 + i)))
    # A normal sprite exactly at x = 0.
    entries.append((176, 0, 0, 0x0F))
    # Sprites running off the right edge.
    for i, x in enumerate([255, 254, 250, 245]):
        entries.append((i * 24 + 4, x, 0, 2 + i))
    entries.append((0xD0, 0, 0, 0))
    vram[0x3800: 0x3800 + 32] = b"\xFF" * 32       # solid pattern 0
    vram[0x3800 + 32: 0x4000] = bytes(rng.getrandbits(8) for _ in range(0x4000 - 0x3820))
    for i, e in enumerate(entries):
        vram[0x1B00 + i * 4: 0x1B00 + i * 4 + 4] = bytes(e)
    return vram, regs


SCENES = {
    "graphics1": graphics1,
    "graphics2": graphics2,
    "graphics2_mag": lambda: graphics2(mag=True),
    "graphics2_masked": graphics2_masked,
    "multicolor": multicolor,
    "text1": text1,
    "blanked": blanked,
    "sprite_edges": sprite_edges,
    "sprite_edges_mag": lambda: sprite_edges(mag=True),
    "sprite_edges_16": lambda: sprite_edges(size16=True),
    "sprite_edges_16_mag": lambda: sprite_edges(mag=True, size16=True),
}
