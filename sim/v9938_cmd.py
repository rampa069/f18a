"""V9938 command engine, functional model (no timing).

A port of the functional behaviour of openMSX VDPCmdEngine (src/video),
ignoring its access slot timing.  It follows current openMSX (master, the
oracle image openmsx-master of sim/openmsx_oracle.py); CmdEngine(openmsx=
"20.0") selects the three differences of openMSX 20.0 (Debian's package),
marked "master" below:
- SRCH not found: 20.0 clears BD, master leaves it.
- S#9 read: master clears BD, 20.0 does not.
- LINE: master stops when DY goes above line 0 (DIY), 20.0 wraps to 1023.


- Commands that need no CPU transfer (POINT, PSET, SRCH, LINE, LMMV, LMMM,
  HMMV, HMMM, YMMM, STOP) run to completion when R#46 is written.
- HMMC and LMMC consume one byte / pixel each time the CPU writes R#44, and
  LMCM produces one pixel in S#7 each time the CPU reads S#7 (see
  "Transfers" below).

The registers are kept like openMSX: SX, SY, DX, DY, NX, NY are unsigned
32-bit counters that a command steps without masking (DY can end at 1024+
or at 0xFFFFFFFF); the CPU writes the low / high byte (the high byte keeps
1 bit for SX / DX and 2 bits for the others, and clears everything above),
and R#32-R#46 read back as (value >> 8) & 0xFF for the high bytes.

Transfers (openMSX behaviour):
- `transfer` is a flag set by every write of R#44 and every read of S#7,
  whatever the command; HMMC / LMMC / LMCM consume it.  HMMC / LMMC do not
  clear it at start, so a write of R#44 before R#46 gives the first byte /
  pixel.  LMCM sets it at start, so the first pixel is read right away.
- TR (S#2 bit 7) is set at the start of HMMC / LMMC / LMCM and only cleared
  by a write of R#44 or a read of S#7 while no command is running: it stays
  1 during the whole transfer and after it ends.
- BD (S#2 bit 4) is set by SRCH when the color is found and cleared when
  it is not found (20.0; master: only cleared by a read of S#9).
- S#8 / S#9 give the internal source X counter ASX, whatever the command.
"""

M32 = 0xFFFFFFFF

# ARG bits
MXD, MXS, DIY, DIX, EQ, MAJ = 0x20, 0x10, 0x08, 0x04, 0x02, 0x01
# S#2 bits
TR, BD, CE = 0x80, 0x10, 0x01

# Commands (R#46 bits 7-4)
STOP, POINT, PSET, SRCH, LINE, LMMV, LMMM, LMCM, LMMC, HMMV, HMMM, YMMM, HMMC = (
    0x0, 0x4, 0x5, 0x6, 0x7, 0x8, 0x9, 0xA, 0xB, 0xC, 0xD, 0xE, 0xF)
# Logical operations (R#46 bits 3-0)
IMP, AND, OR, EOR, NOT, TIMP, TAND, TOR, TEOR, TNOT = 0, 1, 2, 3, 4, 8, 9, 10, 11, 12


class _Mode:
    """Pixel addressing of one screen mode (openMSX Graphic4Mode, ...).
    Addresses are physical VRAM addresses (G6 / G7 already planar)."""

    def __init__(self, name, ppl, shift, color_mask):
        self.name = name
        self.ppl = ppl                  # pixels per line
        self.shift = shift              # log2(pixels per byte)
        self.color_mask = color_mask

    def address(self, x, y):
        n = self.name
        if n == "G4":
            return ((y & 1023) << 7) | ((x & 255) >> 1)
        if n == "G5":
            return ((y & 1023) << 7) | ((x & 511) >> 2)
        if n == "G6":
            return ((x & 2) << 15) | ((y & 511) << 7) | ((x & 511) >> 2)
        if n == "G7":
            return ((x & 1) << 16) | ((y & 511) << 7) | ((x & 255) >> 1)
        return ((y & 511) << 8) | (x & 255)          # non bitmap (V9958)

    def point_of(self, byte, x):
        n = self.name
        if n in ("G4", "G6"):
            return (byte >> (((~x) & 1) << 2)) & 15
        if n == "G5":
            return (byte >> (((~x) & 3) << 1)) & 3
        return byte

    def shifted(self, x, color):
        """(color moved to the pixel position, mask with 1s elsewhere)."""
        n = self.name
        if n in ("G4", "G6"):
            sh = ((~x) & 1) << 2
            return (color << sh) & 0xFF, ~(15 << sh) & 0xFF
        if n == "G5":
            sh = ((~x) & 3) << 1
            return (color << sh) & 0xFF, ~(3 << sh) & 0xFF
        return color, 0


