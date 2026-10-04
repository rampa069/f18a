# F18A on Altera / Intel Cyclone IV E

Reference Quartus project for a stand-alone F18A on a Cyclone IV E
(EP4CE22F17C8), equivalent to `f18a_top.vhd` + `f18a_brd_v13.ucf` for the
original Xilinx Spartan-3E board.

| File | Purpose |
|---|---|
| `f18a_top_altera.vhd` | Top level: PLL, power-on reset, host bus tristate, CPUCLK/GROMCLK outputs, 15 kHz video with PAL/NTSC selection |
| `f18a_pll.v` | altpll: 50 MHz in, 100 MHz + 25 MHz (phase aligned) out |
| `f18a.qpf`, `f18a.qsf` | Project, sources, device, I/O defaults |
| `f18a.sdc` | Clocks and false paths for the asynchronous host bus and video outputs |

The core itself (`../f18a_*.vhd`) is vendor independent.  To use the F18A
inside a larger SoC, instantiate `f18a_core` directly with a 100 MHz clock and
a phase-aligned 25 MHz clock, as this top level does.

## Building

Quartus Prime Lite 21.1 (Docker image `raetro/quartus:21.1.1`):

```bash
cd altera
quartus_sh --flow compile f18a
```

## Results (Quartus 21.1.1 Lite, EP4CE22F17C8)

- 4,383 LEs (20 %), 2,009 registers, 24 M9K (166,400 bits), 5 multipliers, 1 PLL
- Timing met in all corners; worst setup slack 0.112 ns on the 100 MHz clock
  (Fmax 101.1 MHz at slow 85 °C).  The critical paths go from RAM outputs
  (VRAM to GPU, tile line buffer to palette RAM) without an intermediate
  register, so there is little margin when the core shares the device with
  other logic.

## Video output

The board outputs 15 kHz RGB like a real 9918A (NTSC) / 9929A (PAL), meant
for boards with their own scandoubler.  `pal_net` (weak pull-up, jumper to
ground) selects the standard; it takes effect at the next frame.

| `pal_net` | Standard | Lines | Borders top / bottom | Frame rate |
|---|---|---|---|---|
| open | NTSC | 262 | 27 / 24 | 59.94 Hz |
| jumper | PAL | 313 | 51 / 51 | 50.17 Hz |

Lines are 63.68 us (15.70 kHz) with 13 + 256 + 15 visible pixels.  Outputs:
4-bit RGB, `hsync_net`, `vsync_net` and composite `csync_net` (all syncs
active low), and `blank_net` ('1' outside the picture, the inverse of display
enable, e.g. for MiSTer).  The core also keeps the original 640x480 VGA output
(`video_15k_i = '0'`), not used by this top level.

## Notes

- There are no pin assignments: add them for a specific board.  The host bus
  of a real 9918A socket is 5 V and needs level shifters; the FPGA side is
  3.3 V LVTTL.  The USR jumper inputs have weak pull-ups like the original
  board.
- The PLL is in Verilog because Quartus 21.1 fails to compute the altpll
  parameters when it is instantiated from VHDL through `altera_mf_components`.
