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


def seq_vr0():
    """R#8 VR = 0 (16K chips): only A14-A0 count and they reach other cells
    than with VR = 1 (openMSX VDPVRAM::swapAddr)."""
    rng = random.Random(7)
    sw = lambda a: 1 | ((a & 0x7F) << 1) | ((a & 0x7FC0) << 2)
    addrs = [0x0000, 0x0041, 0x0080, 0x1234, 0x3FFF]
    s = reg(8, 0x00)                                     # VR = 0
    for a in addrs:
        s += waddr(a) + [out(0x98, rng.getrandbits(8)) for _ in range(3)]
    s += reg(14, 1) + waddr(0x0123) + [out(0x98, rng.getrandbits(8)) for _ in range(3)]
    s += reg(14, 3) + raddr(0x0123) + [inp(0x98) for _ in range(3)]   # A15 ignored
    s += reg(14, 0)
    for a in addrs:
        s += raddr(a) + [inp(0x98) for _ in range(3)]
    s += reg(8, 0x08)                                    # VR = 1: where did they go?
    for a in addrs + [0x4123]:
        p = sw(a)
        s += reg(14, p >> 14) + raddr(p & 0x3FFF) + [inp(0x98) for _ in range(2)]
    s += reg(14, 0)
    for a in addrs:
        s += raddr(a) + [inp(0x98)]
    # Written with VR = 1, read with VR = 0.
    s += waddr(0x2345) + [out(0x98, rng.getrandbits(8)) for _ in range(4)]
    s += reg(8, 0x00)
    for a in (0x2345, 0x2346, 0x1000, 0x00A2):
        s += raddr(a) + [inp(0x98)]
    s += reg(8, 0x08)
    return s


SEQUENCES = {"basic": seq_basic, "vr0": seq_vr0}


# -- Command engine -------------------------------------------------------
#
# Sequences for the V9938 command engine (test_model_openmsx.test_cmd).  They
# use the extra ops of openmsx_oracle.z80_program: ("wait_ce",),
# ("wait_tr",), ("delay", n), ("block", port, data), ("tr_out", data) and
# ("tr_in", n).

# R#0 for each bitmap mode (R#1 = 40h), pixels per line, bits per pixel,
# bytes per line for the CPU (logical addresses).
CMD_MODES = {
    "G4": (0x06, 256, 4, 128),
    "G5": (0x08, 512, 2, 128),
    "G6": (0x0A, 512, 4, 256),
    "G7": (0x0E, 256, 8, 256),
}

# Logical operations: IMP AND OR EOR NOT, TIMP TAND TOR TEOR TNOT, and an
# undefined code (no write).
LOGOPS = [0, 1, 2, 3, 4, 8, 9, 10, 11, 12, 5]


def set_mode(name):
    """Select a bitmap mode; openMSX applies a mode change at the next line,
    so wait a bit before the next command."""
    return reg(0, CMD_MODES[name][0]) + reg(1, 0x40) + [("delay", 255)]


def pack(name, pixels):
    """Pixel colors to the bytes of a line."""
    bpp = CMD_MODES[name][2]
    per = 8 // bpp
    out_ = []
    for i in range(0, len(pixels), per):
        v = 0
        for p in pixels[i:i + per]:
            v = (v << bpp) | (p & ((1 << bpp) - 1))
        out_.append(v)
    return out_


def random_pixels(name, n, rng):
    """Random colors, a quarter of them 0 (for the T operations)."""
    top = (1 << CMD_MODES[name][2]) - 1
    return [0 if rng.random() < 0.25 else rng.randint(1, top) for _ in range(n)]


def fill_line(name, y, data):
    """Write one line of the bitmap through the data port."""
    bpl = CMD_MODES[name][3]
    a = y * bpl
    return reg(14, a >> 14) + waddr(a & 0x3FFF) + [("block", 0x98, bytes(data))] + reg(14, 0)


