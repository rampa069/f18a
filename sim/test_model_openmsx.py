"""Check the V9938 reference model against openMSX.

Needs the openMSX oracle host (see openmsx_oracle.py); skipped unless
F18A_OPENMSX=1:

    F18A_OPENMSX=1 ../.venv/bin/python -m pytest -v test_model_openmsx.py
"""

import os

import numpy as np
import pytest

import v9938_model as vm
import v9938_scenes

pytestmark = pytest.mark.skipif(os.environ.get("F18A_OPENMSX") != "1",
                                reason="set F18A_OPENMSX=1 to run against openMSX")


@pytest.fixture(scope="module")
def shots():
    import openmsx_oracle as oracle
    scenes = []
    for name, make in v9938_scenes.SCENES.items():
        vram, regs, pal = make()
        scenes.append(oracle.Scene(name, vram, regs, pal))
    return oracle.run_scenes(scenes)


@pytest.mark.parametrize("name", list(v9938_scenes.SCENES))
def test_scene(shots, name):
    vram, regs, pal = v9938_scenes.SCENES[name]()
    model = vm.V9938(vram, regs, pal)
    exp = model.render().astype(np.int32)
    got = shots[name].active
    assert got.shape == exp.shape, f"{name}: {got.shape} vs {exp.shape}"
    diff = got != exp
    if diff.any():
        ys, xs = np.nonzero(diff)
        raise AssertionError(
            f"{name} ({vm.MODE_NAMES[model.mode]}): {len(ys)} samples differ, "
            f"lines {ys.min()}..{ys.max()}, x {xs.min()}..{xs.max()}; "
            f"first ({xs[0]},{ys[0]}) openMSX {got[ys[0], xs[0]]} model {exp[ys[0], xs[0]]}")
    assert shots[name].border == model.border(), f"{name}: border {shots[name].border} vs {model.border()}"


def run_model_io(ops, regs):
    model = vm.V9938(regs=list(regs) + [0] * (47 - len(regs)))
    model.read_status()
    reads = []
    for op in ops:
        port = op[1]
        if op[0] == "out":
            {0x98: model.write_data, 0x99: model.write_ctrl,
             0x9A: model.write_palette, 0x9B: model.write_indirect}[port](op[2])
        else:
            reads.append({0x98: model.read_data, 0x99: model.read_status}[port]())
    return reads, model


# Status bits that depend on the exact time of the read, per S#n (S#0 has
# the frame flag and the sprite flags of the last frame).
TIMING_BITS = {0: 0xFF, 1: 0x01, 2: 0x60}


@pytest.mark.parametrize("seq", ["basic", "vr0"])
def test_io(seq):
    import io_sequences
    import openmsx_oracle as oracle
    ops = io_sequences.SEQUENCES[seq]()
    regs = [0, 0x40, 0, 0, 0, 0, 0, 0, 0x08]      # R#8 VR = 1 (64K chips), like an MSX2 BIOS
    o_reads, o_vram, o_regs, o_pal = oracle.run_io(ops, regs)
    m_reads, model = run_model_io(ops, regs)

    # Which status register each status read returned.
    model_regs15 = []
    sim = vm.V9938(regs=list(regs) + [0] * (47 - len(regs)))
    for op in ops:
        if op[0] == "out":
            {0x98: sim.write_data, 0x99: sim.write_ctrl,
             0x9A: sim.write_palette, 0x9B: sim.write_indirect}[op[1]](op[2])
        else:
            model_regs15.append(sim.regs[15] & 0x0F if op[1] == 0x99 else None)
            {0x98: sim.read_data, 0x99: sim.read_status}[op[1]]()

    assert len(o_reads) == len(m_reads)
    for k, (o, m, sn) in enumerate(zip(o_reads, m_reads, model_regs15)):
        mask = 0xFF if sn is None else 0xFF & ~TIMING_BITS.get(sn, 0)
        assert o & mask == m & mask, f"read {k} (S#{sn}): openMSX {o:02x} model {m:02x}"
    diff = [a for a in range(vm.VRAM_SIZE) if o_vram[a] != model.vram[a]]
    assert not diff, f"VRAM differs at {len(diff)} addresses, first {diff[0]:05x}: openMSX {o_vram[diff[0]]:02x} model {model.vram[diff[0]]:02x}"
    for r in range(47):
        assert o_regs[r] == model.regs[r], f"R#{r}: openMSX {o_regs[r]:02x} model {model.regs[r]:02x}"
    # The initial palette is the BIOS one; compare the entries written.
    for n in sorted(model.palette_written):
        assert o_pal[n] == model.palette[n], f"palette {n}: openMSX {o_pal[n]} model {model.palette[n]}"


