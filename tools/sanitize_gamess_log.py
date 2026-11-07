"""Strip identifying and non-scientific text from a GAMESS log before committing it.

A log records where and by whom it was run: rungms prints the host, every path it
sets and the scratch folder, and the program prints dates and timings. Nothing in
the parser reads these. The long acknowledgement list and the distributed-data
trailer go too. tests/test_fixture_privacy.py holds every committed fixture to this.

Usage: python tools/sanitize_gamess_log.py INPUT OUTPUT
"""

import re
import sys

_DROPPED_LINE = re.compile(
    r"^\s*(?:CPU\s+\d+: STEP CPU TIME|TOTAL WALL CLOCK TIME|\d+\s+WORDS OF DYNAMIC"
    r"|DDI: |CPU timing|ddikick|\d+: \d+\.\d+ \+ |=+$|-{20,}$|unset echo"
    r"|Initiating |PARALLEL VERSION RUNNING|USING DDI|EXECUTION OF GAMESS BEGUN)"
)
_DATE = re.compile(r"\b\d\d:\d\d:\d\d \d\d-[A-Z]{3}-\d{4}\b")
_PATH = re.compile(r"/(?:home|Users|scratch|work|tmp)/[^\s\"]*")
_HOST_NOTE = re.compile(r"(NODE|ON HOST|HOST)\s+\S+")
_THANKS_START = "GAMESS HAS BEEN MADE POSSIBLE"
_THANKS_END = re.compile(r"^\s*(?:EXECUTION OF|ECHO OF THE FIRST|\$CONTRL OPTIONS)")


def sanitize(text):
    """Return *text* with the run script, acknowledgements, paths and times removed."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if "GAMESS VERSION =" in l), 0)
    # Keep the three banner rules around the version line.
    start = max(start - 2, 0)
    out = []
    skipping_thanks = False
    for line in lines[start:]:
        if _THANKS_START in line:
            skipping_thanks = True
            continue
        if skipping_thanks:
            if not _THANKS_END.match(line):
                continue
            skipping_thanks = False
        if line.strip().startswith("ddikick.x") or "exited gracefully" in line:
            break
        if _DROPPED_LINE.match(line) or "STEP CPU TIME" in line:
            continue
        line = _PATH.sub("PATH", line)
        line = _DATE.sub("DATE", line)
        out.append(line.rstrip())
    return "\n".join(out) + "\n"


def main(argv):
    """Sanitize argv[1] into argv[2]."""
    with open(argv[1], encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    with open(argv[2], "w", encoding="utf-8", newline="\n") as handle:
        handle.write(sanitize(text))


if __name__ == "__main__":
    main(sys.argv)