MODES = {
    "G4": _Mode("G4", 256, 1, 0x0F),
    "G5": _Mode("G5", 512, 2, 0x03),
    "G6": _Mode("G6", 512, 1, 0x0F),
    "G7": _Mode("G7", 256, 0, 0xFF),
    "NB": _Mode("NB", 256, 0, 0xFF),
}


def logop(op, src, color, mask):
    """New byte for a logical operation, or None when nothing is written.
    color is already at the pixel position, mask has 1s at the other
    pixels (0 in G7).  The T operations skip the write when the pixel color
    is 0; the undefined codes 5-7, 13-15 never write."""
    if op & 8:
        if color == 0:
            return None
        op &= 7
    if op == IMP:
        return (src & mask) | color
    if op == AND:
        return src & (color | mask)
    if op == OR:
        return src | color
    if op == EOR:
        return src ^ color
    if op == NOT:
        return (src & mask) | (~(color | mask) & 0xFF)
    return None


def clip_nx_1_pixel(m, dx, nx, arg):
    if dx >= m.ppl:
        return 1
    nx = nx or m.ppl
    return min(nx, dx + 1) if arg & DIX else min(nx, m.ppl - dx)


def clip_nx_1_byte(m, dx, nx, arg):
    bpl = m.ppl >> m.shift
    dx >>= m.shift
    if bpl <= dx:
        return 1
    nx >>= m.shift
    nx = nx or bpl
    return min(nx, dx + 1) if arg & DIX else min(nx, bpl - dx)


def clip_nx_2_pixel(m, sx, dx, nx, arg):
    if sx >= m.ppl or dx >= m.ppl:
        return 1
    nx = nx or m.ppl
    return min(nx, min(sx, dx) + 1) if arg & DIX else min(nx, m.ppl - max(sx, dx))


def clip_nx_2_byte(m, sx, dx, nx, arg):
    bpl = m.ppl >> m.shift
    sx >>= m.shift
    dx >>= m.shift
    if bpl <= sx or bpl <= dx:
        return 1
    nx >>= m.shift
    nx = nx or bpl
    return min(nx, min(sx, dx) + 1) if arg & DIX else min(nx, bpl - max(sx, dx))


def clip_ny_1(dy, ny, arg):
    ny = ny or 1024
    return min(ny, (dy + 1) & M32) if arg & DIY else ny


def clip_ny_2(sy, dy, ny, arg):
    ny = ny or 1024
    return min(ny, (min(sy, dy) + 1) & M32) if arg & DIY else ny


def _s32(v):
    return v - (1 << 32) if v & 0x80000000 else v


