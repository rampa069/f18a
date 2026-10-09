"""pytest entry point: builds each simulation once and runs the cocotb modules.

    make test                         # everything
    make test T=test_render           # one module
    F18A_UPDATE_GOLDEN=1 make test    # regenerate golden frames
"""

import os

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
    ("test_v9938_cmd", runner.TOPLEVEL),
    ("test_testcard", runner.TOPLEVEL),
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


def _quick_cases():
    """F18A_QUICK: "module.py::test ..." pairs, grouped per module."""
    groups = {}
    for item in os.environ.get("F18A_QUICK", "").split():
        mod, case = item.split("::")
        groups.setdefault(mod.removesuffix(".py"), []).append(case)
    return list(groups.items())


@pytest.mark.parametrize("module,cases", _quick_cases(), ids=[m for m, _ in _quick_cases()])
def test_quick(module, cases):
    toplevel = dict(MODULES)[module]
    xml = runner.test(sim(toplevel), module, testcase=cases, toplevel=toplevel)
    try:
        _, failed = get_results(xml)
    except SystemExit:
        failed = "some"
    assert not failed, f"{failed} cocotb test(s) failed in {module}, see sim/sim_build/{module}.log"
