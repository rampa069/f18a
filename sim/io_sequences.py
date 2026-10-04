"""Port access sequences for the V9938 CPU interface, run on openMSX and on
v9938_model (see test_model_openmsx.py)."""

import random


def out(port, v):
    return ("out", port, v)


def inp(port):
    return ("in", port)


def reg(r, v):
    return [out(0x99, v), out(0x99, 0x80 | r)]


def waddr(a):
    return [out(0x99, a & 0xFF), out(0x99, 0x40 | ((a >> 8) & 0x3F))]


def raddr(a):
    return [out(0x99, a & 0xFF), out(0x99, (a >> 8) & 0x3F)]


def seq_basic():
    """VRAM writes / reads, read ahead, R#14 and its carry in G4, G7 planar
    addresses, palette, indirect registers, status registers."""
    rng = random.Random(42)
    s = []
    # G1: plain 16K writes and reads with read ahead.
    s += waddr(0x1234) + [out(0x98, rng.getrandbits(8)) for _ in range(20)]
    s += raddr(0x1234) + [inp(0x98) for _ in range(10)]
    s += [out(0x98, 0x55), inp(0x98)]                    # write after read
    # R#14 selects the upper VRAM, wrap at the 16K boundary in G1 (no carry).
    s += reg(14, 5) + waddr(0x3FFE) + [out(0x98, v) for v in (1, 2, 3, 4)]
    # G4: the 16K boundary carries into R#14.
    s += reg(0, 0x06) + reg(14, 2) + waddr(0x3FFE) + [out(0x98, v) for v in (5, 6, 7, 8)]
    # G7: planar addresses.
    s += reg(0, 0x0E) + reg(14, 1) + waddr(0x0100) + [out(0x98, 0xA0 + i) for i in range(8)]
    s += raddr(0x0100) + [inp(0x98) for _ in range(8)]
    s += reg(0, 0x00)
    # Palette through R#16 and port 9Ah, with wrap from 15 to 0.
    s += reg(16, 14) + [out(0x9A, v) for v in (0x71, 0x02, 0x34, 0x05, 0x16, 0x07)]
    # Indirect register access: R#17 with and without auto increment.
    s += reg(17, 0x02) + [out(0x9B, v) for v in (0x06, 0x80, 0x01)]   # R2, R3, R4
    s += reg(17, 0x87) + [out(0x9B, v) for v in (0x4C, 0x5D)]         # R7 twice
    # Status registers: S#1 (ID), S#4-S#9 fixed bits, back to S#0.
    for n in (1, 4, 5, 6, 7, 8, 9):
        s += reg(15, n) + [inp(0x99)]
    s += reg(15, 0) + [inp(0x99)]
    # A status read resets the control port latch.
    s += [out(0x99, 0x34), inp(0x99)] + raddr(0x1234) + [inp(0x98)]
    return s


SEQUENCES = {"basic": seq_basic}
