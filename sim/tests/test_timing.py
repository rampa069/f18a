"""15KHz video timing (NTSC and PAL), composite sync and the frame interrupt."""

import cocotb
from cocotb.triggers import FallingEdge, RisingEdge, Timer
from cocotb.utils import get_sim_time

from f18a_driver import F18A, GEOM15, PIX_NS, W15

LINE_NS = 684 * PIX_NS          # 342 VDP pixels per line
HSYNC_NS = 52 * PIX_NS          # 26 VDP pixels
VSYNC_LINES = 3


async def edge_time(trigger):
    await trigger
    return get_sim_time("ns")


async def start(dut, standard):
    f = F18A(dut)
    await f.reset(pal=standard == "pal")
    await FallingEdge(dut.vsync_n_o)
    return f


async def check_sync_timing(dut, standard):
    lines = GEOM15[standard][2]
    await start(dut, standard)

    t0 = await edge_time(FallingEdge(dut.hsync_n_o))
    t1 = await edge_time(RisingEdge(dut.hsync_n_o))
    t2 = await edge_time(FallingEdge(dut.hsync_n_o))
    assert abs((t2 - t0) - LINE_NS) < 0.01, f"line {t2 - t0}ns"
    assert abs((t1 - t0) - HSYNC_NS) < 0.01, f"hsync width {t1 - t0}ns"

    f0 = await edge_time(FallingEdge(dut.vsync_n_o))
    f1 = await edge_time(RisingEdge(dut.vsync_n_o))
    f2 = await edge_time(FallingEdge(dut.vsync_n_o))
    assert abs((f1 - f0) - VSYNC_LINES * LINE_NS) < 0.01, f"vsync width {f1 - f0}ns"
    assert abs((f2 - f0) - lines * LINE_NS) < 0.01, f"frame {f2 - f0}ns, expected {lines} lines"
    dut._log.info("%s: line %.3fus (%.3fKHz), %d lines, frame %.3fms (%.3fHz)", standard,
                  LINE_NS / 1e3, 1e6 / LINE_NS, lines, (f2 - f0) / 1e6, 1e9 / (f2 - f0))


async def check_picture_area(dut, standard):
    """Count picture (non blanked) pixels and lines in one frame."""
    top, bottom, _ = GEOM15[standard]
    await start(dut, standard)
    await FallingEdge(dut.vsync_n_o)
    lines = 0
    while True:
        await FallingEdge(dut.blank_o)
        if int(dut.vsync_n_o.value) == 0:
            break
        t0 = get_sim_time("ns")
        await RisingEdge(dut.blank_o)
        pixels = round((get_sim_time("ns") - t0) / PIX_NS)
        assert pixels == W15, f"line {lines}: {pixels} picture pixels"
        lines += 1
        if lines == top + 192 + bottom:
            break
    t = get_sim_time("ns")
    await FallingEdge(dut.vsync_n_o)
    # No more picture lines before the next vsync.
    assert get_sim_time("ns") - t > 3 * LINE_NS


async def check_interrupt(dut, standard):
    lines = GEOM15[standard][2]
    f = await start(dut, standard)
    await f.set_reg(1, 0x60)              # BL + IE
    await f.read_status()                 # clear anything pending
    t0 = await edge_time(FallingEdge(dut.int_n_o))
    status = await f.read_status()
    assert status & 0x80, f"status F flag not set: {status:02x}"
    await Timer(1, "us")
    assert int(dut.int_n_o.value) == 1, "INT not cleared by status read"
    status = await f.read_status()
    assert not status & 0x80, f"F flag not cleared: {status:02x}"
    t1 = await edge_time(FallingEdge(dut.int_n_o))
    assert abs((t1 - t0) - lines * LINE_NS) < LINE_NS / 10, f"interrupt period {t1 - t0}ns"

    # IE = 0: the flag is still set but INT stays high.
    await f.read_status()
    await f.set_reg(1, 0x40)
    await FallingEdge(dut.vsync_n_o)
    await FallingEdge(dut.vsync_n_o)
    assert int(dut.int_n_o.value) == 1, "INT asserted with IE = 0"
    status = await f.read_status()
    assert status & 0x80, "F flag must be set even with IE = 0"


@cocotb.test()
async def ntsc_sync_timing(dut):
    await check_sync_timing(dut, "ntsc")


@cocotb.test()
async def pal_sync_timing(dut):
    await check_sync_timing(dut, "pal")


@cocotb.test()
async def ntsc_picture_area(dut):
    await check_picture_area(dut, "ntsc")


@cocotb.test()
async def pal_picture_area(dut):
    await check_picture_area(dut, "pal")


@cocotb.test()
async def composite_sync(dut):
    """CSYNC follows HSYNC, inverted during the vertical sync lines."""
    await start(dut, "ntsc")
    await FallingEdge(dut.vsync_n_o)
    for in_vsync in (True, False):
        if not in_vsync:
            await RisingEdge(dut.vsync_n_o)
            await Timer(LINE_NS, "ns")
        for _ in range(200):
            await Timer(397, "ns")
            h = int(dut.hsync_n_o.value)
            c = int(dut.csync_n_o.value)
            assert c == (1 - h if in_vsync else h), f"csync {c} hsync {h} vsync={in_vsync}"


@cocotb.test()
async def ntsc_frame_interrupt(dut):
    await check_interrupt(dut, "ntsc")


@cocotb.test()
async def pal_frame_interrupt(dut):
    await check_interrupt(dut, "pal")
