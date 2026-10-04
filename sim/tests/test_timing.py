"""VGA timing of the 640x480@60 output and the frame interrupt."""

import cocotb
from cocotb.triggers import FallingEdge, RisingEdge, Timer
from cocotb.utils import get_sim_time

from f18a_driver import F18A

PIXEL_NS = 40           # 25MHz pixel clock
H_TOTAL = 794           # pixels per line (HMAX = 793)
H_SYNC = 96
V_TOTAL = 525
V_SYNC = 2


async def edge_time(trigger):
    await trigger
    return get_sim_time("ns")


@cocotb.test()
async def hsync_vsync(dut):
    f = F18A(dut)
    await f.reset()
    v0 = await edge_time(FallingEdge(dut.vsync_o))

    t0 = await edge_time(FallingEdge(dut.hsync_o))
    t1 = await edge_time(RisingEdge(dut.hsync_o))
    t2 = await edge_time(FallingEdge(dut.hsync_o))
    assert t1 - t0 == H_SYNC * PIXEL_NS, f"hsync width {t1 - t0}ns"
    assert t2 - t0 == H_TOTAL * PIXEL_NS, f"line period {t2 - t0}ns"

    v1 = await edge_time(RisingEdge(dut.vsync_o))
    v2 = await edge_time(FallingEdge(dut.vsync_o))
    line = H_TOTAL * PIXEL_NS
    assert v1 - v0 == V_SYNC * line, f"vsync width {v1 - v0}ns"
    assert v2 - v0 == V_TOTAL * line, f"frame period {v2 - v0}ns"
    dut._log.info("line %.3fus, frame %.3fms (%.3fHz)", line / 1e3, (v2 - v0) / 1e6, 1e9 / (v2 - v0))


@cocotb.test()
async def active_area(dut):
    """Count active (non blanked) pixels and lines in one frame."""
    f = F18A(dut)
    await f.reset()
    await FallingEdge(dut.vsync_o)
    lines = 0
    while True:
        await FallingEdge(dut.blank_o)
        pixels = 0
        while True:
            await RisingEdge(dut.clk_25m0_o)
            if int(dut.blank_o.value):
                break
            pixels += 1
        assert pixels == 640, f"line {lines}: {pixels} active pixels"
        lines += 1
        if lines == 480:
            break
    # No more active lines before the next vsync.
    await FallingEdge(dut.vsync_o)


@cocotb.test()
async def frame_interrupt(dut):
    """INT goes low once per frame when IE is set and clears on a status read."""
    f = F18A(dut)
    await f.reset()
    await f.set_reg(1, 0x60)              # BL + IE
    await f.read_status()                 # clear anything pending
    await FallingEdge(dut.int_n_o)
    t0 = get_sim_time("ns")
    status = await f.read_status()
    assert status & 0x80, f"status F flag not set: {status:02x}"
    await Timer(1, "us")
    assert int(dut.int_n_o.value) == 1, "INT not cleared by status read"
    status = await f.read_status()
    assert not status & 0x80, f"F flag not cleared: {status:02x}"
    await FallingEdge(dut.int_n_o)
    period = get_sim_time("ns") - t0
    assert abs(period - H_TOTAL * V_TOTAL * PIXEL_NS) < 2000, f"interrupt period {period}ns"

    # IE = 0: the flag is still set but INT stays high.
    await f.read_status()
    await f.set_reg(1, 0x40)
    await FallingEdge(dut.vsync_o)
    await FallingEdge(dut.vsync_o)
    assert int(dut.int_n_o.value) == 1, "INT asserted with IE = 0"
    status = await f.read_status()
    assert status & 0x80, "F flag must be set even with IE = 0"
