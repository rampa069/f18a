"""Host interface: VRAM access, address auto-increment, read-ahead, registers."""

import random

import cocotb

from f18a_driver import F18A


@cocotb.test()
async def vram_write_read(dut):
    f = F18A(dut)
    await f.reset()
    rng = random.Random(10)
    for addr in [0x0000, 0x0123, 0x1FFF, 0x2000, 0x3FF0] + [rng.randrange(0x3F00) for _ in range(4)]:
        data = bytes(rng.getrandbits(8) for _ in range(16))
        await f.write_vram(addr, data)
        got = await f.read_vram(addr, len(data))
        assert got == data, f"@{addr:04x}: wrote {data.hex()} read {got.hex()}"


@cocotb.test()
async def address_wraps_at_16k(dut):
    f = F18A(dut)
    await f.reset()
    await f.write_vram(0x3FFE, b"\x11\x22\x33\x44")
    assert await f.read_vram(0x3FFE, 2) == b"\x11\x22"
    assert await f.read_vram(0x0000, 2) == b"\x33\x44"


@cocotb.test()
async def read_ahead_buffer(dut):
    """A read setup prefetches; a data write also loads the read-ahead latch."""
    f = F18A(dut)
    await f.reset()
    await f.write_vram(0x1000, bytes([0xA0, 0xA1, 0xA2, 0xA3]))
    await f.set_read_addr(0x1000)
    assert await f.read_data() == 0xA0
    # The read already prefetched 0x1001, so a write goes to 0x1002, and the
    # written value is what the next read returns (9918A behavior).
    await f.write_data(0x55)
    assert await f.read_data() == 0x55
    assert await f.read_vram(0x1000, 4) == bytes([0xA0, 0xA1, 0x55, 0xA3])


@cocotb.test()
async def control_port_flipflop_reset_by_status_read(dut):
    """Reading status resets the control port first/second byte latch."""
    f = F18A(dut)
    await f.reset()
    await f.write_vram(0x0200, b"\x99")
    await f.write_ctrl(0x34)            # first byte only
    await f.read_status()               # resets the latch
    await f.set_read_addr(0x0200)       # must be interpreted as a full setup
    assert await f.read_data() == 0x99


@cocotb.test()
async def no_x_on_bus(dut):
    """Status and data reads never return undefined bits after reset."""
    f = F18A(dut)
    await f.reset()
    for _ in range(4):
        await f.read_status()
    await f.set_read_addr(0)
    for _ in range(8):
        await f.read_data()
