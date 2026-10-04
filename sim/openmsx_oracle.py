"""openMSX as an oracle for the V9938 reference model.

Runs openMSX (C-BIOS machines, free ROMs) headless in a Docker container
with Xvfb on a Linux host, loads a VRAM image, the registers and the
palette directly through the openMSX Tcl debugger (the Z80 is parked in a
DI / HALT loop so the BIOS does not touch the VDP), and takes raw
screenshots.  The screenshots are decoded back to VDP color codes so they
can be compared with v9938_model without depending on the RGB conversion.

Host setup (once): a Docker image "openmsx-headless" built from
sim/openmsx/Dockerfile on the host given by F18A_OPENMSX_HOST
(default rampa@ea5iue-laptop.local), working directory F18A_OPENMSX_DIR.

    from openmsx_oracle import Scene, run_scenes
    shots = run_scenes([Scene("g4", vram, regs, palette)])
    shots["g4"].active      # (lines, 512) color codes, like V9938.render()
"""

import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

import v9938_model as vm

HOST = os.environ.get("F18A_OPENMSX_HOST", "rampa@ea5iue-laptop.local")
REMOTE_DIR = os.environ.get("F18A_OPENMSX_DIR", "fpga/openmsx-docker")
IMAGE = "openmsx-headless"

# Where the active area is in the raw screenshot, in 320x240 units (the
# screenshots are taken at double size, 640x480, so 512 pixel modes are not
# blended).
RAW_X0 = 32
RAW_Y0 = {192: 24, 212: 14}


@dataclass
class Scene:
    name: str
    vram: bytes
    regs: list
    palette: list = field(default_factory=lambda: list(vm.DEFAULT_PALETTE))
    machine: str = "C-BIOS_MSX2"


@dataclass
class Shot:
    raw: np.ndarray                 # RGB screenshot
    active: np.ndarray              # (lines, 512) color codes, -1 if unknown
    border: int                     # border color code, -1 if unknown


def _tcl_scene(i, scene, vram_file):
    regs = list(scene.regs) + [0] * (47 - len(scene.regs))
    lines = [f"proc scene{i} {{}} {{",
             "  debug break",
             "  poke 0xE000 0xF3",          # DI
             "  poke 0xE001 0x76",          # HALT
             "  reg pc 0xE000",
             "  vdpreg 16 0"]
    for r, g, b in scene.palette:
        lines.append(f"  debug write ioports 0x9a {(r << 4) | b}")
        lines.append(f"  debug write ioports 0x9a {g}")
    lines.append(f"  set f [open /work/{vram_file} rb]; fconfigure $f -translation binary")
    lines.append("  debug write_block {physical VRAM} 0 [read $f]; close $f")
    for r, v in enumerate(regs):
        if r in (14, 15, 16, 17):
            continue
        lines.append(f"  vdpreg {r} {v & 0xFF}")
    lines.append("  debug cont")
    lines.append(f"  after time 0.5 {{ screenshot -raw -doublesize -prefix /work/{scene.name}_; "
                 f"{'scene' + str(i + 1)} }}")
    lines.append("}")
    return "\n".join(lines)


def _run(scenes, workdir):
    tcl = ["set throttle on", "set maxframeskip 0"]
    for i, s in enumerate(scenes):
        vram_file = f"{s.name}.vram"
        (workdir / vram_file).write_bytes(bytes(s.vram) + bytes(vm.VRAM_SIZE - len(s.vram)))
        tcl.append(_tcl_scene(i, s, vram_file))
    tcl.append(f"proc scene{len(scenes)} {{}} {{ exit }}")
    tcl.append("after time 2 { scene0 }")
    (workdir / "run.tcl").write_text("\n".join(tcl) + "\n")

    machine = scenes[0].machine
    remote = f"{REMOTE_DIR}/run"
    subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, f"rm -rf {remote} && mkdir -p {remote}"], check=True)
    subprocess.run(["scp", "-q", "-r", f"{workdir}/.", f"{HOST}:{remote}/"], check=True)
    cmd = (f"cd {remote} && docker run --rm -v $PWD:/work {IMAGE} sh -c "
           f"'xvfb-run -a -s \"-screen 0 1024x768x24\" timeout 300 "
           f"openmsx -machine {machine} -script /work/run.tcl >/work/openmsx.log 2>&1'")
    subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, cmd], check=True)
    subprocess.run(["scp", "-q", f"{HOST}:{remote}/*.png", f"{workdir}/"], check=True)
    shots = {}
    for s in scenes:
        png = next(workdir.glob(f"{s.name}_*.png"))
        shots[s.name] = np.asarray(Image.open(png).convert("RGB")).astype(np.int32)
    return shots


