"""Reference model of the V9938 (and the TMS9918A subset), for the tests.

Functional model of what the VDP shows, following openMSX (src/video), which
is the most hardware-verified V9938 emulation:

- 128 KB of physical VRAM.  In G6 / G7 the logical address is rotated like
  the real two-bank VRAM: physical = ((logical << 16) | (logical >> 1)).
- Table addresses use the openMSX masking scheme: the registers give a base
  with all the low bits set, and the table index is ORed with a mask of the
  bits above the table size, then ANDed with the base.
- Display modes T1, T2, G1, G2, G3, MC, G4, G5, G6, G7, sprite modes 1 and 2,
  192 / 212 lines, vertical scroll (R#23).
- The CPU interface: ports 98h-9Bh, address latch, R#14, palette (R#16 and
  port 9Ah), indirect register access (R#17 and port 9Bh), status registers.
- The command engine (R#32-R#46, S#2 TR / BD / CE, S#7, S#8 / S#9), see
  v9938_cmd.py: commands run at once when R#46 is written, except the CPU
  transfers (HMMC / LMMC: one step per R#44 write, LMCM: per S#7 read).

The picture is produced as color codes, not RGB: palette indices 0-15 in
every mode except G7, where it is the 8-bit GGGRRRBB value.  An active line
is always 512 samples wide (half pixels) covering the 256 pixel area; 256
pixel modes repeat each pixel, and the text modes (240 / 480 pixels) start
TEXT_OFFSET pixels into it, filled with the border color around.
"""

import numpy as np

from v9938_cmd import CmdEngine

VRAM_SIZE = 0x20000

# Display modes, by M5 M4 M3 M2 M1 like openMSX DisplayMode.
G1, T1, MC, G2, G3, T2, G4, G5, G6, G7 = 0x00, 0x01, 0x02, 0x04, 0x08, 0x09, 0x0C, 0x10, 0x14, 0x1C
MODE_NAMES = {G1: "G1", T1: "T1", MC: "MC", G2: "G2", G3: "G3", T2: "T2",
              G4: "G4", G5: "G5", G6: "G6", G7: "G7"}
BITMAP_MODES = (G4, G5, G6, G7)
PLANAR_MODES = (G6, G7)
TEXT_MODES = (T1, T2)

# Text modes start this many pixels into the 256 pixel area: 9 on the
# V99x8, 6 on the TMS9918A (openMSX VDP::getLeftSprites).
TEXT_OFFSET_V9938 = 9
TEXT_OFFSET_TMS = 6

# MSX2 BIOS default palette, (R, G, B) 3 bits each.
DEFAULT_PALETTE = [
    (0, 0, 0), (0, 0, 0), (1, 6, 1), (3, 7, 3), (1, 1, 7), (2, 3, 7), (5, 1, 1), (2, 6, 7),
    (7, 1, 1), (7, 3, 3), (6, 6, 1), (6, 6, 4), (1, 4, 1), (6, 2, 5), (5, 5, 5), (7, 7, 7),
]

# Sprite colors in G7 (fixed), as G7 codes GGGRRRBB.  From openMSX
# Renderer::GRAPHIC7_SPRITE_PALETTE (GRB 3 bits each; the blues are 0, 2, 7,
# which the 2-bit G7 blue encodes as 0, 1, 3).
G7_SPRITE_COLORS = [0x00, 0x01, 0x0C, 0x0D, 0x60, 0x61, 0x6C, 0x6D, 0x9D, 0x03, 0x1C, 0x1F, 0xE0, 0xE3, 0xFC, 0xFF]


def planar(addr):
    """Logical to physical address in the planar (G6 / G7) modes."""
    return ((addr << 16) | (addr >> 1)) & 0x1FFFF


def masked(base, index, bits):
    """openMSX table read: base & (index | ~((1 << bits) - 1))."""
    return base & (index | (~((1 << bits) - 1) & 0x1FFFF))


