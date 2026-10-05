"""Apply the fork version to the VHDL: the power-on banner bitmap
(f18a_version.vhd), the version constants (f18a_cpu.vhd, f18a_gpu.vhd) and
the power-on screen (f18a_single_port_ram.vhd, the test card in G1)."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import testcard  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# 5 x 8 digits in the style of the banner (rows 3-10, '#' = on).
DIGITS = {
    "0": [".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", "#...#", ".###."],
    "1": ["..#..", ".##..", "#.#..", "..#..", "..#..", "..#..", "..#..", "#####"],
    "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#....", "#####"],
    "3": [".###.", "#...#", "....#", "..##.", "....#", "....#", "#...#", ".###."],
    "4": ["...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#.", "...#."],
    "5": ["#####", "#....", "####.", "....#", "....#", "....#", "#...#", ".###."],
    "6": [".###.", "#....", "#....", "####.", "#...#", "#...#", "#...#", ".###."],
    "7": ["#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#...", ".#..."],
    "8": [".###.", "#...#", "#...#", ".###.", "#...#", "#...#", "#...#", ".###."],
    "9": [".###.", "#...#", "#...#", ".####", "....#", "....#", "#...#", ".###."],
}
MAJOR_COL, MINOR_COL = 39, 48           # first column of the two digits
DASH_COLS, DASH_ROW = (31, 35), 3        # the dash after "F18A" (row 3 = the bar of the 8)


def crlf_rw(path, fn):
    s = path.read_bytes().decode()
    crlf = "\r\n" in s
    s = fn(s.replace("\r\n", "\n"))
    path.write_bytes((s.replace("\n", "\r\n") if crlf else s).encode())


def banner(s):
    major, minor = testcard.VERSION.split(".")
    start = s.index("signal verrom : verrom_t :=")
    end = s.index("others => '0');", start)
    rows = re.findall(r"^[ \t]*('[01]'(?:[ \t]*,[ \t]*'[01]')*)[ \t]*,?[ \t]*$", s[start:end], re.M)
    bits = [[c == "1" for c in re.findall(r"'([01])'", r)] for r in rows]
    for col, d in ((MAJOR_COL, major), (MINOR_COL, minor)):
        for r in range(8):                      # clear the old digit (up to 6 wide)
            for k in range(6):
                if col + k < 55:
                    bits[3 + r][col + k] = False
        for r, line in enumerate(DIGITS[d]):
            for k, ch in enumerate(line):
                bits[3 + r][col + k] = ch == "#"
    # "F18A-": the "V" of the original "F18A V1.9" becomes a dash.
    for r in range(8):
        for k in range(DASH_COLS[0] - 2, MAJOR_COL - 1):
            bits[3 + r][k] = False
    for k in range(DASH_COLS[0], DASH_COLS[1] + 1):
        bits[3 + DASH_ROW][k] = True
    out = []
    for row in bits:
        cells = ["'1'" if b else "'0'" for b in row]
        out.append("\t" + ",".join(cells[:57]) + ", " + ",".join(cells[57:]) + ",")
    body = s[start:end]
    first = body.index("(") + 1
    new_body = body[:first] + "\n" + "\n".join(out) + "\n   "
    s = s[:start] + new_body + s[end:]
    # The ASCII art in the comment above.
    art_start = s.index("   -- .........................................................|")
    art_end = s.index("   constant XMAX")
    art = ["   -- " + "".join("#" if b else "." for b in row[:57]) + ("| unused" if i == 0 else "")
           for i, row in enumerate(bits[:15])]
    return s[:art_start] + "\n".join(art) + "\n\n" + s[art_end:]


def constants(s):
    major, minor = testcard.VERSION.split(".")
    s = re.sub(r'(constant VMAJOR\s*: std_logic_vector\(0 to 3\) := X")[0-9A-F](")', rf"\g<1>{major}\2", s)
    s = re.sub(r'(constant VMINOR\s*: std_logic_vector\(0 to 3\) := X")[0-9A-F](")', rf"\g<1>{minor}\2", s)
    return s


def init16k(s):
    vram, _, _ = testcard.to_g1(testcard.card())
    start = s.index("constant INIT16K : init_t :=")
    body_start = s.index("(", start) + 1
    end = s.index(");", body_start)
    head = "  -- F18A " + testcard.VERSION + " test card (tools/testcard.py), G1 with the reset register values\n"
    return s[:body_start] + "\n" + head + testcard.vhdl_init(vram) + "\n" + s[end:]


if __name__ == "__main__":
    testcard.font()                     # keep the original font before INIT16K changes
    crlf_rw(ROOT / "f18a_version.vhd", banner)
    crlf_rw(ROOT / "f18a_cpu.vhd", constants)
    crlf_rw(ROOT / "f18a_gpu.vhd", constants)
    crlf_rw(ROOT / "f18a_single_port_ram.vhd", init16k)
    print("version", testcard.VERSION, "applied")
