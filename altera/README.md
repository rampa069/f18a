# F18A on Altera / Intel Cyclone IV E

Reference Quartus project for a stand-alone F18A on a Cyclone IV E
(EP4CE22F17C8).  It replaces the original Xilinx Spartan-3E board top level
(`f18a_top.vhd` / `f18a_brd_v13.ucf`, removed; see the git history).

| File | Purpose |
|---|---|
| `f18a_top_altera.vhd` | Top level: PLL, power-on reset, host bus tristate, CPUCLK/GROMCLK outputs, 15 kHz video with PAL/NTSC selection |
| `f18a_pll.v` | altpll: 50 MHz in, 85.71 MHz core + 10.71 MHz pixel clock (phase aligned) out |
| `f18a.qpf`, `f18a.qsf` | Project, sources, device, I/O defaults |
| `f18a.sdc` | Clocks and false paths for the asynchronous host bus and video outputs |

The core itself (`../f18a_*.vhd`) is vendor independent.  To use the F18A
inside a larger SoC, instantiate `f18a_core` directly with a core clock and
a phase-aligned pixel clock of 1/8 of it (85.91 / 10.74 MHz from 21.477 MHz
give exactly the 9918A line rate), as this top level does.

## Building

Quartus Prime Lite 21.1 (Docker image `raetro/quartus:21.1.1`):

```bash
cd altera
quartus_sh --flow compile f18a
```

## Results (Quartus 21.1.1 Lite, EP4CE22F17C8)

See the commit history for the current figures; the 85.71 MHz core clock
leaves more timing margin than the original 100 MHz design.

## Video output

The board outputs 15 kHz RGB like a real 9918A (NTSC) / 9929A (PAL), meant
for boards with their own scandoubler.  `pal_net` (weak pull-up, jumper to
ground) selects the standard; it takes effect at the next frame.

| `pal_net` | Standard | Lines | Borders top / bottom | Frame rate |
|---|---|---|---|---|
| open | NTSC | 262 | 27 / 24 | 59.8 Hz |
| jumper | PAL | 313 | 51 / 51 | 50.0 Hz |

With the 50 MHz oscillator lines are 63.84 us (15.66 kHz), 13 + 256 + 15
visible pixels.  Outputs:
4-bit RGB, `hsync_net`, `vsync_net` and composite `csync_net` (all syncs
active low), and `blank_net` ('1' outside the picture, the inverse of display
enable, e.g. for MiSTer).  There is no VGA output: boards use their own scandoubler.

## Notes

- The core clock is 85.71 MHz (85.91 MHz in the OCM wrapper) instead of the
  original 100 MHz, so the GPU and the F18A 10 ns counter run about 14 %
  slower.

- There are no pin assignments: add them for a specific board.  The host bus
  of a real 9918A socket is 5 V and needs level shifters; the FPGA side is
  3.3 V LVTTL.  The USR jumper inputs have weak pull-ups like the original
  board.
- The PLL is in Verilog because Quartus 21.1 fails to compute the altpll
  parameters when it is instantiated from VHDL through `altera_mf_components`.
