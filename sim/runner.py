"""Build the F18A simulation with NVC and run cocotb test modules."""

import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

SIM_DIR = Path(__file__).resolve().parent
RTL_DIR = SIM_DIR.parent
BUILD_DIR = SIM_DIR / "sim_build"
TOPLEVEL = "f18a_tb"

# Analysis order matters: each unit after the ones it instantiates.
RTL_SOURCES = [
    "f18a_video_pkg.vhd",
    "f18a_version.vhd",
    "f18a_color.vhd",
    "f18a_counters.vhd",
    "f18a_div32x16.vhd",
    "f18a_single_port_ram.vhd",
    "f18a_vram.vhd",
    "f18a_raster.vhd",
    "f18a_gpu.vhd",
    "f18a_v9938_cmd.vhd",
    "f18a_cpu.vhd",
    "f18a_tile_linebuf.vhd",
    "f18a_bitmap.vhd",
    "f18a_tiles.vhd",
    "f18a_sprites.vhd",
    "f18a_core.vhd",
]
TB_SOURCES = ["tb/f18a_tb.vhd"]
BUILD_ARGS = ["--std=2008"]

# The cocotb runner passes sys.path to the simulator as PYTHONPATH; make the
# helper modules in sim/ importable from the tests.
if str(SIM_DIR) not in sys.path:
    sys.path.insert(0, str(SIM_DIR))


# The OCM-PLD VDP wrapper, with a simulation model of its PLL.
OCM_TOPLEVEL = "ocm_tb"
OCM_SOURCES = ["tb/f18a_vdp_pll_sim.vhd"]
OCM_RTL_SOURCES = ["ocm/f18a_vdp_ocm.vhd"]
OCM_TB_SOURCES = ["tb/ocm_tb.vhd"]

# The V9990 (v9990/), a separate chip with its own testbench.
V9990_TOPLEVEL = "v9990_tb"
V9990_RTL_SOURCES = [
    "v9990/v9990_pkg.vhd",
    "v9990/v9990_vram_bram.vhd",
    "v9990/v9990_cpu.vhd",
    "v9990/v9990_raster.vhd",
    "v9990/v9990_bitmap.vhd",
    "v9990/v9990_pattern.vhd",
    "v9990/v9990_sprites.vhd",
    "v9990/v9990_cmd.vhd",
    "v9990/v9990_core.vhd",
]
V9990_TB_SOURCES = ["tb/v9990_tb.vhd"]


def build(toplevel=TOPLEVEL):
    runner = get_runner("nvc")
    sources = [RTL_DIR / s for s in RTL_SOURCES]
    if toplevel == V9990_TOPLEVEL:
        sources = [RTL_DIR / s for s in V9990_RTL_SOURCES] + [SIM_DIR / s for s in V9990_TB_SOURCES]
    elif toplevel == OCM_TOPLEVEL:
        sources += [SIM_DIR / s for s in OCM_SOURCES] + [RTL_DIR / s for s in OCM_RTL_SOURCES]
        sources += [SIM_DIR / s for s in OCM_TB_SOURCES]
    else:
        sources += [SIM_DIR / s for s in TB_SOURCES]
    runner.build(
        sources=sources,
        hdl_toplevel=toplevel,
        build_dir=BUILD_DIR,
        build_args=BUILD_ARGS,
        always=True,
    )
    return runner


def test(runner, test_module, testcase=None, toplevel=TOPLEVEL):
    """Run one cocotb test module; returns the results XML path."""
    capture_dir = BUILD_DIR / "frames" / test_module
    capture_dir.mkdir(parents=True, exist_ok=True)
    return runner.test(
        hdl_toplevel=toplevel,
        test_module=test_module,
        testcase=testcase,
        test_dir=SIM_DIR / "tests",
        build_dir=BUILD_DIR,
        parameters={"CAPTURE_DIR": str(capture_dir)},
        extra_env={"F18A_CAPTURE_DIR": str(capture_dir)},
        # The IEEE packages warn about 'U' operands before reset.
        test_args=["--ieee-warnings=off"],
        results_xml=str(BUILD_DIR / f"results_{test_module}.xml"),
        log_file=BUILD_DIR / f"{test_module}.log",
    )