def run_model_cmd(ops, regs, v9958=False):
    """Run a command engine sequence on the model: returns the reads, the
    status register each read came from (None for VRAM) and the model.  The
    waits of the Z80 program are no-ops here (the model runs a command at
    once), but their R#15 writes and status reads are done like the Z80."""
    model = vm.V9938(regs=list(regs) + [0] * (47 - len(regs)))
    model.v9958 = v9958
    model.read_status()
    reads, sources = [], []

    def out(port, v):
        {0x98: model.write_data, 0x99: model.write_ctrl,
         0x9A: model.write_palette, 0x9B: model.write_indirect}[port](v)

    def r15(n):
        out(0x99, n)
        out(0x99, 0x8F)

    def read(port):
        sources.append(model.regs[15] & 0x0F if port == 0x99 else None)
        reads.append({0x98: model.read_data, 0x99: model.read_status}[port]())

    for op in [("out", 0x99, regs[1]), ("out", 0x99, 0x81)] + list(ops):
        kind = op[0]
        if kind == "out":
            out(op[1], op[2])
        elif kind == "in":
            read(op[1])
        elif kind in ("wait_ce", "wait_tr"):
            r15(2)
            model.read_status()
            r15(0)
        elif kind == "delay":
            pass
        elif kind == "block":
            for v in op[2]:
                out(op[1], v)
        elif kind == "tr_out":
            r15(2)
            for v in op[1]:
                model.read_status()
                out(0x9B, v)
            r15(0)
        elif kind == "tr_in":
            for _ in range(op[1]):
                r15(2)
                model.read_status()
                r15(7)
                read(0x99)
            r15(0)
        else:
            raise ValueError(op)
    return reads, sources, model


# S#2 bits that depend on the time of the read: VR, HR, EO.
CMD_TIMING_BITS = {0: 0xFF, 1: 0x01, 2: 0x62}


def _cmd_seqs():
    import io_sequences
    return list(io_sequences.SEQUENCES_CMD)


@pytest.mark.parametrize("seq", _cmd_seqs())
def test_cmd(seq):
    import io_sequences
    import openmsx_oracle as oracle
    ops = io_sequences.SEQUENCES_CMD[seq]()
    regs = [0, 0x40, 0, 0, 0, 0, 0, 0, 0x08]
    v9958 = seq in io_sequences.V9958_SEQUENCES
    o_reads, o_vram, o_regs, _ = oracle.run_io(ops, regs, machine="C-BIOS_MSX2+" if v9958 else "C-BIOS_MSX2")
    m_reads, sources, model = run_model_cmd(ops, regs, v9958)

    errors = []
    assert len(o_reads) == len(m_reads)
    for k, (o, m, sn) in enumerate(zip(o_reads, m_reads, sources)):
        mask = 0xFF if sn is None else 0xFF & ~CMD_TIMING_BITS.get(sn, 0)
        if o & mask != m & mask:
            errors.append(f"read {k} (S#{sn}): openMSX {o:02x} model {m:02x}")
    for r in range(47):
        if o_regs[r] != model.regs[r]:
            errors.append(f"R#{r}: openMSX {o_regs[r]:02x} model {model.regs[r]:02x}")
    diff = [a for a in range(vm.VRAM_SIZE) if o_vram[a] != model.vram[a]]
    if diff:
        errors.append(f"VRAM differs at {len(diff)} addresses: " + ", ".join(
            f"{a:05x} openMSX {o_vram[a]:02x} model {model.vram[a]:02x}" for a in diff[:12]))
    assert not errors, f"{seq}:\n" + "\n".join(errors[:40])


# Scenes with sprites for the collision check.  Each runs in its own openMSX
# session: the Z80 program that clears the collision state only runs for the
# first scene of a session.
COLLISION_SCENES = ["g1", "g2", "g3", "g4_mag_tp", "g5_212", "g7_212"]


@pytest.mark.parametrize("name", COLLISION_SCENES)
def test_collision(name):
    """Sprite collision: S#0 C and the coordinates in S#3-S#6."""
    import openmsx_oracle as oracle
    vram, regs, pal = v9938_scenes.SCENES[name]()
    o = oracle.run_scenes([oracle.Scene(name, vram, regs, pal)], decode=False)[name].status
    model = vm.V9938(vram, regs, pal)
    model.render()
    m = [model.status[k] for k in range(7)]
    assert (o[0] & 0x20, o[3:7]) == (m[0] & 0x20, m[3:7]), \
        f"{name}: collision S#0 {o[0]:02x} S#3-6 {o[3:7]}, model S#0 {m[0]:02x} S#3-6 {m[3:7]}"
