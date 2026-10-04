"""V9938 mode display: 192 / 212 lines, R#23 scroll, R#18 adjust and the
line interrupt, against v9938_model."""

import cocotb
import numpy as np
from cocotb.triggers import FallingEdge, RisingEdge, Timer
from cocotb.utils import get_sim_time

import v9938_model as vm
import v9938_scenes
from f18a_driver import ACTIVE_X, BANNER_H, BANNER_W, CAPTURE_DIR, F18A, GEOM15, PIX_NS, W15, X15_FIRST, save_png

LINE_NS = 684 * PIX_NS


def expand(c3):
    return (c3 << 1) | (c3 >> 2)


def model_frame(model, standard="ntsc"):
    """The picture (border + active area) the core shows for the model, as
    4-bit RGB: (lines with picture, 568, 3)."""
    top, bottom, _ = GEOM15[standard]
    if model.lines == 212:
        top, bottom = top - 10, bottom - 10
    rgb = np.array([[expand(c) for c in p] for p in model.palette], dtype=np.uint8)
    frame = np.empty((top + model.lines + bottom, W15, 3), dtype=np.uint8)
    frame[:, :] = rgb[model.border()]
    x0 = ACTIVE_X - X15_FIRST
    frame[top: top + model.lines, x0: x0 + 512] = rgb[model.render()]
    return frame


def mask_banner(frame):
    frame = frame.copy()
    frame[:BANNER_H, :BANNER_W - X15_FIRST] = 0
    return frame


async def load(f, vram, regs, pal, vram_size=0x8000):
    """Load a scene in V9938 mode: palette (9Ah), VRAM, registers."""
    await f.reset(v9938=True)
    await f.set_reg(1, 0x00)                   # blank while loading
    await f.set_reg(16, 0)
    for r, g, b in pal:
        await f.write_port(2, (r << 4) | b)
        await f.write_port(2, g)
    # 16 KB blocks: the address only carries into R14 in the V9938 modes.
    for block in range(0, vram_size, 0x4000):
        await f.set_reg(14, block >> 14)
        await f.write_vram(0, vram[block: block + 0x4000])
    await f.set_reg(14, 0)
    for r, v in enumerate(regs):
        if r not in (14, 15, 16, 17):
            await f.set_reg(r, v)
    await f.read_status()


async def render_scene(dut, name):
    vram, regs, pal = v9938_scenes.DISPLAY_SCENES[name]()
    f = F18A(dut)
    await load(f, vram, regs, pal)
    await FallingEdge(dut.vsync_n_o)
    got = mask_banner(await f.capture_frame())
    model = vm.V9938(vram, regs, pal)
    exp = mask_banner(model_frame(model))
    save_png(got, CAPTURE_DIR / f"v9938_{name}.png")
    save_png(exp, CAPTURE_DIR / f"v9938_{name}_model.png")
    assert got.shape == exp.shape, f"{name}: frame {got.shape}, expected {exp.shape}"
    diff = np.any(got != exp, axis=2)
    if diff.any():
        ys, xs = np.nonzero(diff)
        raise AssertionError(f"{name}: {len(ys)} samples differ, lines {ys.min()}..{ys.max()}, "
                             f"x {xs.min()}..{xs.max()}; first ({xs[0]},{ys[0]}) RTL {got[ys[0], xs[0]]} "
                             f"model {exp[ys[0], xs[0]]}")


def make_render(name):
    async def test(dut):
        await render_scene(dut, name)
    test.__name__ = test.__qualname__ = f"render_{name}"
    return cocotb.test()(test)


for _name in v9938_scenes.DISPLAY_SCENES:
    globals()[f"render_{_name}"] = make_render(_name)


async def first_picture_line(dut):
    """Time of the first picture line after a vsync."""
    await FallingEdge(dut.vsync_n_o)
    await FallingEdge(dut.blank_o)
    return get_sim_time("ns")


@cocotb.test()
async def line_interrupt(dut):
    """IE1 + R19: INT at the end of display line R19 - R23, FH in S#1."""
    f = F18A(dut)
    await f.reset(v9938=True)
    await f.set_reg(1, 0x40)                   # display on, no frame interrupt
    for r19, r23 in ((100, 0), (100, 20), (5, 250)):
        await f.set_reg(19, r19)
        await f.set_reg(23, r23)
        await f.set_reg(0, 0x10)               # IE1
        await f.set_reg(15, 1)
        t_pic = await first_picture_line(dut)
        await f.read_status()                  # clear FH of the previous frame
        await FallingEdge(dut.int_n_o)
        line = (get_sim_time("ns") - t_pic) / LINE_NS
        # Picture line of the end of active line L: top border (27) + L + 1.
        exp = 27 + ((r19 - r23) & 0xFF) + 1
        assert abs(line - exp) < 0.5, f"R19={r19} R23={r23}: INT at picture line {line:.2f}, expected {exp}"
        s1 = await f.read_status()
        assert s1 & 0x01, f"FH not set: S#1 {s1:02x}"
        await Timer(1, "us")
        assert int(dut.int_n_o.value) == 1, "INT not cleared by reading S#1"
        assert not (await f.read_status()) & 0x01, "FH not cleared"
        await f.set_reg(0, 0x00)
        await f.set_reg(15, 0)


