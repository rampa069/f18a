"""Reference model of the TMS9918A display, used to check the F18A output.

Renders a VRAM image and the eight 9918A registers to the 640x480 VGA frame
the F18A produces (pixel doubled 256x192 area, backdrop colored border), and
computes the sprite status flags.  Only the original 9918A behavior is
modeled; F18A extensions are expected to be disabled (locked VDP).
"""

import numpy as np

from f18a_driver import ACTIVE_X, ACTIVE_Y, TEXT_X, VGA_H, VGA_W

# Default F18A palette 0 (f18a_color.vhd), 4-bit R, G, B.
PALETTE = [
    0x000, 0x000, 0x2C3, 0x5D6, 0x54F, 0x76F, 0xD54, 0x4EF,
    0xF54, 0xF76, 0xDC3, 0xED6, 0x2B2, 0xC5C, 0xCCC, 0xFFF,
]
PALETTE_RGB = np.array([[(c >> 8) & 15, (c >> 4) & 15, c & 15] for c in PALETTE], dtype=np.uint8)

MODE_G1, MODE_G2, MODE_MC, MODE_TEXT = "G1", "G2", "MC", "TEXT"


def mode_of(regs):
    m1 = regs[1] & 0x10
    m2 = regs[1] & 0x08
    m3 = regs[0] & 0x02
    if m1:
        return MODE_TEXT
    if m2:
        return MODE_MC
    if m3:
        return MODE_G2
    return MODE_G1


class Status:
    def __init__(self):
        self.fifth = False
        self.fifth_num = 0
        self.collision = False

    def value(self, frame_flag=False):
        v = 0x80 if frame_flag else 0
        if self.fifth:
            v |= 0x40
        if self.collision:
            v |= 0x20
        return v | (self.fifth_num & 0x1F)


def render_tiles(vram, regs):
    """Return a 192x256 array of color indices (0 = transparent) for the
    pattern layer, or 192x240 in text mode."""
    mode = mode_of(regs)
    nt = (regs[2] & 0x0F) << 10
    if mode == MODE_TEXT:
        pg = (regs[4] & 0x07) << 11
        fg, bg = regs[7] >> 4, regs[7] & 0x0F
        out = np.zeros((192, 240), dtype=np.uint8)
        for y in range(192):
            for col in range(40):
                name = vram[nt + (y // 8) * 40 + col]
                bits = vram[pg + name * 8 + (y & 7)]
                for px in range(6):
                    out[y, col * 6 + px] = fg if bits & (0x80 >> px) else bg
        return out

    out = np.zeros((192, 256), dtype=np.uint8)
    for y in range(192):
        row, line = y // 8, y & 7
        for col in range(32):
            name = vram[nt + row * 32 + col]
            if mode == MODE_G1:
                pg = (regs[4] & 0x07) << 11
                ct = regs[3] << 6
                bits = vram[pg + name * 8 + line]
                color = vram[ct + (name >> 3)]
                fg, bg = color >> 4, color & 0x0F
            elif mode == MODE_G2:
                third = (row // 8) << 8
                pmask = ((regs[4] & 0x03) << 8) | 0xFF
                pg = (regs[4] & 0x04) << 11
                pidx = (third | name) & pmask
                bits = vram[pg + pidx * 8 + line]
                cmask = ((regs[3] & 0x7F) << 3) | 0x07
                ct = (regs[3] & 0x80) << 6
                cidx = (third | name) & cmask
                color = vram[ct + cidx * 8 + line]
                fg, bg = color >> 4, color & 0x0F
            else:  # MODE_MC
                pg = (regs[4] & 0x07) << 11
                color = vram[pg + name * 8 + (row & 3) * 2 + (line >> 2)]
                fg, bg = color >> 4, color & 0x0F
                bits = 0xF0
            for px in range(8):
                out[y, col * 8 + px] = fg if bits & (0x80 >> px) else bg
    return out


def render_sprites(vram, regs, max_per_line=4):
    """Return (192x256 color indices, Status) for the sprite layer."""
    out = np.zeros((192, 256), dtype=np.uint8)
    hit = np.zeros((192, 256), dtype=bool)
    status = Status()
    if mode_of(regs) == MODE_TEXT:
        return out, status

    sat = (regs[5] & 0x7F) << 7
    spg = (regs[6] & 0x07) << 11
    size16 = bool(regs[1] & 0x02)
    mag = bool(regs[1] & 0x01)
    pix = 16 if size16 else 8
    span = pix * (2 if mag else 1)

    sprites = []
    for n in range(32):
        y, x, name, color = vram[sat + n * 4: sat + n * 4 + 4]
        if y == 0xD0:
            break
        sprites.append((n, y, x, name, color))

    fifth_found = False
    for line in range(192):
        on_line = []
        for n, y, x, name, color in sprites:
            top = (y + 1) & 0xFF
            if top > 0xE0:          # y in 0xE1..0xFF wraps to a negative position
                top -= 256
            if top <= line < top + span:
                on_line.append((n, top, x, name, color))
        if len(on_line) > max_per_line:
            if not fifth_found:
                fifth_found = True
                status.fifth = True
                status.fifth_num = on_line[max_per_line][0]
            on_line = on_line[:max_per_line]

        drawn = np.zeros(256, dtype=bool)
        for n, top, x, name, color in on_line:
            if color & 0x80:
                x -= 32
            row = (line - top) // (2 if mag else 1)
            if size16:
                base = spg + (name & 0xFC) * 8
                bits = (vram[base + row] << 8) | vram[base + 16 + row]
            else:
                bits = vram[spg + name * 8 + row] << 8
            for p in range(span):
                sx = x + p
                if not 0 <= sx < 256:
                    continue
                if not bits & (0x8000 >> (p // (2 if mag else 1))):
                    continue
                if drawn[sx]:
                    status.collision = True
                else:
                    drawn[sx] = True
                    if color & 0x0F:
                        out[line, sx] = color & 0x0F
                        hit[line, sx] = True
    if not fifth_found and sprites:
        # Without a 5th sprite the number field holds the last sprite processed.
        status.fifth_num = len(sprites) if len(sprites) < 32 else 31
    return out, status


def render_frame(vram, regs, max_per_line=4):
    """Render the full 640x480 4-bit RGB VGA frame."""
    vram = bytes(vram)
    backdrop = regs[7] & 0x0F
    frame = np.empty((VGA_H, VGA_W, 3), dtype=np.uint8)
    frame[:, :] = PALETTE_RGB[backdrop]
    if not regs[1] & 0x40:              # BL = 0, display blanked
        return frame, Status()

    tiles = render_tiles(vram, regs)
    sprites, status = render_sprites(vram, regs, max_per_line)
    if mode_of(regs) == MODE_TEXT:
        img = tiles
        x0 = TEXT_X
    else:
        img = np.where(sprites != 0, sprites, tiles)
        x0 = ACTIVE_X
    img = np.where(img == 0, backdrop, img)
    big = np.repeat(np.repeat(img, 2, axis=0), 2, axis=1)
    frame[ACTIVE_Y: ACTIVE_Y + big.shape[0], x0: x0 + big.shape[1]] = PALETTE_RGB[big]
    return frame, status