class V9938:
    def __init__(self, vram=None, regs=None, palette=None):
        self.vram = bytearray(VRAM_SIZE) if vram is None else bytearray(vram)
        self.regs = [0] * 48
        if regs:
            for i, v in enumerate(regs):
                self.regs[i] = v & 0xFF
        self.palette = list(palette or DEFAULT_PALETTE)
        self.status = [0] * 10
        self.status[1] = 0x00               # ID 0 = V9938
        self.status[2] = 0x0C               # unused bits 1; TR, BD, CE from the command engine
        self.status[4] = 0xFE               # unused bits read as 1
        self.status[6] = 0xFC
        self.status[9] = 0xFE
        # CPU interface state.
        self.addr = 0                       # 14-bit pointer (R#14 has A16-A14)
        self.latch = None                   # first control byte
        self.palette_latch = None
        self.palette_written = set()        # entries written through port 9Ah
        self.read_ahead = 0
        self.v9958 = False                  # R#25 CMD bit: commands in non-bitmap modes
        # Command engine, loaded with R#32-R#45 (R#46 = 0: no command).
        self.cmd = CmdEngine(self)
        for r in range(32, 46):
            self.cmd.write_reg(r - 32, self.regs[r])
        self.cmd.CMD = 0
        self._cmd_regs()

    # -- Mode and tables ----------------------------------------------------

    @property
    def mode(self):
        r0, r1 = self.regs[0], self.regs[1]
        m1 = (r1 >> 4) & 1
        m2 = (r1 >> 3) & 1
        m3 = (r0 >> 1) & 1
        m4 = (r0 >> 2) & 1
        m5 = (r0 >> 3) & 1
        return (m5 << 4) | (m4 << 3) | (m3 << 2) | (m2 << 1) | m1

    @property
    def lines(self):
        return 212 if self.regs[9] & 0x80 else 192

    @property
    def sprite_mode(self):
        m = self.mode
        if m in TEXT_MODES:
            return 0
        if m in (G1, G2, MC):
            return 1
        return 2

    def name_base(self):
        return ((self.regs[2] << 10) | 0x3FF) & 0x1FFFF

    def color_base(self):
        return ((self.regs[10] << 14) | (self.regs[3] << 6) | 0x3F) & 0x1FFFF

    def pattern_base(self):
        return ((self.regs[4] << 11) | 0x7FF) & 0x1FFFF

    def vram_read(self, addr):
        """Read a logical address as the display does in the current mode."""
        if self.mode in PLANAR_MODES:
            addr = planar(addr)
        return self.vram[addr & 0x1FFFF]

    # -- CPU interface ------------------------------------------------------

    def _vram_addr(self):
        addr = ((self.regs[14] & 7) << 14) | self.addr
        return planar(addr) if self.mode in PLANAR_MODES else addr

    def _increment(self):
        self.addr = (self.addr + 1) & 0x3FFF
        if self.addr == 0 and self.mode not in (G1, G2, MC, T1):
            self.regs[14] = (self.regs[14] + 1) & 7

    def write_data(self, value):
        self.latch = None
        self.vram[self._vram_addr()] = value
        self.read_ahead = value
        self._increment()

    def read_data(self):
        self.latch = None
        value = self.read_ahead
        self.read_ahead = self.vram[self._vram_addr()]
        self._increment()
        return value

    def write_ctrl(self, value):
        if self.latch is None:
            self.latch = value
            return
        first, self.latch = self.latch, None
        if value & 0x80:
            self.write_reg(value & 0x3F, first)
        else:
            self.addr = ((value & 0x3F) << 8) | first
            if not value & 0x40:
                self.read_ahead = self.vram[self._vram_addr()]
                self._increment()

    def read_status(self):
        self.latch = None
        n = self.regs[15] & 0x0F
        if n == 2:
            self.cmd.sync()
            value = (self.status[2] & ~0x91) | self.cmd.status
        elif n == 7:
            value = self.cmd.read_s7()
        elif n == 8:
            value = self.cmd.read_s8()
        elif n == 9:
            value = self.cmd.read_s9()
        else:
            value = self.status[n] if n < len(self.status) else 0xFF
        if n == 0:
            self.status[0] &= 0x1F
        self._cmd_regs()
        return value

    def write_palette(self, value):
        if self.palette_latch is None:
            self.palette_latch = value
            return
        first, self.palette_latch = self.palette_latch, None
        n = self.regs[16] & 0x0F
        self.palette[n] = ((first >> 4) & 7, value & 7, first & 7)
        self.palette_written.add(n)
        self.regs[16] = (n + 1) & 0x0F

    def write_indirect(self, value):
        reg = self.regs[17] & 0x3F
        if reg != 17:
            self.write_reg(reg, value)
        if not self.regs[17] & 0x80:
            self.regs[17] = (self.regs[17] & 0xC0) | ((reg + 1) & 0x3F)

    def write_reg(self, reg, value):
        if 32 <= reg < 47:
            self.cmd.write_reg(reg - 32, value & 0xFF)
            self._cmd_regs()
            return
        if reg < len(self.regs):
            self.regs[reg] = value & 0xFF
        if reg == 16:
            self.palette_latch = None
        if reg in (0, 1, 25):
            self.cmd.mode_changed()
            self._cmd_regs()

    def _cmd_regs(self):
        """R#32-R#46 read back from the command engine."""
        for i in range(15):
            self.regs[32 + i] = self.cmd.read_reg(i)

    # -- Display ------------------------------------------------------------

    def border(self):
        """Border color code (G7: the whole R#7; G5: the even half pixel,
        R#7 bits 3-2, see border_line)."""
        if self.mode == G7:
            return self.regs[7]
        if self.mode == G5:
            return (self.regs[7] >> 2) & 3
        return self.regs[7] & 0x0F

    def border_line(self):
        """512 border samples: in G5 the half pixels alternate between
        R#7 bits 3-2 and bits 1-0."""
        line = np.full(512, self.border(), dtype=np.uint16)
        if self.mode == G5:
            line[1::2] = self.regs[7] & 3
        return line

    def transparent(self):
        """Color 0 is transparent (shows the border color) unless TP."""
        return not self.regs[8] & 0x20

    def render(self):
        """Active area: (lines, 512) array of color codes, and the status
        flags produced by the sprites."""
        n = self.lines
        img = np.zeros((n, 512), dtype=np.uint16)
        sprites = self._sprites(n)
        for line in range(n):
            img[line] = self._render_line(line, sprites.get(line, []))
        return img

    def _display_y(self, line):
        return (line + self.regs[23]) & 0xFF

    def _render_line(self, line, line_sprites):
        mode = self.mode
        y = self._display_y(line)
        bg = self.border()
        tp = self.transparent()
        out = self.border_line()

        if mode == T1:
            fg, tbg = self.regs[7] >> 4, self.regs[7] & 0x0F
            for col in range(40):
                name = self.vram_read(masked(self.name_base(), ((y // 8) * 40 + col + 0xC00), 12))
                bits = self.vram_read(masked(self.pattern_base(), name * 8 + (y & 7), 11))
                for px in range(6):
                    c = fg if bits & (0x80 >> px) else tbg
                    if c == 0 and tp:
                        c = bg
                    x = 2 * TEXT_OFFSET_V9938 + col * 12 + px * 2
                    out[x: x + 2] = c
            return out
        if mode == T2:
            fg, tbg = self.regs[7] >> 4, self.regs[7] & 0x0F
            for col in range(80):
                name = self.vram_read(masked(self.name_base(), (y // 8) * 80 + col, 12))
                bits = self.vram_read(masked(self.pattern_base(), name * 8 + (y & 7), 11))
                for px in range(6):
                    c = fg if bits & (0x80 >> px) else tbg
                    if c == 0 and tp:
                        c = bg
                    out[2 * TEXT_OFFSET_V9938 + col * 6 + px] = c
            return out

        if mode in BITMAP_MODES:
            pix = self._bitmap_line(y)
        else:
            pix = self._char_line(y)

        if mode in (G5, G6):
            pix512 = pix
        else:
            pix512 = np.repeat(pix, 2)
        if tp and mode != G7:
            # Background color 0 shows the border (G7 has no transparency).
            pix512 = np.where(pix512 == 0, out, pix512)

        # Sprites over the background: a sprite pixel is never transparent
        # once drawn (in G5 each half takes 2 bits of the sprite color).
        return self._draw_sprites(pix512.astype(np.uint16), line_sprites)

    def _char_line(self, y):
        mode = self.mode
        pix = np.zeros(256, dtype=np.uint16)
        row, l = y // 8, y & 7
        for col in range(32):
            name = self.vram_read(masked(self.name_base(), row * 32 + col, 10))
            if mode == G1:
                bits = self.vram_read(masked(self.pattern_base(), name * 8 + l, 11))
                color = self.vram_read(masked(self.color_base(), name >> 3, 6))
                fg, bg = color >> 4, color & 0x0F
            elif mode in (G2, G3):
                char = name | ((y & 0xC0) << 2)
                bits = self.vram_read(masked(self.pattern_base(), char * 8 + l, 13))
                color = self.vram_read(masked(self.color_base(), char * 8 + l, 13))
                fg, bg = color >> 4, color & 0x0F
            else:  # MC
                color = self.vram_read(masked(self.pattern_base(), name * 8 + ((row & 3) * 2) + (l >> 2), 11))
                fg, bg = color >> 4, color & 0x0F
                bits = 0xF0
            for px in range(8):
                pix[col * 8 + px] = fg if bits & (0x80 >> px) else bg
        return pix

    def _bitmap_line(self, y):
        """One display line of G4-G7: 256 (G4, G7) or 512 (G5, G6) codes."""
        mode = self.mode
        base = self.name_base()
        if mode in PLANAR_MODES:
            vline = (base >> 7) & (0x100 | y)
            data = [self.vram[planar(vline * 256 + i)] for i in range(256)]
        else:
            vline = (base >> 7) & (0x300 | y)
            data = [self.vram[(vline * 128 + i) & 0x1FFFF] for i in range(128)]
        if mode == G4:
            return np.array([v for b in data for v in (b >> 4, b & 15)], dtype=np.uint16)
        if mode == G5:
            return np.array([(b >> s) & 3 for b in data for s in (6, 4, 2, 0)], dtype=np.uint16)
        if mode == G6:
            return np.array([v for b in data for v in (b >> 4, b & 15)], dtype=np.uint16)
        return np.array(data, dtype=np.uint16)                          # G7

    # -- Sprites ------------------------------------------------------------

    def _sat_base(self):
        return ((self.regs[11] << 15) | (self.regs[5] << 7) | 0x7F) & 0x1FFFF

    def _spg_base(self):
        return ((self.regs[6] << 11) | 0x7FF) & 0x1FFFF

    def _sprite_read(self, base, index, bits):
        return self.vram_read(masked(base, index, bits))

    def _sprites(self, n_lines):
        """Per display line, the visible sprites (x, 32-bit pattern, color
        attribute), and the sprite status bits in S#0."""
        sm = self.sprite_mode
        result = {}
        if sm == 0 or self.regs[8] & 0x02:          # text mode or SPD
            return result
        size = 16 if self.regs[1] & 0x02 else 8
        mag = bool(self.regs[1] & 0x01)
        span = size * (2 if mag else 1)
        limit = 4 if sm == 1 else 8
        stop = 208 if sm == 1 else 216
        sat, spg = self._sat_base(), self._spg_base()
        attr_off = 0 if sm == 1 else 512
        idx_bits = 7 if sm == 1 else 10
        counts = [0] * n_lines
        fifth = None
        for s in range(32):
            ys, x, name = (self._sprite_read(sat, attr_off + s * 4 + k, idx_bits) for k in range(3))
            if ys == stop:
                break
            if sm == 1:
                color1 = self._sprite_read(sat, attr_off + s * 4 + 3, idx_bits)
            for line in range(n_lines):
                # Sprites are checked one line ahead and shown one lower.
                spr_line = (self._display_y(line) - 1 - ys) & 0xFF
                if spr_line >= span:
                    continue
                if counts[line] == limit:
                    if fifth is None or line < fifth[0]:
                        fifth = (line, s)
                    continue
                counts[line] += 1
                row = spr_line // (2 if mag else 1)
                if sm == 1:
                    attr = color1 & 0x8F
                else:
                    attr = self._sprite_read(sat, s * 16 + row, idx_bits)
                pname = name & 0xFC if size == 16 else name
                bits = self._sprite_read(spg, pname * 8 + row, 11) << 24
                if size == 16:
                    bits |= self._sprite_read(spg, pname * 8 + 16 + row, 11) << 16
                if mag:
                    wide = 0
                    for i in range(16):
                        if bits & (0x80000000 >> i):
                            wide |= 0xC0000000 >> (2 * i)
                    bits = wide
                sx = x - 32 if attr & 0x80 else x
                result.setdefault(line, []).append((sx, bits, attr))
        if fifth is not None:
            self.status[0] = 0x40 | (self.status[0] & 0xA0) | fifth[1]
        return result

    def _draw_sprites(self, pix, line_sprites):
        """Draw the sprites of one line over the 512 background samples,
        like openMSX SpriteConverter."""
        mode = self.mode
        sm = self.sprite_mode
        tp = self.transparent()
        out = pix.copy()
        if not line_sprites:
            return out

        def put(x, color):
            if not 0 <= x < 256:
                return
            if mode == G7:
                color = G7_SPRITE_COLORS[color]
            if mode == G5:
                out[2 * x] = color >> 2
                out[2 * x + 1] = color & 3
            else:
                out[2 * x] = color
                out[2 * x + 1] = color

        if sm == 1:
            # Lower numbers on top; color 0 is never drawn.
            for sx, bits, attr in reversed(line_sprites):
                c = attr & 0x0F
                if c == 0:
                    continue
                for i in range(32):
                    if bits & (0x80000000 >> i):
                        put(sx + i, c)
            return out

        # Sprite mode 2: CC = 1 sprites OR their color into the previous
        # CC = 0 sprite, and are only visible after one.
        first = 0
        while first < len(line_sprites) and line_sprites[first][2] & 0x40:
            first += 1
        for i in range(len(line_sprites) - 1, first - 1, -1):
            sx, bits, attr = line_sprites[i]
            c = attr & 0x0F
            if c == 0 and tp:
                continue
            for p in range(32):
                if not bits & (0x80000000 >> p):
                    continue
                x = sx + p
                color = c
                for sx2, bits2, attr2 in line_sprites[i + 1:]:
                    if not attr2 & 0x40:
                        break
                    shift = x - sx2
                    if 0 <= shift < 32 and bits2 & (0x80000000 >> shift):
                        color |= attr2 & 0x0F
                put(x, color)
        return out


def from_registers(vram, regs, palette=None):
    """Build a model from a VRAM image and a register list."""
    return V9938(vram=vram, regs=regs, palette=palette)