async def picture_top(dut):
    """Time of the first picture line of a frame: the first pixel after a
    blanking of more than two lines (the syncs may overlap the border)."""
    while True:
        await RisingEdge(dut.blank_o)
        t = get_sim_time("ns")
        await FallingEdge(dut.blank_o)
        if get_sim_time("ns") - t > 2 * LINE_NS:
            return get_sim_time("ns")


@cocotb.test()
async def set_adjust(dut):
    """R18 moves the picture against the syncs: hadj pixels left, vadj up."""
    f = F18A(dut)
    await f.reset(v9938=True)
    await f.set_reg(1, 0x40)
    results = {}
    for r18 in (0x00, 0x03, 0x0D, 0x50, 0xB0):
        await f.set_reg(18, r18)
        await FallingEdge(dut.vsync_n_o)
        t_top = await picture_top(dut)
        # Last vsync before the top of the picture.
        await FallingEdge(dut.vsync_n_o)
        t_vs = get_sim_time("ns")
        t_top2 = await picture_top(dut)
        dv = (t_top2 - t_vs) / LINE_NS
        # A line in the middle of the picture.
        await Timer(100 * LINE_NS, "ns")
        await FallingEdge(dut.hsync_n_o)
        t_hs = get_sim_time("ns")
        await FallingEdge(dut.blank_o)
        dh = (get_sim_time("ns") - t_hs) / PIX_NS
        results[r18] = (dh, dv)
        dut._log.info("R18=%02x: hsync to picture %.1f px, vsync to picture %.2f lines", r18, dh, dv)
    h0, v0 = results[0x00]
    for r18, (dh, dv) in results.items():
        hadj = (r18 & 0x0F) - (16 if r18 & 0x08 else 0)
        vadj = (r18 >> 4) - (16 if r18 & 0x80 else 0)
        # The picture moves hadj pixels (2 raster pixels each) left: closer
        # to the previous hsync; vadj lines up: closer to the vsync.
        assert abs((h0 - dh) - 2 * hadj) < 0.5, f"R18={r18:02x}: hsync to picture {dh:.1f} vs {h0:.1f}"
        # (modulo a frame: the vsync may move to the other side of the top)
        err = ((v0 - dv) - vadj) % GEOM15["ntsc"][2]
        assert min(err, GEOM15["ntsc"][2] - err) < 0.5, f"R18={r18:02x}: vsync to picture {dv:.2f} vs {v0:.2f}"


async def sprite_status(dut, entries, mode2=True):
    """Show sprites over a blank G3 / G1 screen for a frame; return S#0."""
    vram = bytearray(vm.VRAM_SIZE)
    vram[0x7800:0x7800 + 32] = b"\xFF" * 32           # pattern 0: solid 16x16
    if mode2:
        regs = [0x04, 0x42, 0x06, 0xFF, 0x03, 0xEF, 0x0F, 0x04, 0x08, 0, 0, 0]
        v9938_scenes.sprite_table(vram, 0x7600, 0x7400, entries)
    else:
        regs = [0x00, 0x42, 0x06, 0x80, 0x00, 0xEC, 0x0F, 0x04, 0x08, 0, 0, 0]
        for i, (y, x, name, colors) in enumerate(entries):
            vram[0x7600 + i * 4: 0x7600 + i * 4 + 4] = bytes((y, x, name, colors[0]))
    f = F18A(dut)
    await load(f, vram, regs, vm.DEFAULT_PALETTE)
    await FallingEdge(dut.vsync_n_o)
    await f.read_status()
    await FallingEdge(dut.vsync_n_o)
    return await f.read_status()


@cocotb.test()
async def sprite2_status(dut):
    """Sprite mode 2: 9th sprite in S#0, collisions only between CC = 0 and
    IC = 0 sprites of a color other than 0."""
    def entries(colors_b, nine=False):
        e = [(50, 100, 0, [0x0F]), (50, 108, 0, colors_b)]
        if nine:
            e += [(120, 20 * k, 0, [0x02]) for k in range(9)]
        return e + [(216, 0, 0, [0])]
    cases = [
        ([0x05], False, True),            # plain overlap: collision
        ([0x25], False, False),           # IC: no collision
        ([0x45], False, False),           # CC: no collision
        ([0x00], False, False),           # color 0: no collision
    ]
    for colors_b, nine, collide in cases:
        s0 = await sprite_status(dut, entries(colors_b, nine))
        assert bool(s0 & 0x20) == collide, f"color {colors_b[0]:02x}: S#0 {s0:02x}, collision expected {collide}"
    # Nine sprites on a line: 5S and the number of the 9th (sprite 10).
    s0 = await sprite_status(dut, entries([0x25], nine=True))
    assert s0 & 0x40 and s0 & 0x1F == 10, f"9th sprite: S#0 {s0:02x}, expected 5S and 10"
