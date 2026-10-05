"""The F18A test card (tools/testcard.py) in every mode: each frame is
compared against the reference model and saved to docs/testcard/<mode>.png.
The power-on version banner is masked for the comparison only."""

import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.triggers import FallingEdge

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import testcard                                          # noqa: E402
import v9938_model as vm                                 # noqa: E402
from f18a_driver import F18A, mask_banner, save_png      # noqa: E402
from test_v9938_display import load, model_frame         # noqa: E402
from tms9918_model import render_frame15                 # noqa: E402

OUT = ROOT / "docs" / "testcard"


async def run(dut, mode):
    vram, regs, pal, chip = testcard.mode_scene(mode)
    f = F18A(dut)
    if chip == "tms":
        await f.reset(sprite_max_4=True)
        await f.set_reg(1, 0x80)
        await f.load_vram(vram)
        await f.set_regs(regs)
        exp, _ = render_frame15(vram, regs, "ntsc", max_per_line=4)
    else:
        await load(f, vram, regs, pal, vram_size=len(vram))
        exp = model_frame(vm.V9938(vram, regs, pal))
    await f.read_status()
    await FallingEdge(dut.vsync_n_o)
    got = await f.capture_frame()
    OUT.mkdir(parents=True, exist_ok=True)
    save_png(got, OUT / f"{chip}_{mode.lower()}.png")
    g, e = mask_banner(got), mask_banner(exp)
    assert g.shape == e.shape, f"{mode}: frame {g.shape}, model {e.shape}"
    diff = np.any(g != e, axis=2)
    assert not diff.any(), f"{mode}: {diff.sum()} samples differ from the model"


@cocotb.test()
async def testcard_boot(dut):
    """The power-on screen: the card in G1 with the reset register values,
    nothing loaded.  First in the module: the reset does not restore the
    VRAM the other tests load."""
    f = F18A(dut)
    await f.reset(sprite_max_4=True)
    await FallingEdge(dut.vsync_n_o)
    got = await f.capture_frame()
    OUT.mkdir(parents=True, exist_ok=True)
    save_png(got, OUT / "boot.png")
    vram, _, _ = testcard.to_g1(testcard.card())
    exp, _ = render_frame15(vram, [0x00, 0x40, 0x00, 0x10, 0x01, 0x0A, 0x02, 0x1F], "ntsc", max_per_line=4)
    g, e = mask_banner(got), mask_banner(exp)
    diff = np.any(g != e, axis=2)
    assert not diff.any(), f"boot: {diff.sum()} samples differ from the card"


def _make(mode):
    async def t(dut):
        await run(dut, mode)
    t.__name__ = t.__qualname__ = f"testcard_{mode.lower()}"
    return cocotb.test()(t)


for _mode in testcard.MODES:
    globals()[f"testcard_{_mode.lower()}"] = _make(_mode)