# -- Color decoding -------------------------------------------------------

_DAC = None   # openMSX 3-bit level -> 8-bit value, per channel
_G7 = None    # openMSX RGB -> G7 color code


def _calibrate():
    """Learn the openMSX palette DAC: show the 8 levels of each channel."""
    global _DAC
    if _DAC is not None:
        return _DAC
    pal = [(k, 0, 0) for k in range(8)] + [(0, k, 7 - k) for k in range(8)]
    vram = bytearray(vm.VRAM_SIZE)
    for y in range(212):
        for xb in range(128):
            c = xb // 8            # 16 stripes of 16 pixels
            vram[y * 128 + xb] = (c << 4) | c
    regs = [0x06, 0x40, 0x1F, 0, 0, 0, 0, 0x00, 0x2A, 0x80]   # G4, TP
    shot = run_scenes([Scene("calib", vram, regs, pal)], decode=False)["calib"].raw
    y = (RAW_Y0[212] + 100) * 2
    dac = {"r": {}, "g": {}, "b": {}}
    for c in range(16):
        r, g, b = shot[y, (RAW_X0 + c * 16 + 8) * 2]
        if c < 8:
            dac["r"][c] = int(r)
        else:
            dac["g"][c - 8] = int(g)
            dac["b"][7 - (c - 8)] = int(b)
    _DAC = dac
    return dac


def _calibrate_g7():
    """Learn the G7 colors: one line with the 256 codes."""
    global _G7
    if _G7 is not None:
        return _G7
    vram = bytearray(vm.VRAM_SIZE)
    for y in range(212):
        for x in range(256):
            vram[vm.planar(y * 256 + x)] = x
    regs = [0x0E, 0x40, 0x1F, 0, 0, 0, 0, 0x00, 0x2A, 0x80]   # G7, TP
    shot = run_scenes([Scene("calg7", vram, regs)], decode=False)["calg7"].raw
    row = shot[(RAW_Y0[212] + 100) * 2]
    _G7 = {tuple(int(v) for v in row[(RAW_X0 + x) * 2]): x for x in range(256)}
    assert len(_G7) == 256, f"G7 colors not unique: {len(_G7)}"
    return _G7


def _decode(raw, scene):
    model = vm.V9938(scene.vram, scene.regs, scene.palette)
    lines = model.lines
    if model.mode == vm.G7:
        lut = _calibrate_g7()
    else:
        dac = _calibrate()
        lut = {}
        for idx, (r, g, b) in enumerate(scene.palette):
            lut.setdefault((dac["r"][r], dac["g"][g], dac["b"][b]), idx)
    assert raw.shape[:2] == (480, 640), f"expected a double size screenshot, got {raw.shape}"
    active = np.full((lines, 512), -1, dtype=np.int32)
    for line in range(lines):
        row = raw[(RAW_Y0[lines] + line) * 2]
        for x in range(512):
            active[line, x] = lut.get(tuple(int(v) for v in row[RAW_X0 * 2 + x]), -1)
    b = raw[4, 4]
    border = lut.get(tuple(int(v) for v in b), -1)
    return active, border


def run_scenes(scenes, decode=True):
    """Run the scenes in one openMSX session; returns {name: Shot}."""
    with tempfile.TemporaryDirectory() as tmp:
        raws = _run(scenes, Path(tmp))
    shots = {}
    for s in scenes:
        raw = raws[s.name]
        if decode:
            active, border = _decode(raw, s)
        else:
            active, border = None, -1
        shots[s.name] = Shot(raw=raw, active=active, border=border)
    return shots


# -- I/O sequences --------------------------------------------------------

PROG_ADDR = 0xD000      # Z80 program in RAM
READS_ADDR = 0xE800     # IN results


def z80_program(ops):
    """Assemble the port sequence: DI, then LD A,n / OUT (p),A and
    IN A,(p) / LD (nn),A with a few NOPs between accesses, then DI; HALT."""
    code = bytearray([0xF3])                            # DI
    n_reads = 0
    for op in ops:
        if op[0] == "out":
            code += bytes([0x3E, op[2] & 0xFF, 0xD3, op[1]])
        else:
            addr = READS_ADDR + n_reads
            code += bytes([0xDB, op[1], 0x32, addr & 0xFF, addr >> 8])
            n_reads += 1
        code += bytes(8)                                # NOPs: VDP access time
    code += bytes([0xF3, 0x76, 0x18, 0xFE])             # DI; HALT; JR $
    assert PROG_ADDR + len(code) < READS_ADDR, "sequence too long"
    return bytes(code), n_reads


