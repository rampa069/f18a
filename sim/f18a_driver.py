"""cocotb helpers to drive the F18A host interface and capture video frames.

The host interface mimics a TMS9918A bus: MODE selects the port (0 = data /
VRAM, 1 = control / status), CSW and CSR are active-low strobes.  The F18A
synchronizes the strobes to its 100MHz clock, so the bus is driven with
nanosecond timing (asynchronous to the core clocks) like a real host.
"""

import os
from pathlib import Path

import numpy as np
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, Timer
from PIL import Image

# Video geometry of the 640x480 VGA output.
VGA_W, VGA_H = 640, 480
# The 256x192 VDP area is shown pixel doubled starting at this VGA position.
# The counters start the area at x=64 (XSTART), but the pixel pipeline delays
# the output by one VGA pixel, so it appears at x=65.
ACTIVE_X, ACTIVE_Y = 65, 48
# Text mode (240 pixels wide) is centered (XSTART2 = 80, plus the same delay).
TEXT_X = 81
# Power-on version banner in the top-left corner of the border (raster
# coordinates < XMAX/YMAX in f18a_version.vhd), shown for 384 frames.
BANNER_W, BANNER_H = 58, 14

# Bus timing (ns).  The 9918A minimum strobe is 186ns; the F18A needs at
# least two 100MHz clocks of sync, so these give plenty of margin while
# keeping the simulation fast.
T_SETUP = 30
T_STROBE = 200
T_HOLD = 30
T_GAP = 200

CAPTURE_DIR = Path(os.environ.get("F18A_CAPTURE_DIR", "."))


class F18A:
    def __init__(self, dut):
        self.dut = dut

    async def reset(self, sprite_max_4=True, scanlines=False):
        """Reset the core.  sprite_max_4 selects the real 9918A limit of
        four sprites per line (jumper USR1 off on the F18A board)."""
        dut = self.dut
        dut.reset_n_i.value = 0
        dut.mode_i.value = 0
        dut.csw_n_i.value = 1
        dut.csr_n_i.value = 1
        dut.cd_i.value = 0
        dut.sprite_max_i.value = 1 if sprite_max_4 else 0
        dut.scanlines_i.value = 1 if scanlines else 0
        dut.capture_en_i.value = 0
        await Timer(1, "us")
        dut.reset_n_i.value = 1
        await Timer(1, "us")

    # -- Raw bus cycles -----------------------------------------------------

    async def _write(self, mode, value):
        dut = self.dut
        dut.mode_i.value = mode
        dut.cd_i.value = value & 0xFF
        await Timer(T_SETUP, "ns")
        dut.csw_n_i.value = 0
        await Timer(T_STROBE, "ns")
        dut.csw_n_i.value = 1
        await Timer(T_HOLD, "ns")
        await Timer(T_GAP, "ns")

    async def _read(self, mode):
        dut = self.dut
        dut.mode_i.value = mode
        await Timer(T_SETUP, "ns")
        dut.csr_n_i.value = 0
        await Timer(T_STROBE, "ns")
        await ReadOnly()
        value = dut.cd_o.value
        if not value.is_resolvable:
            raise AssertionError(f"CD bus has unresolved value {value}")
        await Timer(1, "ns")
        dut.csr_n_i.value = 1
        await Timer(T_HOLD + T_GAP, "ns")
        return int(value)

    async def write_ctrl(self, value):
        await self._write(1, value)

    async def write_data(self, value):
        await self._write(0, value)

    async def read_status(self):
        return await self._read(1)

    async def read_data(self):
        return await self._read(0)

    # -- 9918A programming model --------------------------------------------

    async def set_reg(self, reg, value):
        await self.write_ctrl(value)
        await self.write_ctrl(0x80 | reg)

    async def set_regs(self, regs):
        for reg, value in enumerate(regs):
            await self.set_reg(reg, value)

    async def set_write_addr(self, addr):
        await self.write_ctrl(addr & 0xFF)
        await self.write_ctrl(0x40 | ((addr >> 8) & 0x3F))

    async def set_read_addr(self, addr):
        await self.write_ctrl(addr & 0xFF)
        await self.write_ctrl((addr >> 8) & 0x3F)

    async def write_vram(self, addr, data):
        await self.set_write_addr(addr)
        for b in data:
            await self.write_data(b)

    async def read_vram(self, addr, length):
        await self.set_read_addr(addr)
        return bytes([await self.read_data() for _ in range(length)])

    async def load_vram(self, vram):
        """Write a whole 16K VRAM image."""
        await self.write_vram(0, vram)

    # -- Video ---------------------------------------------------------------

    async def capture_frame(self):
        """Capture the next complete frame, returned as an (480, 640, 3)
        array of 4-bit RGB values."""
        dut = self.dut
        start = int(dut.frames_o.value)
        dut.capture_en_i.value = 1
        # Wait for the capture to be armed at the next vsync, then disarm so
        # only one frame is written.  The capture process samples the vsync
        # edge one 25MHz clock later, so keep the enable until it has.
        await FallingEdge(dut.vsync_o)
        await ClockCycles(dut.clk_25m0_o, 2)
        dut.capture_en_i.value = 0
        while int(dut.frames_o.value) == start:
            await dut.frames_o.value_change
        return load_ppm(CAPTURE_DIR / f"frame_{start}.ppm")


def load_ppm(path):
    tokens = Path(path).read_text().split()
    assert tokens[0] == "P3", f"{path}: not an ASCII PPM"
    w, h = int(tokens[1]), int(tokens[2])
    pixels = np.array(tokens[4:], dtype=np.uint8)
    assert pixels.size == w * h * 3, f"{path}: {pixels.size // 3} pixels, expected {w * h}"
    return pixels.reshape(h, w, 3)


def save_png(frame, path):
    """Save a 4-bit RGB frame as an 8-bit PNG."""
    Image.fromarray((frame * 17).astype(np.uint8)).save(path)


def load_png(path):
    return (np.asarray(Image.open(path).convert("RGB")) // 17).astype(np.uint8)


def mask_banner(frame):
    """Blank the power-on version banner so frames can be compared."""
    frame = frame.copy()
    frame[:BANNER_H, :BANNER_W] = 0
    return frame
