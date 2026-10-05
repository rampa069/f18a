# F18A 3.0 test card

The power-on screen of the F18A 3.0 is a test card (`tools/testcard.py`,
G1 with the reset register values): a 16 x 16 grid and a circle for the
geometry, the 15 colors, the version and the credits.

These images are the card as the simulated VDP shows it (15 kHz frame, 568
samples per line) in every mode, captured by `sim/tests/test_testcard.py`,
which also checks each frame against the reference models.  The corner
banner is the power-on version banner.

| File | Mode |
|---|---|
| `boot.png` | Power-on screen (nothing loaded) |
| `tms_g1.png`, `tms_g2.png`, `tms_mc.png`, `tms_t1.png` | 9918A mode: Graphics 1, Graphics 2, Multicolor, Text 1 |
| `v9938_g3.png` ... `v9938_g7.png` | V9938 mode: G3 (SCREEN 4) to G7 (SCREEN 8); G5 has 4 colors, G7 the fixed 256 colors |
| `v9938_t2.png` | V9938 mode: Text 2 (80 columns) |

Regenerate the power-on screen and the version (banner, VR registers) with
`python3 tools/apply_version.py` after changing `VERSION` in
`tools/testcard.py`.
