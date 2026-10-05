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
make -C sim test                        # everything (~50 min)
make -C sim test T=host_io              # one module: host_io, timing, render, v9938_io, v9938_display, v9938_cmd, testcard, ocm
make -C sim test-quick                  # one test of each area (~4 min)
F18A_UPDATE_GOLDEN=1 make -C sim test   # regenerate sim/golden/*.png
```

Logs, results and captured frames go to `sim/sim_build/`
(`<module>.log`, `frames/<module>/`).

## Layout

| File | Purpose |
|---|---|
| `tb/f18a_tb.vhd` | Wrapper: 85.91 / 10.74 MHz clocks, dumps 15 kHz frames (`capture_en_i`) to PPM files |
| `f18a_driver.py` | Host bus driver (9918A MODE/CSW/CSR cycles), VRAM/register helpers, frame capture |
| `tms9918_model.py` | Reference TMS9918A renderer (G1, G2, MC, Text 1, sprites, status flags) |
| `scenes.py` | Deterministic VRAM images and register sets |
| `tests/test_host_io.py` | VRAM read/write, auto-increment, read-ahead, control port latch |
| `tests/test_timing.py` | NTSC / PAL sync timing, picture area, composite sync, frame interrupt and status F flag |
| `tests/test_render.py` | Renders each scene (NTSC, two also PAL), compares against the model and the golden PNGs |
| `tests/test_v9938_display.py` | V9938 mode display against the model: G1/G2/MC/G3/G4/G5 with 192/212 lines, R#23, pages, TP, sprite mode 2; sprite status; line interrupt (R#19, IE1, FH); set adjust (R#18) |
| `tests/test_v9938_io.py` | V9938 mode CPU interface (ports 98h-9Bh, R#14, planar addresses, palette, indirect registers, status) against `v9938_model.py` |
| `tests/test_v9938_cmd.py` | V9938 command engine: the `io_sequences.SEQUENCES_CMD` sequences (every command and logical operation in G4-G7, clipping, transfers, status) against `v9938_model.py`, comparing the reads, the whole VRAM and R#32-R#46 |
| `tests/test_testcard.py` | The power-on test card (`tools/testcard.py`) in every mode against the models; saves `docs/testcard/<chip>_<mode>.png` and `boot.png` |
| `tb/ocm_tb.vhd`, `tb/f18a_vdp_pll_sim.vhd` | OCM-PLD VDP wrapper testbench (CLK21M, frames sampled on CLK21M) and PLL model |
| `tests/test_ocm.py` | OCM-PLD wrapper: dot clocks, bus, MSX2 register writes, PAL/NTSC selection, 15/31 kHz line timing and image |
| `tb/v9990_tb.vhd`, `v9990_driver.py` | V9990 (`../v9990/`) testbench: 42.95 MHz core clock, VRAM in block RAM, synchronous host bus (req / ack) |
| `tests/test_v9990_io.py` | V9990 CPU interface (ports 60h-6Fh, registers, palette, VRAM pointers and mapping, system reset) against `v9990_model.py` |
| `tests/test_v9990_timing.py` | V9990 line / frame timing (NTSC, PAL), display area and R#16, status VR / HR / EO, VI and HI interrupts, border color |
| `tests/test_v9990_display.py` | V9990 bitmap modes against the model, one frame per scene compared clock by clock: B0-B4, B7, all color modes, scroll and roll, cursors, overscan, PAL, even / odd pages, display off, CPU writes during the display |

Each render test is checked twice: against the reference model (is the
output a correct 9918A image?) and against `golden/<scene>.png` (did the
output change?).  The power-on version banner in the top-left corner is
masked out.

Known deviations from the 9918A are listed in `KNOWN_BUGS` in
`tests/test_render.py` with their beads issue; those tests are marked
`expect_fail` until the bug is fixed.

## V9938 reference model and openMSX oracle

`v9938_model.py` is a functional V9938 model (all display modes, sprite
modes 1 and 2, 192 / 212 lines, R#23, the CPU interface with R#14, 9Ah, 9Bh,
the status registers and, in `v9938_cmd.py`, the command engine) following
openMSX.  It is checked against openMSX
itself:

```bash
F18A_OPENMSX=1 ../.venv/bin/python -m pytest -v test_model_openmsx.py   # ~3 min
```

The oracle is openMSX built from the current git master (image
`openmsx-master`, `openmsx/Dockerfile.master`), because the command engine
follows master: openMSX 20.0 (the Debian package, image `openmsx-headless`
from `openmsx/Dockerfile`, selected with `F18A_OPENMSX_IMAGE`) differs in
BD after SRCH / S#9 and in LINE going above line 0 (see `v9938_cmd.py`).

`openmsx_oracle.py` runs openMSX (free C-BIOS ROMs) headless in Docker with
Xvfb on the host `F18A_OPENMSX_HOST` (default `rampa@ea5iue-laptop.local`,
image built from `openmsx/Dockerfile` as `openmsx-headless`).  Display
scenes are loaded through the Tcl debugger with the Z80 parked, and the raw
double size screenshots are decoded back to VDP color codes; port sequences
(`io_sequences.py`) are assembled into a Z80 program started from the
H.TIMI hook, so reads and writes have their real side effects.
`v9938_scenes.py` has the display scenes.

## V9990 reference model and oracle

`v9990_model.py` models the V9990 like openMSX (`src/video/v9990`): the
CPU interface, the display timing and the bitmap modes with the cursors;
`v9990_sequences.py` has the port sequences and `v9990_scenes.py` the
display scenes.  The model is checked against openMSX with the GFX9000
extension (`v9990_oracle.py`, same Docker image): the port sequences run
by the Z80 and the `Sunrise GFX9000` regs, palette and VRAM debuggables
dumped; the scenes loaded through the debugger and the raw double size
screenshots (`set ::videosource GFX9000`) compared with the model through
the openMSX DAC curve (calibrated with a 16 bpp ramp).  The screenshots are
640 pixels wide, so B7 is compared as pairs of pixels:

```bash
F18A_OPENMSX=1 ../.venv/bin/python -m pytest -v test_v9990_model_openmsx.py   # ~2 min
```

## Notes

- Frames are the 15 kHz picture, one sample per pixel clock (half a VDP
  pixel): 568 x 243 (NTSC) or 568 x 294 (PAL).  The VDP area starts at x=26
  (13 border pixels) because of the one pixel output pipeline delay.
- The default sprite limit is driven to four per line (`sprite_max_i = 1`)
  so sprites behave like a real 9918A.
- Some registers had no power-up value and stayed `'U'` in simulation; they
  now have explicit `0` initial values, which is what the FPGA does anyway.
