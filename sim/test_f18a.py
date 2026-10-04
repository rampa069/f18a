"""pytest entry point: builds each simulation once and runs the cocotb modules.

    make test                         # everything
    make test T=test_render           # one module
    F18A_UPDATE_GOLDEN=1 make test    # regenerate golden frames
"""

import pytest
from cocotb_tools.check_results import get_results

import runner

# (cocotb module, HDL toplevel)
MODULES = [
    ("test_host_io", runner.TOPLEVEL),
    ("test_timing", runner.TOPLEVEL),
    ("test_render", runner.TOPLEVEL),
    ("test_v9938_io", runner.TOPLEVEL),
    ("test_v9938_display", runner.TOPLEVEL),
    ("test_ocm", runner.OCM_TOPLEVEL),
]

_built = {}


def sim(toplevel):
    # Both toplevels share the build directory, so rebuild when switching.
    if _built.get("toplevel") != toplevel:
        _built["runner"] = runner.build(toplevel)
        _built["toplevel"] = toplevel
    return _built["runner"]


@pytest.mark.parametrize("module,toplevel", MODULES, ids=[m for m, _ in MODULES])
def test_module(module, toplevel):
    xml = runner.test(sim(toplevel), module, toplevel=toplevel)
    try:
        _, failed = get_results(xml)
    except SystemExit:
        failed = "some"
    assert not failed, f"{failed} cocotb test(s) failed in {module}, see sim/sim_build/{module}.log"
