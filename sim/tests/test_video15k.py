"""15KHz RGB output, NTSC and PAL: timing, composite sync and image."""

import cocotb
import numpy as np
from cocotb.triggers import FallingEdge, RisingEdge, Timer
from cocotb.utils import get_sim_time

import scenes
from f18a_driver import BANNER_H, BANNER_W, CAPTURE_DIR, F18A, GEOM15, X15_FIRST, save_png
from tms9918_model import render_frame15

LINE_NS = 2 * 796 * 40          # one 15KHz line = two 796-pixel raster lines
HALFPX_NS = LINE_NS / 684       # 342 9918A pixels per line
HSYNC_NS = 52 * HALFPX_NS       # 26 pixels
VSYNC_LINES = 3


async def edge_time(trigger):
    await trigger
    return get_sim_time("ns")


async def start_15k(dut, standard):
    f = F18A(dut)
    await f.reset(video_15k=True, pal=standard == "pal")
    # The raster switches to the 15KHz geometry at the end of a frame.
    await FallingEdge(dut.vsync_o)
    await FallingEdge(dut.vsync_o)
    return f


def mask_banner15(frame):
    frame = frame.copy()
    frame[:(BANNER_H + 1) // 2, :BANNER_W - X15_FIRST] = 0
    return frame


async def check_sync_timing(dut, standard):
    lines = GEOM15[standard][2]
    await start_15k(dut, standard)

    await FallingEdge(dut.vsync15_n_o)
    t0 = await edge_time(FallingEdge(dut.hsync15_n_o))
    t1 = await edge_time(RisingEdge(dut.hsync15_n_o))
    t2 = await edge_time(FallingEdge(dut.hsync15_n_o))
    assert t2 - t0 == LINE_NS, f"15KHz line {t2 - t0}ns"
    assert abs((t1 - t0) - HSYNC_NS) <= 10, f"hsync width {t1 - t0}ns, expected {HSYNC_NS:.0f}"

    f0 = await edge_time(FallingEdge(dut.vsync15_n_o))
    f1 = await edge_time(RisingEdge(dut.vsync15_n_o))
    f2 = await edge_time(FallingEdge(dut.vsync15_n_o))
    assert f1 - f0 == VSYNC_LINES * LINE_NS, f"vsync width {f1 - f0}ns"
    assert f2 - f0 == lines * LINE_NS, f"15KHz frame {f2 - f0}ns, expected {lines} lines"
    dut._log.info("%s: line %.3fus (%.3fKHz), %d lines, frame %.3fms (%.3fHz)", standard,
                  LINE_NS / 1e3, 1e6 / LINE_NS, lines, (f2 - f0) / 1e6, 1e9 / (f2 - f0))


async def check_interrupt_rate(dut, standard):
    lines = GEOM15[standard][2]
    f = await start_15k(dut, standard)
    await f.set_reg(1, 0x60)
    await f.read_status()
    t0 = await edge_time(FallingEdge(dut.int_n_o))
    await f.read_status()
    t1 = await edge_time(FallingEdge(dut.int_n_o))
    assert abs((t1 - t0) - lines * LINE_NS) < 100, f"interrupt period {t1 - t0}ns"


async def run_scene15(dut, name, standard):
    vram, regs = scenes.SCENES[name]()
    f = await start_15k(dut, standard)
    await f.set_reg(1, 0x80)
    await f.load_vram(vram)
    await f.set_regs(regs)
    await FallingEdge(dut.vsync_o)
    got = mask_banner15(await f.capture_frame15())
    exp = mask_banner15(render_frame15(vram, regs, standard)[0])
    save_png(got, CAPTURE_DIR / f"{name}_{standard}.png")
    save_png(exp, CAPTURE_DIR / f"{name}_{standard}_model.png")
    assert got.shape == exp.shape, f"{name}: frame {got.shape}, expected {exp.shape}"
    diff = np.any(got != exp, axis=2)
    if diff.any():
        ys, xs = np.nonzero(diff)
        raise AssertionError(f"{name}: {len(ys)} pixels differ, bbox x={xs.min()}..{xs.max()} "
                             f"y={ys.min()}..{ys.max()}; first ({xs[0]},{ys[0]}) got {got[ys[0], xs[0]]} "
                             f"expected {exp[ys[0], xs[0]]}")


@cocotb.test()
async def ntsc_sync_timing(dut):
    await check_sync_timing(dut, "ntsc")


@cocotb.test()
async def pal_sync_timing(dut):
    await check_sync_timing(dut, "pal")


@cocotb.test()
async def composite_sync(dut):
    """CSYNC follows HSYNC, inverted during the vertical sync lines."""
    await start_15k(dut, "ntsc")
    await FallingEdge(dut.vsync15_n_o)
    for in_vsync in (True, False):
        if not in_vsync:
            await RisingEdge(dut.vsync15_n_o)
            await Timer(LINE_NS, "ns")
        for _ in range(200):
            await Timer(397, "ns")
            h = int(dut.hsync15_n_o.value)
            c = int(dut.csync15_n_o.value)
            assert c == (1 - h if in_vsync else h), f"csync {c} hsync {h} vsync={in_vsync}"


@cocotb.test()
async def ntsc_interrupt_rate(dut):
    await check_interrupt_rate(dut, "ntsc")


@cocotb.test()
async def pal_interrupt_rate(dut):
    await check_interrupt_rate(dut, "pal")


@cocotb.test()
async def ntsc_render_graphics2(dut):
    await run_scene15(dut, "graphics2", "ntsc")


@cocotb.test()
async def ntsc_render_text1(dut):
    await run_scene15(dut, "text1", "ntsc")


@cocotb.test()
async def pal_render_graphics1(dut):
    await run_scene15(dut, "graphics1", "pal")


@cocotb.test()
async def pal_render_text1(dut):
    await run_scene15(dut, "text1", "pal")