def run_io(ops, regs=None, machine="C-BIOS_MSX2"):
    """Run a sequence of port accesses in openMSX, executed by the Z80.

    ops: list of ("out", port, value) or ("in", port).  The VDP starts with
    VRAM cleared, the given registers (R#0-R#23, others 0) and the address
    latch reset.  Returns (reads, vram, regs, palette) after the sequence.
    """
    regs = list(regs or []) + [0] * (47 - len(regs or []))
    # The frame interrupt must be enabled for the hook to run; the program
    # then sets the requested R#1 first (with the Z80 interrupts disabled).
    code, n_reads = z80_program([("out", 0x99, regs[1]), ("out", 0x99, 0x81)] + list(ops))
    # The program is started from the H.TIMI hook (FD9Fh), called by the
    # BIOS on the next frame interrupt: the CPU sits in a HALT of the BIOS
    # idle loop, which only an interrupt ends.  The BIOS interrupt handler
    # reads S#0 before the hook, which resets the control port latch.
    tcl = ["set throttle on", "set maxframeskip 0", "proc doit {} {",
           "  debug break",
           "  debug write_block {physical VRAM} 0 [string repeat \\0 131072]"]
    for r, v in enumerate(regs):
        if r in (15, 16, 17):
            continue
        tcl.append(f"  vdpreg {r} {(v | 0x20) if r == 1 else v & 0xFF}")
    tcl += ["  vdpreg 15 0", "  vdpreg 17 0",
            "  set f [open /work/prog.bin rb]; fconfigure $f -translation binary",
            f"  debug write_block memory {PROG_ADDR} [read $f]; close $f",
            f"  poke 0xFD9F 0xC3; poke 0xFDA0 {PROG_ADDR & 0xFF}; poke 0xFDA1 {PROG_ADDR >> 8}",
            "  debug cont",
            "  after time 1 { dump }", "}",
            "proc dump {} {",
            f"  set f [open /work/reads.bin wb]; fconfigure $f -translation binary",
            f"  puts -nonewline $f [debug read_block memory {READS_ADDR} {max(n_reads, 1)}]; close $f",
            "  set f [open /work/regs.txt w]; for {set r 0} {$r < 47} {incr r} { puts -nonewline $f \"[vdpreg $r] \" }; close $f",
            "  set f [open /work/vram.bin wb]; fconfigure $f -translation binary",
            "  puts -nonewline $f [debug read_block {physical VRAM} 0 131072]; close $f",
            "  set f [open /work/palette.bin wb]; fconfigure $f -translation binary",
            "  puts -nonewline $f [debug read_block {VDP palette} 0 32]; close $f",
            "  exit", "}", "after time 2 { doit }"]
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        (workdir / "run.tcl").write_text("\n".join(tcl) + "\n")
        (workdir / "prog.bin").write_bytes(code)
        remote = f"{REMOTE_DIR}/run"
        subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, f"rm -rf {remote} && mkdir -p {remote}"], check=True)
        subprocess.run(["scp", "-q", f"{workdir}/run.tcl", f"{workdir}/prog.bin", f"{HOST}:{remote}/"], check=True)
        cmd = (f"cd {remote} && docker run --rm -v $PWD:/work {IMAGE} sh -c "
               f"'xvfb-run -a -s \"-screen 0 1024x768x24\" timeout 300 "
               f"openmsx -machine {machine} -script /work/run.tcl >/work/openmsx.log 2>&1'")
        subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, cmd], check=True)
        for f in ("reads.bin", "regs.txt", "vram.bin", "palette.bin"):
            subprocess.run(["scp", "-q", f"{HOST}:{remote}/{f}", f"{workdir}/"], check=True)
        reads = list((workdir / "reads.bin").read_bytes()[:n_reads])
        out_regs = [int(v) for v in (workdir / "regs.txt").read_text().split()]
        vram = (workdir / "vram.bin").read_bytes()
        p = (workdir / "palette.bin").read_bytes()
        palette = [((p[2 * i] >> 4) & 7, p[2 * i + 1] & 7, p[2 * i] & 7) for i in range(16)]
    return reads, vram, out_regs, palette
