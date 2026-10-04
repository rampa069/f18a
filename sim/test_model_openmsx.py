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


@pytest.mark.parametrize("seq", ["basic"])
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
