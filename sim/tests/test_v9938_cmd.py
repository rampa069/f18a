"""V9938 command engine of the RTL against the reference model.

Runs the command sequences of io_sequences.SEQUENCES_CMD (checked against
openMSX in test_model_openmsx.py) on the core in V9938 mode and on
v9938_model, then compares the reads, the whole VRAM and R#32-R#46.  The
model starts from the RTL VRAM (the F18A boot screen in the first 16 KB,
and what the previous tests left), so commands that read untouched VRAM
agree too.

The waits of the sequences poll the status like the Z80 program of the
openMSX oracle: the RTL engine takes time, the model finishes at once.
"""

import cocotb
from cocotb.triggers import Timer

import io_sequences
import v9938_model as vm
from f18a_driver import F18A

PORTS = {0x98: 0, 0x99: 1, 0x9A: 2, 0x9B: 3}
# S#2 bits that depend on the time of the read: VR, HR, EO.
TIMING_BITS = {0: 0xFF, 1: 0x01, 2: 0x62}
REGS = [0, 0x40, 0, 0, 0, 0, 0, 0, 0x08]
POLL_LIMIT = 20000


def engine_regs(cmd):
    """R#32-R#46 of the RTL engine, read back like the model (the high byte
    of the 16-bit counters)."""
    v = {n: int(getattr(cmd, n).value) for n in
         ("sx_r", "sy_r", "dx_r", "dy_r", "nx_r", "ny_r", "col_r", "arg_r", "cmd_r")}
    words = [v["sx_r"], v["sy_r"], v["dx_r"], v["dy_r"], v["nx_r"], v["ny_r"]]
    out = []
    for w in words:
        out += [w & 0xFF, (w >> 8) & 0xFF]
    return out + [v["col_r"], v["arg_r"], v["cmd_r"]]


def vram_snapshot(ram):
    """The whole RTL VRAM, read as one array value (much faster than one
    handle per address)."""
    return [int(v) for v in ram.value]


async def run(dut, seq):
    f = F18A(dut)
    await f.reset(v9938=True)
    for r, v in enumerate(REGS):
        await f.set_reg(r, v)
    await f.read_status()

    core = dut.inst_core
    ram = core.inst_vram.inst_ram.ram
    model = vm.V9938(regs=REGS + [0] * (47 - len(REGS)))
    model.vram[:] = bytes(vram_snapshot(ram))
    model.read_status()

    def m_out(port, v):
        {0x98: model.write_data, 0x99: model.write_ctrl,
         0x9A: model.write_palette, 0x9B: model.write_indirect}[port](v)

    async def out(port, v):
        await f.write_port(PORTS[port], v)
        m_out(port, v)

    async def r15(n):
        await out(0x99, n)
        await out(0x99, 0x8F)

    async def poll(bit, value):
        """Read S#2 until (S#2 & bit) == value (the model reads once)."""
        for _ in range(POLL_LIMIT):
            if (await f.read_port(1)) & bit == value:
                break
            await Timer(1, "us")
        else:
            raise AssertionError(f"{seq}: S#2 bit {bit:02x} never became {value:02x}")
        model.read_status()

    errors = []
    nread = 0

    async def read(port):
        nonlocal nread
        sn = model.regs[15] & 0x0F if port == 0x99 else None
        got = await f.read_port(PORTS[port])
        exp = {0x98: model.read_data, 0x99: model.read_status}[port]()
        mask = 0xFF if sn is None else 0xFF & ~TIMING_BITS.get(sn, 0)
        if got & mask != exp & mask:
            errors.append(f"read {nread} (S#{sn}): RTL {got:02x} model {exp:02x}")
        nread += 1

    ops = [("out", 0x99, REGS[1]), ("out", 0x99, 0x81)] + io_sequences.SEQUENCES_CMD[seq]()
    for op in ops:
        kind = op[0]
        if kind == "out":
            await out(op[1], op[2])
        elif kind == "in":
            await read(op[1])
        elif kind == "wait_ce":
            await r15(2)
            await poll(0x01, 0x00)
            await r15(0)
        elif kind == "wait_tr":
            await r15(2)
            await poll(0x80, 0x80)
            await r15(0)
        elif kind == "delay":
            pass
        elif kind == "block":
            for v in op[2]:
                await out(op[1], v)
        elif kind == "tr_out":
            await r15(2)
            for v in op[1]:
                await poll(0x80, 0x80)
                await out(0x9B, v)
            await r15(0)
        elif kind == "tr_in":
            for _ in range(op[1]):
                await r15(2)
                await poll(0x80, 0x80)
                await r15(7)
                await read(0x99)
            await r15(0)
        else:
            raise ValueError(op)

    # A command still running would make the comparison meaningless.
    assert not int(core.inst_cpu.inst_cmd.busy.value), f"{seq}: engine still busy"

    got = engine_regs(core.inst_cpu.inst_cmd)
    for i, g in enumerate(got):
        exp = model.cmd.read_reg(i)
        if g != exp:
            errors.append(f"R#{32 + i}: RTL {g:02x} model {exp:02x}")

    rtl = vram_snapshot(ram)
    diff = [(a, rtl[a], model.vram[a]) for a in range(vm.VRAM_SIZE) if rtl[a] != model.vram[a]]
    if diff:
        errors.append(f"VRAM differs at {len(diff)} addresses: " + ", ".join(
            f"{a:05x} RTL {g:02x} model {m:02x}" for a, g, m in diff[:12]))
    assert not errors, f"{seq}:\n" + "\n".join(errors[:40])
    dut._log.info("%s: %d ops, %d reads", seq, len(ops), nread)


def _make(seq):
    async def t(dut):
        await run(dut, seq)
    t.__name__ = t.__qualname__ = f"cmd_{seq}"
    return cocotb.test()(t)


for _seq in io_sequences.SEQUENCES_CMD:
    if _seq not in io_sequences.V9958_SEQUENCES:
        globals()[f"cmd_{_seq}"] = _make(_seq)
