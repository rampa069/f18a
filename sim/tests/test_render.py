"""Render 9918A scenes and compare against the reference model and goldens.

Set F18A_UPDATE_GOLDEN=1 to (re)generate sim/golden/<scene>.png.
"""

import os
from pathlib import Path

import cocotb
import numpy as np
from cocotb.triggers import FallingEdge

import scenes
from f18a_driver import CAPTURE_DIR, F18A, load_png, mask_banner, save_png
from tms9918_model import render_frame

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"
UPDATE_GOLDEN = os.environ.get("F18A_UPDATE_GOLDEN") == "1"


def describe_diff(got, exp):
    diff = np.any(got != exp, axis=2)
    ys, xs = np.nonzero(diff)
    if not len(ys):
        return "identical"
    return (f"{len(ys)} pixels differ, bbox x={xs.min()}..{xs.max()} y={ys.min()}..{ys.max()}; "
            f"first at ({xs[0]},{ys[0]}) got {got[ys[0], xs[0]]} expected {exp[ys[0], xs[0]]}")


async def run_scene(dut, name):
    vram, regs = scenes.SCENES[name]()
    f = F18A(dut)
    await f.reset(sprite_max_4=True)
    await f.set_reg(1, 0x80)                   # blank while loading
    await f.load_vram(vram)
    dut._log.info("%s: VRAM loaded", name)
    await f.set_regs(regs)
    await f.read_status()
    await FallingEdge(dut.vsync_o)             # let a full frame settle the status
    await f.read_status()

    got = mask_banner(await f.capture_frame())
    status = await f.read_status()
    dut._log.info("%s: frame captured, status %02x", name, status)
    exp_frame, exp_status = render_frame(vram, regs, max_per_line=4)
    exp = mask_banner(exp_frame)

    save_png(got, CAPTURE_DIR / f"{name}.png")
    save_png(exp, CAPTURE_DIR / f"{name}_model.png")

    golden = GOLDEN_DIR / f"{name}.png"
    if UPDATE_GOLDEN:
        GOLDEN_DIR.mkdir(exist_ok=True)
        save_png(got, golden)
    elif golden.exists():
        assert np.array_equal(got, load_png(golden)), f"{name}: differs from golden: {describe_diff(got, load_png(golden))}"

    assert np.array_equal(got, exp), f"{name}: differs from model: {describe_diff(got, exp)}"

    # 5S and C flags (the frame flag is not compared).
    assert status & 0x60 == exp_status.value() & 0x60, f"{name}: status {status:02x} expected {exp_status.value():02x}"
    if exp_status.fifth:
        assert status & 0x1F == exp_status.fifth_num, f"{name}: 5th sprite {status & 0x1F} expected {exp_status.fifth_num}"


# Known F18A deviations from the 9918A, tracked in beads.  These tests are
# expected to fail until the bug is fixed; cocotb then reports them as failing
# ("unexpectedly passed") so the entry can be removed.
KNOWN_BUGS = {
    "graphics2_mag": "f18a-v0x",        # early clock sprites miss column 0
    "sprite_edges": "f18a-v0x",
    "sprite_edges_mag": "f18a-v0x",
    "sprite_edges_16": "f18a-v0x",
    "sprite_edges_16_mag": "f18a-v0x",
    "blanked": "f18a-y7g",              # sprite flags set while blanked
}


def make_test(name):
    async def test(dut):
        await run_scene(dut, name)
    test.__name__ = test.__qualname__ = f"render_{name}"
    return cocotb.test(expect_fail=name in KNOWN_BUGS)(test)


for _name in scenes.SCENES:
    globals()[f"render_{_name}"] = make_test(_name)
