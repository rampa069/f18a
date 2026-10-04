# F18A simulation (NVC + cocotb).  Run `make setup` once.

PYTHON ?= ../.venv/bin/python
T ?=

.PHONY: setup test clean

setup:
	python3 -m venv ../.venv
	../.venv/bin/pip install -r requirements.txt

test:
	$(PYTHON) -m pytest -v test_f18a.py $(if $(T),-k $(T))

clean:
	rm -rf sim_build __pycache__ tests/__pycache__ .pytest_cache
