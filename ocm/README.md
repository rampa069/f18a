# F18A as an OCM-PLD VDP replacement

`f18a_vdp_ocm.vhd` defines an entity `vdp` with the same name, ports and
port order as the ESE / OCM-PLD V9938/V9958 VDP
(`esemsx3/src/video/vdp.vhd` in
[ocm-pld-dev](https://github.com/gnogni/ocm-pld-dev)), so an OCM based core
can use the F18A without changing the instance in `emsx_top.vhd`.

This is an interface wrapper: no ESE-VDP code is used.

## Using it in a core

In the core's Quartus project, replace the `src/video/vdp*.vhd` files with:

```
f18a_video_pkg.vhd  f18a_version.vhd  f18a_color.vhd  f18a_counters.vhd
f18a_div32x16.vhd  f18a_single_port_ram.vhd  f18a_vram.vhd  f18a_raster.vhd
f18a_gpu.vhd  f18a_v9938_cmd.vhd  f18a_cpu.vhd  f18a_tile_linebuf.vhd
f18a_bitmap.vhd  f18a_tiles.vhd  f18a_sprites.vhd  f18a_core.vhd
ocm/f18a_vdp_pll.v  ocm/f18a_vdp_ocm.vhd
```

Set `intended_device_family` in `f18a_vdp_pll.v` to the core's FPGA family.
`f18a_vdp.qpf` is a stand-alone synthesis check of the wrapper (all ports as
virtual pins).

## Interface

| Ports | Behavior |
|---|---|
| `CLK21M`, `RESET` | 21.477 MHz system clock; a PLL makes the F18A clocks (85.91 MHz core = x4, 10.74 MHz pixel = /2) |
| `REQ`, `ACK`, `WRT`, `ADR`, `DBO`, `DBI` | I/O 98h (data) and 99h (control / status) as a TMS9918A with the F18A extensions. 9Ah / 9Bh writes are ignored, reads return FFh. `ACK` follows `REQ` one cycle later like the original. `DBI` is valid 5 cycles (230 ns) after `REQ`; OCM samples it much later (420 ns at 3.58 MHz, 6 wait states in turbo modes) |
| `INT_N` | Frame interrupt, synchronized to `CLK21M` |
| `PRAM*` | Unused: the VRAM is inside the F18A. `PRAMWE_N` / `PRAMOE_N` stay high |
| `PVIDEODHCLK`, `PVIDEODLCLK` | Same 4-phase sequence as the original VDP. **Required**: `emsx_top` uses them to schedule CPU / VDP SDRAM slots |
| `PVIDEOR/G/B` | 6-bit RGB (the F18A 4-bit color with the 2 MSBs repeated) |
| `PVIDEOHS_N`, `PVIDEOVS_N`, `PVIDEOCS_N`, `BLANK_O` | Syncs and blank, active low syncs, `BLANK_O` = '1' outside the picture |
| `DISPRESO` | '0' = 15 kHz (1368 `CLK21M` cycles per line, like the V9938), '1' = 31 kHz through a line doubler (684 cycles per line) |
| `NTSC_PAL_TYPE`, `FORCED_V_MODE` | PAL / NTSC: R#9 bit 1 (NT) when `NTSC_PAL_TYPE` = '1', otherwise `FORCED_V_MODE`, like the original. NTSC 262 lines 59.9 Hz, PAL 313 lines 50.1 Hz |
| `INTERLACEMODE` | Always '0' |
| `VGA_INT_FIELD`, `SPMAXSPR` | Default '0', so the wrapper also binds to the older `vdp` component without them (e.g. the [ZEMMIX](https://github.com/BigMist/ZEMMIX) `emsx_top`) |
| `VDPSPEEDMODE`, `RATIOMODE`, `CENTERYJK_R25_N`, `LEGACY_VGA`, `VGA_INT_FIELD`, `SPMAXSPR`, `VDP_ID`, `OFFSET_Y` | Ignored |

## MSX2 compatibility

The F18A is a TMS9918A, not a V9938.  MSX1 software works; MSX2 features
are not available: the V9938/V9958 screen modes (SCREEN 4-8, 80 columns,
SCREEN 10-12), the command engine, 128 KB VRAM, the 512 color palette,
sprite mode 2, R#18 adjust, R#23 vertical scroll, line interrupts,
interlace and the status registers S#1-S#9.

So that an MSX2 / MSX2+ BIOS does not corrupt the display, register writes
to R#8 and above are ignored while the F18A is locked, instead of being
masked to R#0-7 like a real 9918A does.  R#57 still unlocks the F18A
extensions.  Full V9938 compatibility is tracked in beads (f18a-5pv).
