# F18A simulation tests

Simulates `f18a_core` with [NVC](https://www.nickg.me.uk/nvc/) and drives it
from Python with [cocotb](https://www.cocotb.org/).

## Setup

```bash
brew install nvc          # VHDL simulator (GHDL's Homebrew cask is disabled)
make -C sim setup         # creates ../.venv with cocotb, pytest, numpy, pillow
```

## Running

```bash
make -C sim test                        # everything (~13 min)
make -C sim test T=host_io              # one module: host_io, timing, render, video15k
F18A_UPDATE_GOLDEN=1 make -C sim test   # regenerate sim/golden/*.png
```

Logs, results and captured frames go to `sim/sim_build/`
(`<module>.log`, `frames/<module>/`).

## Layout

| File | Purpose |
|---|---|
| `tb/f18a_tb.vhd` | Wrapper: 100/25 MHz clocks, dumps VGA frames (`capture_en_i`) and 15 kHz frames (`capture15_en_i`) to PPM files |
| `f18a_driver.py` | Host bus driver (9918A MODE/CSW/CSR cycles), VRAM/register helpers, frame capture |
| `tms9918_model.py` | Reference TMS9918A renderer (G1, G2, MC, Text 1, sprites, status flags) |
| `scenes.py` | Deterministic VRAM images and register sets |
| `tests/test_host_io.py` | VRAM read/write, auto-increment, read-ahead, control port latch |
| `tests/test_timing.py` | VGA sync timing, active area, frame interrupt and status F flag |
| `tests/test_render.py` | Renders each scene, compares against the model and the golden PNGs |
| `tests/test_video15k.py` | 15 kHz output: sync timing, composite sync, interrupt rate, image vs. model |

Each render test is checked twice: against the reference model (is the
output a correct 9918A image?) and against `golden/<scene>.png` (did the
output change?).  The power-on version banner in the top-left corner is
masked out.

Known deviations from the 9918A are listed in `KNOWN_BUGS` in
`tests/test_render.py` with their beads issue; those tests are marked
`expect_fail` until the bug is fixed.

## Notes

- The VDP area appears at VGA x=65 (not 64) because of the one-pixel output
  pipeline delay, and the active area is 640x480 with 794-pixel lines
  (31.76 us, 59.97 Hz frames).
- The default sprite limit is driven to four per line (`sprite_max_i = 1`)
  so sprites behave like a real 9918A.
- Some registers had no power-up value and stayed `'U'` in simulation; they
  now have explicit `0` initial values, which is what the FPGA does anyway.