def command(cmd, sx=0, sy=0, dx=0, dy=0, nx=0, ny=0, col=0, arg=0, wait=True, first=32, last=46):
    """Write R#first..R#last through port 9Bh (R#17 auto increment); R#46
    starts the command.  Then wait for CE = 0 (when R#46 is written)."""
    values = [sx & 0xFF, sx >> 8, sy & 0xFF, sy >> 8, dx & 0xFF, dx >> 8, dy & 0xFF, dy >> 8,
              nx & 0xFF, nx >> 8, ny & 0xFF, ny >> 8, col, arg, cmd]
    s = reg(17, first) + [("block", 0x9B, bytes(values[first - 32:last - 31]))]
    return s + ([("wait_ce",)] if wait and last == 46 else [])


def status(*regs):
    """Read status registers (S#7 sets the transfer flag, S#9 clears BD)."""
    s = []
    for n in regs:
        s += reg(15, n) + [inp(0x99)]
    return s + reg(15, 0)


STAT = (2, 8, 9, 7)


def _prep(name, rng, lines=(10, 11)):
    s = set_mode(name)
    ppl = CMD_MODES[name][1]
    for y in lines:
        s += fill_line(name, y, pack(name, random_pixels(name, ppl, rng)))
    return s


def seq_cmd_move(name):
    """HMMV, YMMM, HMMM with DIX / DIY, clipping at the right / left edge,
    DX beyond the line, NX = 1 in a byte command, NY = 0 (1024 lines, wraps
    at the bottom), MXD / MXS (no extended VRAM)."""
    rng = random.Random(1000 + list(CMD_MODES).index(name))
    ppl = CMD_MODES[name][1]
    s = _prep(name, rng)
    s += command(0xC0, dx=8, dy=20, nx=24, ny=4, col=0x5A)                   # HMMV
    s += command(0xC3, dx=61, dy=30, nx=16, ny=3, col=0xA5, arg=0x0C)        # DIX DIY, op ignored
    s += command(0xC0, dx=ppl - 6, dy=34, nx=40, ny=2, col=0x3C)             # clipped right
    s += command(0xC0, dx=5, dy=37, nx=40, ny=2, col=0xC3, arg=0x04)         # clipped left
    s += command(0xC0, dx=0, dy=40, nx=1, ny=1, col=0x11)                    # NX = 1
    s += command(0xC0, dx=0x1F0, dy=42, nx=8, ny=2, col=0x22)                # DX >= ppl (G4 / G7)
    s += command(0xC0, dx=ppl - 4, dy=10, nx=4, ny=0, col=0x77)              # NY = 0
    s += command(0xC0, dx=0, dy=46, nx=8, ny=2, col=0xEE, arg=0x20)          # MXD: nothing
    s += status(*STAT)
    s += command(0xE0, sy=10, dx=ppl // 2 + 3, dy=50, ny=2)                  # YMMM
    s += command(0xE0, sy=11, dx=20, dy=53, ny=2, arg=0x0C)                  # YMMM DIX DIY
    s += command(0xE0, sy=10, dx=8, dy=56, ny=1, arg=0x20)                   # YMMM MXD
    s += command(0xD0, sx=4, sy=10, dx=100, dy=58, nx=32, ny=2)              # HMMM
    s += command(0xD0, sx=40, sy=11, dx=81, dy=62, nx=16, ny=2, arg=0x0C)    # DIX DIY
    s += command(0xD0, sx=ppl - 10, sy=10, dx=30, dy=64, nx=64, ny=1)        # SX clipped
    s += command(0xD0, sx=0, sy=10, dx=12, dy=66, nx=0, ny=1)                # NX = 0
    s += command(0xD0, sx=0, sy=10, dx=12, dy=68, nx=8, ny=1, arg=0x10)      # MXS: reads FFh
    s += command(0xD0, sx=8, sy=4, dx=8, dy=0, nx=8, ny=0, arg=0x08)         # NY = 0, DIY: 1 line
    s += status(*STAT)
    return s


def seq_cmd_logic(name):
    """LMMV, LMMM and PSET with every logical operation, on a background
    that is not 0, with color 0 for the T operations; LMMM DIX / DIY and
    clipping."""
    rng = random.Random(2000 + list(CMD_MODES).index(name))
    ppl = CMD_MODES[name][1]
    top = (1 << CMD_MODES[name][2]) - 1
    s = _prep(name, rng)
    s += command(0xC0, dx=0, dy=60, nx=ppl, ny=30, col=0x96 if top != 3 else 0x9C)
    for k, op in enumerate(LOGOPS):
        col = rng.randint(1, top) if k % 3 else 0
        if op & 8:
            col = 0 if k % 2 else rng.randint(1, top)
        s += command(0x80 | op, dx=k * 3 + 1, dy=60 + k, nx=11, ny=1, col=col | 0x00)     # LMMV
        s += command(0x90 | op, sx=k * 5, sy=10, dx=k * 4 + 3, dy=72 + k, nx=13, ny=1)    # LMMM
        s += command(0x50 | op, dx=100 + k, dy=60 + k, col=rng.randint(0, top), first=36)  # PSET
    s += command(0x80, dx=ppl - 3, dy=84, nx=9, ny=2, col=top)               # LMMV clipped
    s += command(0x88, dx=7, dy=87, nx=6, ny=3, col=top & 0x5, arg=0x0C)     # DIX DIY
    s += command(0x90, sx=9, sy=11, dx=60, dy=86, nx=20, ny=2, arg=0x0C)     # LMMM DIX DIY
    s += command(0x93, sx=ppl - 5, sy=10, dx=3, dy=88, nx=30, ny=1)          # SX clipped
    s += command(0x90, sx=3, sy=10, dx=40, dy=89, nx=5, ny=1, arg=0x10)      # MXS: reads FFh
    s += command(0x50, dx=5, dy=89, col=top, arg=0x20, first=36)             # PSET MXD
    s += command(0x51, dx=1, dy=50, col=0xFF, first=36)                      # PSET color masked
    s += status(*STAT)
    return s


def seq_cmd_line(name):
    """LINE (X / Y major, all DIX / DIY, NX = 0, top stop, edge stop), SRCH
    (EQ / NE, DIX, found / not found, BD), POINT."""
    rng = random.Random(3000 + list(CMD_MODES).index(name))
    ppl = CMD_MODES[name][1]
    top = (1 << CMD_MODES[name][2]) - 1
    s = _prep(name, rng)
    lines = [
        (40, 100, 30, 7, 0x00), (40, 100, 30, 7, 0x04), (40, 100, 30, 7, 0x08), (40, 100, 30, 7, 0x0C),
        (40, 100, 30, 9, 0x01), (40, 100, 30, 9, 0x05), (40, 100, 30, 9, 0x09), (40, 100, 30, 9, 0x0D),
        (120, 20, 5, 5, 0x00), (120, 20, 0, 0, 0x00), (150, 3, 10, 40, 0x09),    # 45 deg, NX = 0, top
        (ppl - 4, 130, 20, 3, 0x00), (3, 140, 20, 3, 0x04),                      # right / left edge
        (200, 140, 12, 30, 0x00),                                                 # NY > NX
    ]
    for k, (dx, dy, nx, ny, arg) in enumerate(lines):
        op = LOGOPS[k % len(LOGOPS)]
        s += command(0x70 | op, dx=dx, dy=dy, nx=nx, ny=ny, col=rng.randint(1, top), arg=arg, first=36)
        s += status(2, 8, 9)
    s += command(0x73, dx=10, dy=150, nx=40, ny=3, col=top, arg=0x20, first=36)   # MXD
    # SRCH on line 10: a color there, a color that is not (unless by
    # chance), from the right with DIX, EQ (stop on a different color).
    for sx, col, arg in [(0, 0, 0x00), (5, 0, 0x04), (0, top, 0x02), (ppl - 1, 0, 0x06), (3, 0x55, 0x00),
                         (20, 0, 0x10)]:
        s += command(0x60, sx=sx, sy=10, col=col, arg=arg, first=32)
        s += status(2, 8, 2, 9, 2)
    s += command(0x60, sx=0, sy=200, col=0, arg=0x02)                        # not found: BD stays
    s += status(2, 8, 9)
    for sx, sy in [(0, 10), (1, 10), (7, 11), (ppl - 1, 11)]:
        s += command(0x40, sx=sx, sy=sy, arg=0x00)
        s += status(7, 2)
    s += command(0x40, sx=2, sy=10, arg=0x10)                                # MXS: FFh
    s += status(7)
    return s


def seq_cmd_transfer(name):
    """HMMC, LMMC (logical operations), LMCM through R#44 / S#7 with the TR
    handshake; the first byte from the R#44 written before R#46, or from
    the first R#44 after it; STOP in the middle of a transfer."""
    rng = random.Random(4000 + list(CMD_MODES).index(name))
    ppl = CMD_MODES[name][1]
    top = (1 << CMD_MODES[name][2]) - 1
    s = _prep(name, rng)
    # HMMC 6 x 3 bytes, the first byte in R#44 with the command.
    data = [rng.randint(0, 255) for _ in range(18)]
    ppb = 8 // CMD_MODES[name][2]
    s += command(0xF0, dx=10, dy=20, nx=6 * ppb, ny=3, col=data[0], wait=False)
    s += reg(17, 0x80 | 44) + [("tr_out", bytes(data[1:5]))] + status(2)
    s += [("tr_out", bytes(data[5:]))] + [("wait_ce",)] + status(2, 2)
    # HMMC DIX DIY, without R#44 before R#46: the first R#44 is the first byte.
    s += command(0, dx=40, dy=30, nx=4 * ppb, ny=2, first=36, last=43)       # R36-R43 (no start)
    s += reg(17, 45) + [("block", 0x9B, bytes([0x0C, 0xF0]))]
    s += reg(17, 0x80 | 44) + [("tr_out", bytes(rng.randint(0, 255) for _ in range(8)))]
    s += [("wait_ce",)] + status(2)
    # LMMC with each logical operation, 5 x 2 pixels on a non-zero background.
    s += command(0xC0, dx=0, dy=40, nx=ppl, ny=24, col=0x96 if top != 3 else 0x9C)
    for k, op in enumerate(LOGOPS):
        pix = [rng.randint(0, top) if rng.random() < 0.7 else 0 for _ in range(10)]
        s += command(0xB0 | op, dx=k * 7 + 1, dy=40 + 2 * k, nx=5, ny=2, col=pix[0], arg=0x04 if k == 3 else 0,
                     wait=False)
        s += reg(17, 0x80 | 44) + [("tr_out", bytes(pix[1:]))] + [("wait_ce",)]
    s += status(2)
    # LMCM: 7 x 2 pixels from line 10, then DIX.
    s += command(0xA0, sx=5, sy=10, nx=7, ny=2, wait=False) + [("tr_in", 14)] + status(2, 8)
    s += command(0xA0, sx=30, sy=10, nx=5, ny=1, arg=0x04, wait=False) + [("tr_in", 5)] + status(2, 7, 2)
    # STOP in the middle of an LMMC, then R#44 when idle clears TR.
    s += command(0xB0, dx=0, dy=70, nx=8, ny=2, col=top, wait=False)
    s += reg(17, 0x80 | 44) + [("tr_out", bytes([1, 2, 3]))]
    s += reg(46, 0x00) + status(2) + reg(44, 0x12) + status(2)
    # S#7 read before an HMMC sets the transfer flag: R#44 = 33h is written
    # first, then 2 more bytes.
    s += reg(44, 0x33) + status(7)
    s += command(0xF0, dx=0, dy=80, nx=3 * ppb, ny=1, first=36, last=43)
    s += reg(17, 46) + [("block", 0x9B, bytes([0xF0]))]
    s += reg(17, 0x80 | 44) + [("tr_out", bytes([0x44, 0x55]))] + [("wait_ce",)] + status(2)
    return s


def seq_cmd_misc():
    """Commands in a non-bitmap mode (nothing happens), unused register bits
    set, R#44 read back after POINT, DY past 1023 (HMMV wraps, LINE)."""
    s = reg(0, 0x00) + reg(1, 0x40) + [("delay", 255)]                       # G1
    s += command(0xC0, dx=0, dy=0, nx=16, ny=4, col=0x55)
    s += status(2)
    s += set_mode("G4")
    s += fill_line("G4", 10, list(range(1, 129)))
    s += reg(17, 32) + [("block", 0x9B, bytes([0xFF] * 14))]                   # unused bits set (R32-R45)
    s += command(0x40, sx=1, sy=10, first=44)                                # POINT, R#44 = 0
    s += status(7, 2)
    s += command(0xC0, dx=0x1F0, dy=1022, nx=0x3FF, ny=4, col=0x5A)          # DY wraps past 1023
    s += status(2, 8, 9)
    s += command(0x70, dx=10, dy=1020, nx=6, ny=2, col=7, arg=0x01, first=36)   # Y major past 1023
    return s


def seq_cmd_nonbitmap():
    """V9958 (MSX2+) with R#25 CMD = 1: commands in G1 use the G7 pixel
    coordinates on a linear 256 byte per line VRAM (openMSX NonBitmapMode)."""
    rng = random.Random(5000)
    s = reg(0, 0x00) + reg(1, 0x40) + reg(25, 0x40) + [("delay", 255)]
    s += waddr(10 * 256) + [("block", 0x98, bytes(rng.choice([0, rng.randint(1, 255)]) for _ in range(256)))]
    s += command(0xC0, dx=3, dy=20, nx=5, ny=3, col=0x5A)                    # HMMV
    s += command(0xD0, sx=7, sy=10, dx=200, dy=21, nx=30, ny=1)              # HMMM
    s += command(0xE0, sy=10, dx=250, dy=22, ny=1)                           # YMMM
    s += command(0x83, dx=2, dy=21, nx=7, ny=2, col=0x0F)                    # LMMV EOR
    s += command(0x98, sx=0, sy=10, dx=40, dy=23, nx=40, ny=1)               # LMMM TIMP
    s += command(0x74, dx=60, dy=30, nx=20, ny=6, col=0x81, first=36)        # LINE NOT
    s += command(0x52, dx=1, dy=1, col=0x18, first=36)                       # PSET OR
    s += command(0x40, sx=2, sy=10) + status(7)                              # POINT
    s += command(0x60, sx=0, sy=10, col=0, arg=0x00) + status(2, 8, 9)       # SRCH
    s += command(0xF0, dx=100, dy=40, nx=2, ny=2, col=0x11, wait=False)      # HMMC
    s += reg(17, 0x80 | 44) + [("tr_out", bytes([0x22, 0x33, 0x44]))] + [("wait_ce",)] + status(2)
    return s


for _m in CMD_MODES:
    SEQUENCES_CMD = globals().setdefault("SEQUENCES_CMD", {})
    SEQUENCES_CMD[f"{_m}_move"] = (lambda m=_m: seq_cmd_move(m))
    SEQUENCES_CMD[f"{_m}_logic"] = (lambda m=_m: seq_cmd_logic(m))
    SEQUENCES_CMD[f"{_m}_line"] = (lambda m=_m: seq_cmd_line(m))
    SEQUENCES_CMD[f"{_m}_transfer"] = (lambda m=_m: seq_cmd_transfer(m))
SEQUENCES_CMD["misc"] = seq_cmd_misc
SEQUENCES_CMD["nonbitmap"] = seq_cmd_nonbitmap

# Sequences that need a V9958 (MSX2+ machine).
V9958_SEQUENCES = {"nonbitmap"}
