"""V9938 CPU interface of the RTL against the reference model.

Runs the port sequences of io_sequences.py (checked against openMSX in
test_model_openmsx.py) on the core in V9938 mode and on v9938_model, then
compares the reads, the VRAM, the V9938 registers and the palette.
"""

import cocotb

import io_sequences
import v9938_model as vm
from f18a_driver import F18A

# Status bits that depend on the exact time of the read, per S#n.
TIMING_BITS = {0: 0xFF, 1: 0x01, 2: 0x60}
PORTS = {0x98: 0, 0x99: 1, 0x9A: 2, 0x9B: 3}


def expand(c3):
    """3-bit V9938 color to the 4-bit F18A palette."""
    return (c3 << 1) | (c3 >> 2)


async def run(dut, seq):
    f = F18A(dut)
    await f.reset(v9938=True)
    regs = [0, 0x40, 0, 0, 0, 0, 0, 0, 0x08]
    for r, v in enumerate(regs):
        await f.set_reg(r, v)
    await f.read_status()

    model = vm.V9938(regs=regs + [0] * (47 - len(regs)))
    model.read_status()

    ops = io_sequences.SEQUENCES[seq]()
    for k, op in enumerate(ops):
        port = PORTS[op[1]]
        if op[0] == "out":
            await f.write_port(port, op[2])
            {0: model.write_data, 1: model.write_ctrl,
             2: model.write_palette, 3: model.write_indirect}[port](op[2])
        else:
            sn = model.regs[15] & 0x0F if port == 1 else None
            got = await f.read_port(port)
            exp = {0: model.read_data, 1: model.read_status}[port]()
            mask = 0xFF if sn is None else 0xFF & ~TIMING_BITS.get(sn, 0)
            assert got & mask == exp & mask, f"op {k} read port {port} (S#{sn}): RTL {got:02x} model {exp:02x}"
    return model


@cocotb.test()
async def io_basic(dut):
    model = await run(dut, "basic")
    core = dut.inst_core

    # V9938 registers.
    for r in range(47):
        got = int(core.inst_cpu.v38_reg[r].value)
        assert got == model.regs[r], f"R#{r}: RTL {got:02x} model {model.regs[r]:02x}"

    # VRAM: every address the model has non zero, and a sample of the rest.
    ram = core.inst_vram.inst_ram.ram
    addrs = [a for a in range(vm.VRAM_SIZE) if model.vram[a]] + list(range(0, vm.VRAM_SIZE, 997))
    for a in addrs:
        got = int(ram[a].value)
        if a < 0x4000 and not model.vram[a]:
            continue          # the power-on screen is in the first 16 KB
        assert got == model.vram[a], f"VRAM {a:05x}: RTL {got:02x} model {model.vram[a]:02x}"

    # Palette entries written through port 9Ah, in F18A palette 0.
    for n in sorted(model.palette_written):
        r, g, b = model.palette[n]
        exp = (expand(r) << 8) | (expand(g) << 4) | expand(b)
        got = int(core.inst_color.colram[n].value)
        assert got == exp, f"palette {n}: RTL {got:03x} expected {exp:03x}"
    dut._log.info("checked %d VRAM addresses, %d palette entries", len(addrs), len(model.palette_written))


@cocotb.test()
async def tms_mode_ports(dut):
    """In 9918A mode only A0 is decoded: port 9Ah is the data port and 9Bh
    the control port, like a real 9918A."""
    f = F18A(dut)
    await f.reset(v9938=False)
    await f.write_vram(0x0100, bytes([0x11, 0x22]))
    await f.write_port(2, 0x77)            # data port: VRAM 0x0102
    await f.write_port(3, 0x88)            # control port: first byte latched
    await f.read_status()                  # resets the latch
    got = await f.read_vram(0x0100, 3)
    assert got == bytes([0x11, 0x22, 0x77]), f"VRAM {got.hex()}"
