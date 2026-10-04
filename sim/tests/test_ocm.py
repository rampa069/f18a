"""OCM-PLD VDP replacement (ocm/f18a_vdp_ocm.vhd): bus, dot clocks, video."""

import os
from pathlib import Path

import cocotb
import numpy as np
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge
from cocotb.utils import get_sim_time

import scenes
from f18a_driver import BANNER_H, BANNER_W, X15_FIRST, load_ppm, save_png
from tms9918_model import render_frame15

CAPTURE_DIR = Path(os.environ.get("F18A_CAPTURE_DIR", "."))
CLK_NS = 84 * 0.554             # CLK21M period of the testbench (21.487MHz)
LINE_CLKS = 1368                # 15KHz line, like the V9938
BUS_CLKS = 12                   # CLK21M cycles per I/O access (~560ns)


class OcmBus:
    """Drives the VDP like emsx_top: a one cycle REQ, data sampled later."""

    def __init__(self, dut):
        self.dut = dut

    async def reset(self, dispreso=0, ntsc_pal_type=0, forced_v_mode=0):
        dut = self.dut
        dut.reset_i.value = 1
        dut.req_i.value = 0
        dut.wrt_i.value = 0
        dut.adr_i.value = 0
        dut.dbo_i.value = 0
        dut.capture_en_i.value = 0
        dut.dispreso_i.value = dispreso
        dut.ntsc_pal_type_i.value = ntsc_pal_type
        dut.forced_v_mode_i.value = forced_v_mode
        await ClockCycles(dut.clk21m_o, 40)
        dut.reset_i.value = 0
        await ClockCycles(dut.clk21m_o, 40)

    async def io(self, port, wrt, value=0):
        dut = self.dut
        await RisingEdge(dut.clk21m_o)
        dut.adr_i.value = port
        dut.wrt_i.value = wrt
        dut.dbo_i.value = value
        dut.req_i.value = 1
        await RisingEdge(dut.clk21m_o)
        dut.req_i.value = 0
        await ClockCycles(dut.clk21m_o, BUS_CLKS - 1)
        await ReadOnly()
        data = dut.dbi_o.value
        assert data.is_resolvable, f"DBI unresolved: {data}"
        return int(data)

    async def out(self, port, value):
        await self.io(port, 1, value)

    async def inp(self, port):
        return await self.io(port, 0)

    async def set_reg(self, reg, value):
        await self.out(0x99, value)
        await self.out(0x99, 0x80 | reg)

    async def write_vram(self, addr, data):
        await self.out(0x99, addr & 0xFF)
        await self.out(0x99, 0x40 | ((addr >> 8) & 0x3F))
        for b in data:
            await self.out(0x98, b)

    async def read_vram(self, addr, length):
        await self.out(0x99, addr & 0xFF)
        await self.out(0x99, (addr >> 8) & 0x3F)
        return bytes([await self.inp(0x98) for _ in range(length)])

    async def capture(self):
        dut = self.dut
        # Callers often start right at a vsync edge, which the testbench sees
        # one clock later: enable the capture after it so only one frame is
        # armed.
        await ClockCycles(dut.clk21m_o, 4)
        start = int(dut.frames_o.value)
        dut.capture_en_i.value = 1
        await FallingEdge(dut.pvideovs_n_o)
        await ClockCycles(dut.clk21m_o, 2)
        dut.capture_en_i.value = 0
        while int(dut.frames_o.value) == start:
            await dut.frames_o.value_change
        return load_ppm(CAPTURE_DIR / f"ocm_{start}.ppm")


async def edge_time(trigger):
    await trigger
    return get_sim_time("ns")


def to6(frame4):
    """4-bit model colors as the wrapper's 6-bit outputs."""
    return (frame4.astype(np.uint16) << 2 | frame4 >> 2).astype(np.uint8)


