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

# V9958 YJK frames hold 15-bit RGB codes with this flag (render()).
YJK_RGB = 0x8000

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
        self.blink_state = False            # T2 blink: True = R#12 colors
        if self.regs[13] & 0xF0:
            self.blink_state = True         # as if R#13 was written
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

    def phys(self, addr):
        """VRAM cell of an address (already planar in G6 / G7).  self.vram
        is kept in the R#8 VR = 1 order; with VR = 0 (16K chips) only A14-A0
        count and they reach the cells like openMSX VDPVRAM::swapAddr."""
        addr &= 0x1FFFF
        if self.regs[8] & 0x08:
            return addr
        a = addr & 0x7FFF
        return 1 | ((a & 0x7F) << 1) | ((a & 0x7FC0) << 2)

    def vram_read(self, addr):
        """Read a logical address as the display does in the current mode."""
        if self.mode in PLANAR_MODES:
            addr = planar(addr)
        return self.vram[self.phys(addr)]

    # -- CPU interface ------------------------------------------------------

    def _vram_addr(self):
        addr = ((self.regs[14] & 7) << 14) | self.addr
        return self.phys(planar(addr) if self.mode in PLANAR_MODES else addr)

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
            if n == 1 and self.v9958:
                value |= 0x04                 # ID 2 = V9958
        if n == 0:
            self.status[0] &= 0x1F
        if n == 5:
            self._set_collision(0, 0)       # reading S#5 clears the coordinates
        self._cmd_regs()
        return value

    def _set_collision(self, x, y):
        """S#3-S#6: collision X and Y (openMSX SpriteChecker collisionX / Y)."""
        self.status[3] = x & 0xFF
        self.status[4] = ((x >> 8) & 0xFF) | 0xFE
        self.status[5] = y & 0xFF
        self.status[6] = ((y >> 8) & 0xFF) | 0xFC

    def _collisions(self, sprites, n_lines):
        """Sprite collision (openMSX SpriteChecker::findCollision1 / 2): the
        first display line where two colliding sprites have a pixel at the
        same x in the screen sets C and, unless C was set already, the
        coordinates: X = the lowest such x + 12, Y = the line + 7 (openMSX:
        the line the sprites are checked at, one before they show, + 8).  Color
        0 sprites only collide with TP; in mode 2 not with CC or IC set."""
        if self.status[0] & 0x20:
            return
        can0 = not self.transparent()
        for line in range(n_lines):
            spr = [(sx, bits) for sx, bits, attr in sprites.get(line, [])
                   if (can0 or attr & 0x0F) and not (self.sprite_mode == 2 and attr & 0x60)]
            xs = set()
            hit = None
            for sx, bits in spr:
                for p in range(32):
                    x = sx + p
                    if bits & (0x80000000 >> p) and 0 <= x < 256:
                        if x in xs and (hit is None or x < hit):
                            hit = x
                        xs.add(x)
            if hit is not None:
                self.status[0] |= 0x20
                self._set_collision(hit + 12, line + 7)
                return

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
        if reg == 13:
            # openMSX VDP::changeRegister: switch to the on state unless the
            # on period is 0 (the per frame alternation is not modelled).
            if self.blink_state == ((value & 0xF0) == 0):
                self.blink_state = not self.blink_state
        if reg in (0, 1, 25):
            self.cmd.mode_changed()
            self._cmd_regs()

    def _cmd_regs(self):
        """R#32-R#46 read back from the command engine."""
        for i in range(15):
            self.regs[32 + i] = self.cmd.read_reg(i)

    # -- Display ------------------------------------------------------------

    @property
    def yjk(self):
        """V9958 R#25 YJK in a bitmap mode (YJK only works in G6 / G7)."""
        return self.v9958 and self.regs[25] & 0x08 and self.mode in BITMAP_MODES

    def border(self):
        """Border color code (G7: the whole R#7; G5: the even half pixel,
        R#7 bits 3-2, see border_line)."""
        if self.mode == G7 and not self.yjk:
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
        self._collisions(sprites, n)
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

        # Text modes (openMSX PixelRenderer::draw, textModeCounter): R#23
        # only moves the line inside the characters, not the character rows.
        if mode == T1:
            fg, tbg = self.regs[7] >> 4, self.regs[7] & 0x0F
            for col in range(40):
                name = self.vram_read(masked(self.name_base(), ((line // 8) * 40 + col + 0xC00), 12))
                bits = self.vram_read(masked(self.pattern_base(), name * 8 + (y & 7), 11))
                for px in range(6):
                    c = fg if bits & (0x80 >> px) else tbg
                    if c == 0 and tp:
                        c = bg
                    x = 2 * TEXT_OFFSET_V9938 + col * 12 + px * 2
                    out[x: x + 2] = c
            return self._text_scroll(out)
        if mode == T2:
            fg, tbg = self.regs[7] >> 4, self.regs[7] & 0x0F
            # Blink (openMSX CharacterConverter::renderText2): characters
            # with their bit set in the color table use R#12 while the
            # blink state is on; those colors are never transparent.
            bfg, bbg = self.regs[12] >> 4, self.regs[12] & 0x0F
            if bfg == 0:
                bfg = bbg
            cbase = (self.regs[10] << 14) | (self.regs[3] << 6) | 0x3F
            for col in range(80):
                name = self.vram_read(masked(self.name_base(), (line // 8) * 80 + col, 12))
                bits = self.vram_read(masked(self.pattern_base(), name * 8 + (y & 7), 11))
                attr = self.vram_read(masked(cbase, (line // 8) * 10 + col // 8, 9))
                blink = self.blink_state and attr & (0x80 >> (col & 7))
                for px in range(6):
                    if blink:
                        c = bfg if bits & (0x80 >> px) else bbg
                    else:
                        c = fg if bits & (0x80 >> px) else tbg
                        if c == 0 and tp:
                            c = bg
                    out[2 * TEXT_OFFSET_V9938 + col * 6 + px] = c
            return self._text_scroll(out)

        line_fn = self._bitmap_line if mode in BITMAP_MODES else self._char_line
        if self.yjk:
            line_fn = self._yjk_line
        pix = line_fn(y)
        if self.v9958 and (self.regs[26] & 0x1F or self.regs[25] & 0x01):
            pix = self._hscroll(pix, line_fn, y)

        if mode in (G5, G6) and not self.yjk:
            pix512 = pix
        else:
            pix512 = np.repeat(pix, 2)
        pix512 = self._shift_low(pix512, out)
        if tp and (mode != G7 or self.yjk):
            # Background color 0 shows the border (G7 has no transparency).
            pix512 = np.where(pix512 == 0, out, pix512)

        # Sprites over the background: a sprite pixel is never transparent
        # once drawn (in G5 each half takes 2 bits of the sprite color).
        line_out = self._left_border(self._draw_sprites(pix512.astype(np.uint16), line_sprites), out)
        if self.yjk:
            # Everything as 15-bit RGB (YJK_RGB | r << 10 | g << 5 | b).
            line_out = np.array([c if c & YJK_RGB else self.pal5(c) for c in line_out], dtype=np.uint16)
        return line_out

    def pal5(self, idx):
        """A palette color as a YJK frame code: the 3-bit levels at 5 bits."""
        r, g, b = self.palette[idx]
        m = lambda c: (c << 2) | (c >> 1)
        return YJK_RGB | (m(r) << 10) | (m(g) << 5) | m(b)

    def _yjk_line(self, y, even=False):
        """V9958 YJK (openMSX BitmapConverter::renderYJK / renderYAE): groups
        of 4 pixels share J and K, each has its own Y; with YAE a pixel with
        bit 3 set is the palette color (byte >> 4).  G4 / G5 with YJK show
        palette color 15."""
        if self.mode in (G4, G5):
            return np.full(256, 15, dtype=np.uint16)
        base = self.name_base()
        if even:
            base &= ~(0x100 << 7)
        vline = (base >> 7) & (0x100 | y)
        data = [self.vram[self.phys(planar(vline * 256 + i))] for i in range(256)]
        yae = self.regs[25] & 0x10
        out = np.zeros(256, dtype=np.uint16)
        for g in range(0, 256, 4):
            p = data[g: g + 4]
            j = (p[2] & 7) + ((p[3] & 3) << 3) - ((p[3] & 4) << 3)
            k = (p[0] & 7) + ((p[1] & 3) << 3) - ((p[1] & 4) << 3)
            for n in range(4):
                if yae and p[n] & 0x08:
                    out[g + n] = p[n] >> 4
                else:
                    yy = p[n] >> 3
                    r = min(max(yy + j, 0), 31)
                    gg = min(max(yy + k, 0), 31)
                    b = min(max((5 * yy - 2 * j - k + 2) // 4, 0), 31)
                    out[g + n] = YJK_RGB | (r << 10) | (gg << 5) | b
        return out

    def _text_scroll(self, out):
        """V9958 in the text modes: only R#27 moves the text (no R#26); with
        MSK the first 8 pixels of the text area are border."""
        if not self.v9958:
            return out
        border = self.border_line()
        low = self.regs[27] & 7
        if low:
            out = self._shift_low(out, border)
            # The text area still ends 240 pixels after its start.
            end = 2 * (TEXT_OFFSET_V9938 + 240) if self.mode == T1 else 2 * TEXT_OFFSET_V9938 + 480
            out[end:] = border[end:]
        if self.regs[25] & 0x02:
            out = out.copy()
            n = 2 * (TEXT_OFFSET_V9938 + 8)
            out[:n] = border[:n]
        return out

    def _shift_low(self, line, border):
        """V9958 R#27: the background moves R#27 pixels to the right; those
        pixels show the border (openMSX getLeftBackground)."""
        low = self.regs[27] & 7 if self.v9958 else 0
        if not low:
            return line
        out = np.empty_like(line)
        out[2 * low:] = line[: len(line) - 2 * low]
        out[: 2 * low] = border[: 2 * low]
        return out

    def _hscroll(self, pix, line_fn, y):
        """V9958 R#26: the background moves 8 * R#26 pixels to the left; with
        R#25 SP2 and an odd page in R#2 it continues into the other page of
        the pair, starting with the page of R#26 bit 5 (openMSX
        SDLRasterizer::drawDisplay, CharacterConverter::getNamePtr)."""
        w = len(pix)
        hs = 8 * (w // 256) * (self.regs[26] & 0x1F)
        multi = self.regs[25] & 0x01 and self.regs[2] & 0x20
        if multi:
            rows = [line_fn(y, even=True), pix]
            p1 = (self.regs[26] >> 5) & 1
        else:
            rows, p1 = [pix, pix], 0
        out = np.empty_like(pix)
        for x in range(w):
            src = x + hs
            out[x] = rows[p1][src] if src < w else rows[p1 ^ 1][src - w]
        return out

    def _left_border(self, out, border):
        """V9958: the R#27 pixels at the left (and with R#25 MSK the first 8)
        are border, sprites included (openMSX PixelRenderer: the display
        area starts at getLeftBackground / getLeftBorder)."""
        if not self.v9958:
            return out
        n = max(2 * (self.regs[27] & 7), 16 if self.regs[25] & 0x02 else 0)
        if n:
            out = out.copy()
            out[:n] = border[:n]
        return out

    def _char_line(self, y, even=False):
        mode = self.mode
        pix = np.zeros(256, dtype=np.uint16)
        row, l = y // 8, y & 7
        nbase = self.name_base() & ~0x8000 if even else self.name_base()
        for col in range(32):
            name = self.vram_read(masked(nbase, row * 32 + col, 10))
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

    def _bitmap_line(self, y, even=False):
        """One display line of G4-G7: 256 (G4, G7) or 512 (G5, G6) codes."""
        mode = self.mode
        # Even / odd page (openMSX VDP::getEvenOddMask): line bit 8 (the odd
        # page) is cleared when R#9 EO alternation is on in an even field
        # (S#2 EO = 0), or while the R#13 blink state is on.
        eo_mask = ((~self.regs[9] & 4) << 6 | (self.status[2] & 2) << 7) & ((not self.blink_state) << 8)
        base = self.name_base() & (~(0x100 << 7) | (eo_mask << 7))
        if even:
            base &= ~(0x100 << 7)            # V9958 multi page: the even page
        if mode in PLANAR_MODES:
            vline = (base >> 7) & (0x100 | y)
            data = [self.vram[self.phys(planar(vline * 256 + i))] for i in range(256)]
        else:
            vline = (base >> 7) & (0x300 | y)
            data = [self.vram[self.phys(vline * 128 + i)] for i in range(128)]
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
            if mode == G7 and not self.yjk:
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
