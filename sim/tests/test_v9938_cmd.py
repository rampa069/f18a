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


async def run(dut, seq, fast=True):
    f = F18A(dut)
    await f.reset(v9938=True)
    dut.cmd_fast_i.value = 1 if fast else 0
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
            await Timer(1 if fast else 20, "us")
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


@cocotb.test()
async def cmd_G4_move_timed(dut):
    """The G4_move sequence with the V9938 command timing."""
    await run(dut, "G4_move", fast=False)


for _seq in io_sequences.SEQUENCES_CMD:
    if _seq not in io_sequences.V9958_SEQUENCES:
        globals()[f"cmd_{_seq}"] = _make(_seq)


# -- V9938 command timing (cmd_fast_i = 0) ------------------------------------

VAS_URL = "openMSX src/video/VDPAccessSlots.cc"
CYCLE_NS = 4 * 11.640          # 21.477 MHz, 4 core clocks


def slot_table(engine, name):
    v = int(getattr(engine, name).value)          # 1368 bits, bit 0 = MSB
    return [(v >> (1367 - i)) & 1 for i in range(1368)]


async def grants(dut, engine, n_max, timeout_us):
    """(time in ns, line cycle, need) of each access of the engine, taken
    when it is granted a slot."""
    from cocotb.triggers import RisingEdge, with_timeout
    from cocotb.utils import get_sim_time
    out = []

    async def watch():
        while len(out) < n_max:
            await RisingEdge(engine.go_r)
            out.append((get_sim_time("ns"), int(dut.inst_core.cyc_r.value), int(engine.need_r.value)))
    try:
        await with_timeout(watch(), timeout_us, "us")
    except Exception:
        pass
    return out


@cocotb.test()
async def cmd_timing(dut):
    """With the V9938 timing every access goes to the first command slot (of
    the display off table here, BL = 0) at least 'need' cycles after the
    previous one, and the deltas are those of openMSX: HMMV starts after
    112 cycles, then 46 per byte and 46 + 58 after the last of a line."""
    import cocotb as _c
    f = F18A(dut)
    await f.reset(v9938=True)
    dut.cmd_fast_i.value = 0
    for r, v in enumerate([0x06, 0x00, 0x1F, 0, 0, 0, 0, 0, 0x08]):  # G4, BL = 0
        await f.set_reg(r, v)
    engine = dut.inst_core.inst_cpu.inst_cmd
    table = slot_table(engine, "SLOTS_SCREEN_OFF")

    # HMMV 8 x 3 bytes (16 pixels in G4).
    task = _c.start_soon(grants(dut, engine, 24, 2000))
    await f.set_reg(17, 36)
    for v in [10, 0, 20, 0, 16, 0, 3, 0, 0x5A, 0x00, 0xC0]:
        await f.write_port(3, v)
    t_r46 = _c.utils.get_sim_time("ns")
    got = await task
    assert len(got) == 24, f"HMMV: {len(got)} accesses"

    needs = [g[2] for g in got]
    exp = [112] + ([46] * 7 + [104]) * 2 + [46] * 7
    assert needs == exp, f"deltas {needs}"

    prev = None
    for k, (t, cyc, need) in enumerate(got):
        assert table[cyc], f"access {k} at cycle {cyc}: not a command slot"
        if prev is not None:
            gap = round((t - prev[0]) / CYCLE_NS)
            assert gap >= need, f"access {k}: {gap} cycles after the previous one, need {need}"
            # It is the first slot that satisfies 'need'.
            for d in range(need, gap):
                assert not table[(prev[1] + d) % 1368], \
                    f"access {k}: slot at +{d} skipped (need {need}, took {gap})"
        prev = (t, cyc)
    dut._log.info("HMMV: %d accesses in %.1f us", len(got), (got[-1][0] - t_r46) / 1000)