class CmdEngine:
    def __init__(self, vdp, openmsx="master"):
        self.vdp = vdp
        self.master = openmsx == "master"
        self.SX = self.SY = self.DX = self.DY = self.NX = self.NY = 0
        self.ASX = self.ADX = self.ANX = 0
        self.COL = self.ARG = self.CMD = 0
        self.status = 0                 # TR, BD, CE
        self.transfer = False
        self.has_ext_vram = False       # 128 KB: MXS reads 0xFF, MXD writes nothing

    # -- Mode ---------------------------------------------------------------

    def mode(self):
        """The command mode, or None when commands are not possible."""
        import v9938_model as vm
        m = self.vdp.mode
        name = {vm.G4: "G4", vm.G5: "G5", vm.G6: "G6", vm.G7: "G7"}.get(m)
        if name is None and getattr(self.vdp, "v9958", False) and self.vdp.regs[25] & 0x40:
            name = "NB"
        return MODES.get(name)

    def mode_changed(self):
        """A display mode change aborts a command when the new mode has no
        commands (openMSX updateDisplayMode)."""
        if self.CMD and self.mode() is None:
            self._done()

    # -- VRAM ---------------------------------------------------------------

    def _rd(self, addr):
        return self.vdp.vram[addr & 0x1FFFF]

    def _wr(self, addr, value):
        self.vdp.vram[addr & 0x1FFFF] = value & 0xFF

    def _point(self, m, x, y, ext):
        if ext and not self.has_ext_vram:
            return 0xFF
        return m.point_of(self._rd(m.address(x, y)), x)

    def _pset(self, m, x, y, ext, color, op):
        """Read-modify-write of one pixel with a logical operation."""
        if ext and not self.has_ext_vram:
            return
        addr = m.address(x, y)
        c, mask = m.shifted(x, color)
        v = logop(op, self._rd(addr), c, mask)
        if v is not None:
            self._wr(addr, v)

    # -- Registers ----------------------------------------------------------

    def read_reg(self, i):
        """R#32+i as the CPU (debugger) reads it back."""
        v = (self.SX, self.SX >> 8, self.SY, self.SY >> 8, self.DX, self.DX >> 8,
             self.DY, self.DY >> 8, self.NX, self.NX >> 8, self.NY, self.NY >> 8,
             self.COL, self.ARG, self.CMD)[i]
        return v & 0xFF

    def write_reg(self, i, value):
        """CPU write of R#32+i."""
        self.sync()
        if i == 0:
            self.SX = (self.SX & 0x100) | value
        elif i == 1:
            self.SX = (self.SX & 0x0FF) | ((value & 1) << 8)
        elif i == 2:
            self.SY = (self.SY & 0x300) | value
        elif i == 3:
            self.SY = (self.SY & 0x0FF) | ((value & 3) << 8)
        elif i == 4:
            self.DX = (self.DX & 0x100) | value
        elif i == 5:
            self.DX = (self.DX & 0x0FF) | ((value & 1) << 8)
        elif i == 6:
            self.DY = (self.DY & 0x300) | value
        elif i == 7:
            self.DY = (self.DY & 0x0FF) | ((value & 3) << 8)
        elif i == 8:
            self.NX = (self.NX & 0x300) | value
        elif i == 9:
            self.NX = (self.NX & 0x0FF) | ((value & 3) << 8)
        elif i == 10:
            self.NY = (self.NY & 0x300) | value
        elif i == 11:
            self.NY = (self.NY & 0x0FF) | ((value & 3) << 8)
        elif i == 12:
            self.COL = value
            # The real VDP resets TR for a moment on every write; openMSX
            # only clears it when no command is running.
            if not self.CMD:
                self.status &= ~TR
            self.transfer = True
        elif i == 13:
            self.ARG = value
        elif i == 14:
            self.CMD = value
            self._start()
        self.sync()

    # -- Status -------------------------------------------------------------

    def read_s7(self):
        self.sync()
        value = self.COL
        if not self.CMD:
            self.status &= ~TR
        self.transfer = True
        self.sync()
        return value

    def read_s8(self):
        return self.ASX & 0xFF

    def read_s9(self):
        value = ((self.ASX >> 8) & 0xFF) | 0xFE
        if self.master:
            self.status &= ~BD
        return value

    # -- Execution ----------------------------------------------------------

    def _done(self):
        # TR is left as it is.
        self.status &= ~CE
        self.CMD = 0

    def sync(self):
        """Run the pending transfer step of HMMC / LMMC / LMCM."""
        if not self.CMD or not self.transfer:
            return
        m = self.mode()
        cmd = self.CMD >> 4
        if m is None:
            return
        if cmd == LMCM:
            self._step_lmcm(m)
        elif cmd == LMMC:
            self._step_lmmc(m)
        elif cmd == HMMC:
            self._step_hmmc(m)

    def _start(self):
        m = self.mode()
        if m is None:
            self._done()                # no commands in this mode
            return
        self.status |= CE
        cmd = self.CMD >> 4
        if cmd < POINT:
            self._done()
        else:
            {POINT: self._point_cmd, PSET: self._pset_cmd, SRCH: self._srch, LINE: self._line,
             LMMV: self._lmmv, LMMM: self._lmmm, LMCM: self._lmcm, LMMC: self._lmmc,
             HMMV: self._hmmv, HMMM: self._hmmm, YMMM: self._ymmm, HMMC: self._hmmc}[cmd](m)

    def _tx(self, step=1):
        return (-step if self.ARG & DIX else step) & M32

    def _ty(self):
        return M32 if self.ARG & DIY else 1

    def _point_cmd(self, m):
        self.COL = self._point(m, self.SX, self.SY, self.ARG & MXS)
        self._done()

    def _pset_cmd(self, m):
        self._pset(m, self.DX, self.DY, self.ARG & MXD, self.COL & m.color_mask, self.CMD & 15)
        self._done()

    def _srch(self, m):
        self.ASX = self.SX
        cl = self.COL & m.color_mask
        aeq = bool(self.ARG & EQ)
        tx = self._tx()
        while True:
            p = self._point(m, self.ASX, self.SY, self.ARG & MXS)
            if (p == cl) ^ aeq:
                self.status |= BD
                break
            self.ASX = (self.ASX + tx) & M32
            if self.ASX & m.ppl:
                if not self.master:
                    self.status &= ~BD  # not found (master: BD is left as it is)
                break
        self._done()

    def _line(self, m):
        self.NY &= 1023
        self.ASX = ((self.NX - 1) & M32) >> 1
        self.ADX = self.DX
        self.ANX = 0
        cl = self.COL & m.color_mask
        op = self.CMD & 15
        tx, ty = self._tx(), self._ty()
        ext = self.ARG & MXD
        while True:
            self._pset(m, self.ADX, self.DY, ext, cl, op)
            if not self.ARG & MAJ:
                # X major: the end test is before DY moves.
                self.ADX = (self.ADX + tx) & M32
                end = self.ANX == self.NX or self.ADX & m.ppl
                self.ANX = (self.ANX + 1) & M32
                if end:
                    break
                if self.ASX < self.NY:
                    self.ASX = (self.ASX + self.NX) & M32
                    self.DY = (self.DY + ty) & M32
                    if self.master and ty == M32 and _s32(self.DY) < 0:
                        break           # master: stop above the top (20.0: wraps)
                self.ASX = ((self.ASX - self.NY) & M32) & 1023
            else:
                # Y major: DY moves before the end test.
                self.DY = (self.DY + ty) & M32
                if self.master and ty == M32 and _s32(self.DY) < 0:
                    break
                if self.ASX < self.NY:
                    self.ASX = (self.ASX + self.NX) & M32
                    self.ADX = (self.ADX + tx) & M32
                self.ASX = ((self.ASX - self.NY) & M32) & 1023
                end = self.ANX == self.NX or self.ADX & m.ppl
                self.ANX = (self.ANX + 1) & M32
                if end:
                    break
        self._done()

    def _next_line(self, two, reset):
        """End of a line of a block command: returns True when done.
        openMSX masks NY to 10 bits each time the engine runs, so only the
        last decrement can leave it negative (NY = 0, i.e. 1024 lines,
        clipped to one line)."""
        ty = self._ty()
        if two:
            self.SY = (self.SY + ty) & M32
        self.DY = (self.DY + ty) & M32
        self.NY = (self.NY - 1) & M32
        reset()                         # ASX / ADX / ANX back to the line start
        self.tmp_ny -= 1
        if self.tmp_ny == 0:
            self._done()
            return True
        self.NY &= 1023
        return False

    def _lmmv(self, m):
        self.NY &= 1023
        tmp_nx = clip_nx_1_pixel(m, self.DX, self.NX, self.ARG)
        self.tmp_ny = clip_ny_1(self.DY, self.NY, self.ARG)
        self.ADX, self.ANX = self.DX, tmp_nx
        cl = self.COL & m.color_mask
        op = self.CMD & 15
        tx = self._tx()
        def reset():
            self.ADX, self.ANX = self.DX, tmp_nx
        while True:
            self._pset(m, self.ADX, self.DY, self.ARG & MXD, cl, op)
            self.ADX = (self.ADX + tx) & M32
            self.ANX -= 1
            if self.ANX == 0:
                if self._next_line(False, reset):
                    return

    def _lmmm(self, m):
        self.NY &= 1023
        tmp_nx = clip_nx_2_pixel(m, self.SX, self.DX, self.NX, self.ARG)
        self.tmp_ny = clip_ny_2(self.SY, self.DY, self.NY, self.ARG)
        self.ASX, self.ADX, self.ANX = self.SX, self.DX, tmp_nx
        op = self.CMD & 15
        tx = self._tx()
        def reset():
            self.ASX, self.ADX, self.ANX = self.SX, self.DX, tmp_nx
        while True:
            src = self._point(m, self.ASX, self.SY, self.ARG & MXS)
            self._pset(m, self.ADX, self.DY, self.ARG & MXD, src, op)
            self.ASX = (self.ASX + tx) & M32
            self.ADX = (self.ADX + tx) & M32
            self.ANX -= 1
            if self.ANX == 0:
                if self._next_line(True, reset):
                    return

    def _hmmv(self, m):
        self.NY &= 1023
        tmp_nx = clip_nx_1_byte(m, self.DX, self.NX, self.ARG)
        self.tmp_ny = clip_ny_1(self.DY, self.NY, self.ARG)
        self.ADX, self.ANX = self.DX, tmp_nx
        tx = self._tx(1 << m.shift)
        dst_ok = not (self.ARG & MXD) or self.has_ext_vram
        def reset():
            self.ADX, self.ANX = self.DX, tmp_nx
        while True:
            if dst_ok:
                self._wr(m.address(self.ADX, self.DY), self.COL)
            self.ADX = (self.ADX + tx) & M32
            self.ANX -= 1
            if self.ANX == 0:
                if self._next_line(False, reset):
                    return

    def _hmmm(self, m):
        self.NY &= 1023
        tmp_nx = clip_nx_2_byte(m, self.SX, self.DX, self.NX, self.ARG)
        self.tmp_ny = clip_ny_2(self.SY, self.DY, self.NY, self.ARG)
        self.ASX, self.ADX, self.ANX = self.SX, self.DX, tmp_nx
        tx = self._tx(1 << m.shift)
        src_ok = not (self.ARG & MXS) or self.has_ext_vram
        dst_ok = not (self.ARG & MXD) or self.has_ext_vram
        def reset():
            self.ASX, self.ADX, self.ANX = self.SX, self.DX, tmp_nx
        while True:
            v = self._rd(m.address(self.ASX, self.SY)) if src_ok else 0xFF
            if dst_ok:
                self._wr(m.address(self.ADX, self.DY), v)
            self.ASX = (self.ASX + tx) & M32
            self.ADX = (self.ADX + tx) & M32
            self.ANX -= 1
            if self.ANX == 0:
                if self._next_line(True, reset):
                    return

    def _ymmm(self, m):
        # From DX to the left / right edge; source and destination X are
        # both DX; MXD selects the memory of both.
        self.NY &= 1023
        tmp_nx = clip_nx_1_byte(m, self.DX, 512, self.ARG)
        self.tmp_ny = clip_ny_2(self.SY, self.DY, self.NY, self.ARG)
        self.ADX, self.ANX = self.DX, tmp_nx
        tx = self._tx(1 << m.shift)
        ok = not (self.ARG & MXD) or self.has_ext_vram
        def reset():
            self.ADX, self.ANX = self.DX, tmp_nx
        while True:
            if ok:
                self._wr(m.address(self.ADX, self.DY), self._rd(m.address(self.ADX, self.SY)))
            self.ADX = (self.ADX + tx) & M32
            self.ANX -= 1
            if self.ANX == 0:
                if self._next_line(True, reset):
                    return

    # Transfer commands: start, then one step per transfer.

    def _lmcm(self, m):
        self.NY &= 1023
        self.ASX = self.SX
        self.ANX = clip_nx_1_pixel(m, self.SX, self.NX, self.ARG)
        self.transfer = True
        self.status |= TR

    def _step_lmcm(self, m):
        self.NY &= 1023
        tmp_nx = clip_nx_1_pixel(m, self.SX, self.NX, self.ARG)
        self.tmp_ny = clip_ny_1(self.SY, self.NY, self.ARG)
        self.ANX = clip_nx_1_pixel(m, self.ASX, self.ANX, self.ARG)
        self.COL = self._point(m, self.ASX, self.SY, self.ARG & MXS)
        self.transfer = False
        self.ASX = (self.ASX + self._tx()) & M32
        self.ANX -= 1
        if self.ANX == 0:
            self.SY = (self.SY + self._ty()) & M32
            self.NY = (self.NY - 1) & M32
            self.ASX, self.ANX = self.SX, tmp_nx
            self.tmp_ny -= 1
            if self.tmp_ny == 0:
                self._done()

    def _lmmc(self, m):
        self.NY &= 1023
        self.ADX = self.DX
        self.ANX = clip_nx_1_pixel(m, self.DX, self.NX, self.ARG)
        self.status |= TR               # 'transfer' is left as it is

    def _step_lmmc(self, m):
        self.NY &= 1023
        tmp_nx = clip_nx_1_pixel(m, self.DX, self.NX, self.ARG)
        self.tmp_ny = clip_ny_1(self.DY, self.NY, self.ARG)
        self.ANX = clip_nx_1_pixel(m, self.ADX, self.ANX, self.ARG)
        self._pset(m, self.ADX, self.DY, self.ARG & MXD, self.COL & m.color_mask, self.CMD & 15)
        self.transfer = False
        self.ADX = (self.ADX + self._tx()) & M32
        self.ANX -= 1
        if self.ANX == 0:
            self.DY = (self.DY + self._ty()) & M32
            self.NY = (self.NY - 1) & M32
            self.ADX, self.ANX = self.DX, tmp_nx
            self.tmp_ny -= 1
            if self.tmp_ny == 0:
                self._done()

    def _hmmc(self, m):
        self.NY &= 1023
        self.ADX = self.DX
        self.ANX = clip_nx_1_byte(m, self.DX, self.NX, self.ARG)
        self.status |= TR               # 'transfer' is left as it is

    def _step_hmmc(self, m):
        self.NY &= 1023
        tmp_nx = clip_nx_1_byte(m, self.DX, self.NX, self.ARG)
        self.tmp_ny = clip_ny_1(self.DY, self.NY, self.ARG)
        self.ANX = clip_nx_1_byte(m, self.ADX, self.ANX << m.shift, self.ARG)
        if not (self.ARG & MXD) or self.has_ext_vram:
            self._wr(m.address(self.ADX, self.DY), self.COL)
        self.transfer = False
        self.ADX = (self.ADX + self._tx(1 << m.shift)) & M32
        self.ANX -= 1
        if self.ANX == 0:
            self.DY = (self.DY + self._ty()) & M32
            self.NY = (self.NY - 1) & M32
            self.ADX, self.ANX = self.DX, tmp_nx
            self.tmp_ny -= 1
            if self.tmp_ny == 0:
                self._done()
