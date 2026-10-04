# F18A simulation (NVC + cocotb).  Run `make setup` once.

PYTHON ?= ../.venv/bin/python
T ?=

.PHONY: setup test test-quick clean

setup:
	python3 -m venv ../.venv
	../.venv/bin/pip install -r requirements.txt

test:
	$(PYTHON) -m pytest -v test_f18a.py $(if $(T),-k $(T))

# A few minutes: one test of each area (9918A I/O, timing, render, V9938).
QUICK = test_host_io.py::vram_write_read test_timing.py::ntsc_sync_timing \
        test_render.py::render_graphics2 test_v9938_io.py::io_basic \
        test_v9938_display.py::render_g3

test-quick:
	F18A_QUICK="$(QUICK)" $(PYTHON) -m pytest -v test_f18a.py -k quick

clean:
	rm -rf sim_build __pycache__ tests/__pycache__ .pytest_cache
