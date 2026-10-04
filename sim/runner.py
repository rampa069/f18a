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
    "f18a_vga_cont_640_60.vhd",
    "f18a_video_15k.vhd",
    "f18a_gpu.vhd",
    "f18a_cpu.vhd",
    "f18a_tile_linebuf.vhd",
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


def build():
    runner = get_runner("nvc")
    runner.build(
        sources=[RTL_DIR / s for s in RTL_SOURCES] + [SIM_DIR / s for s in TB_SOURCES],
        hdl_toplevel=TOPLEVEL,
        build_dir=BUILD_DIR,
        build_args=BUILD_ARGS,
        always=True,
    )
    return runner


def test(runner, test_module, testcase=None):
    """Run one cocotb test module; returns the results XML path."""
    capture_dir = BUILD_DIR / "frames" / test_module
    capture_dir.mkdir(parents=True, exist_ok=True)
    return runner.test(
        hdl_toplevel=TOPLEVEL,
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