def mask_banner(frame):
    frame = frame.copy()
    frame[:(BANNER_H + 1) // 2, :BANNER_W - X15_FIRST] = 0
    return frame


def diff_msg(name, got, exp):
    if got.shape != exp.shape:
        return f"{name}: frame {got.shape}, expected {exp.shape}"
    ys, xs = np.nonzero(np.any(got != exp, axis=2))
    return (f"{name}: {len(ys)} pixels differ, bbox x={xs.min()}..{xs.max()} y={ys.min()}..{ys.max()}; "
            f"first ({xs[0]},{ys[0]}) got {got[ys[0], xs[0]]} expected {exp[ys[0], xs[0]]}")


@cocotb.test()
async def dot_clocks(dut):
    """DH/DL follow the 4 phase sequence of the original VDP."""
    bus = OcmBus(dut)
    await bus.reset()
    seq = []
    for _ in range(16):
        await RisingEdge(dut.clk21m_o)
        await ReadOnly()
        seq.append((int(dut.pvideodhclk_o.value), int(dut.pvideodlclk_o.value)))
    # Rotate to the (DH, DL) = (1, 1) phase and compare with 00 > 01 > 11 > 10.
    i = seq.index((1, 1))
    assert seq[i:i + 8] == [(1, 1), (0, 1), (1, 0), (0, 0)] * 2, f"DH/DL sequence {seq}"


@cocotb.test()
async def bus_vram_and_status(dut):
    bus = OcmBus(dut)
    await bus.reset()
    data = bytes(range(0x30, 0x50))
    await bus.write_vram(0x1234, data)
    assert await bus.read_vram(0x1234, len(data)) == data
    assert await bus.inp(0x9A) == 0xFF, "port 9Ah must read FFh"
    assert int(dut.pramwe_n_o.value) == 1 and int(dut.pramoe_n_o.value) == 1, "external VRAM must be idle"

    # Frame interrupt and status F flag.
    await bus.set_reg(1, 0x60)
    await bus.inp(0x99)
    await FallingEdge(dut.int_n_o)
    assert await bus.inp(0x99) & 0x80, "F flag not set"
    await ClockCycles(dut.clk21m_o, 4)
    assert int(dut.int_n_o.value) == 1, "INT not cleared by the status read"


@cocotb.test()
async def msx2_registers_ignored(dut):
    """R#8+ writes (an MSX2 BIOS) must not be masked onto R#0-7."""
    bus = OcmBus(dut)
    await bus.reset()
    await bus.set_reg(1, 0x60)                  # display on, IE
    for reg, value in [(8, 0x08), (9, 0x00), (10, 0x00), (11, 0x00), (17, 0x00)]:
        await bus.set_reg(reg, value)           # masked would hit R0..R3, R1 = 0
    await bus.inp(0x99)
    await FallingEdge(dut.int_n_o)              # IE still set


async def frame_lines(dut):
    v0 = await edge_time(FallingEdge(dut.pvideovs_n_o))
    v1 = await edge_time(FallingEdge(dut.pvideovs_n_o))
    return round((v1 - v0) / (LINE_CLKS * CLK_NS))


@cocotb.test()
async def pal_selection(dut):
    bus = OcmBus(dut)
    # NTSC_PAL_TYPE = 0: FORCED_V_MODE selects.
    await bus.reset(ntsc_pal_type=0, forced_v_mode=1)
    await FallingEdge(dut.pvideovs_n_o)
    assert await frame_lines(dut) == 313, "FORCED_V_MODE = 1 must give PAL"
    # NTSC_PAL_TYPE = 1: R#9 bit 1 selects.
    await bus.reset(ntsc_pal_type=1, forced_v_mode=1)
    await FallingEdge(dut.pvideovs_n_o)
    assert await frame_lines(dut) == 262, "R#9 = 0 must give NTSC"
    await bus.set_reg(9, 0x02)
    await FallingEdge(dut.pvideovs_n_o)
    await FallingEdge(dut.pvideovs_n_o)
    assert await frame_lines(dut) == 313, "R#9 NT = 1 must give PAL"


@cocotb.test()
async def hsync_period(dut):
    """The 15KHz line is exactly 1368 CLK21M cycles; 31KHz is half."""
    for dispreso, clks in ((0, LINE_CLKS), (1, LINE_CLKS // 2)):
        bus = OcmBus(dut)
        await bus.reset(dispreso=dispreso)
        await FallingEdge(dut.pvideovs_n_o)
        await FallingEdge(dut.pvideohs_n_o)
        t0 = await edge_time(FallingEdge(dut.pvideohs_n_o))
        t1 = await edge_time(FallingEdge(dut.pvideohs_n_o))
        assert abs((t1 - t0) - clks * CLK_NS) < 1, f"DISPRESO={dispreso}: line {t1 - t0}ns"


async def render(dut, name, dispreso, standard="ntsc"):
    vram, regs = scenes.SCENES[name]()
    bus = OcmBus(dut)
    await bus.reset(dispreso=dispreso, forced_v_mode=1 if standard == "pal" else 0)
    await bus.set_reg(1, 0x80)
    await bus.write_vram(0, vram)
    for reg, value in enumerate(regs):
        await bus.set_reg(reg, value)
    await FallingEdge(dut.pvideovs_n_o)
    errors = int(dut.phase_err_o.value)
    got = mask_banner(await bus.capture())
    assert int(dut.phase_err_o.value) == errors, "pixels not aligned to half pixel boundaries"
    # The F18A default is 32 sprites per line in the wrapper.
    exp = render_frame15(vram, regs, standard, max_per_line=32)[0]
    if dispreso:
        exp = np.repeat(exp, 2, axis=0)
    exp = mask_banner(to6(exp)) if not dispreso else to6(exp)
    if dispreso:
        exp[:BANNER_H + 1, :BANNER_W - X15_FIRST] = 0
        got = got.copy()
        got[:BANNER_H + 1, :BANNER_W - X15_FIRST] = 0
    save_png(got >> 2, CAPTURE_DIR / f"ocm_{name}_{dispreso}.png")
    save_png(exp >> 2, CAPTURE_DIR / f"ocm_{name}_{dispreso}_model.png")
    assert got.shape == exp.shape and np.array_equal(got, exp), diff_msg(name, got, exp)


@cocotb.test()
async def render_15k(dut):
    await render(dut, "graphics2", dispreso=0)


@cocotb.test()
async def render_31k(dut):
    await render(dut, "text1", dispreso=1)
