"""pytest entry point: builds the simulation once and runs each cocotb module.

    make test                         # everything
    make test T=test_render           # one module
    F18A_UPDATE_GOLDEN=1 make test    # regenerate golden frames
"""

import pytest
from cocotb_tools.check_results import get_results

import runner

MODULES = ["test_host_io", "test_timing", "test_render"]


@pytest.fixture(scope="session")
def sim():
    return runner.build()


@pytest.mark.parametrize("module", MODULES)
def test_module(sim, module):
    xml = runner.test(sim, module)
    try:
        _, failed = get_results(xml)
    except SystemExit:
        failed = "some"
    assert not failed, f"{failed} cocotb test(s) failed in {module}, see sim/sim_build/{module}.log"
