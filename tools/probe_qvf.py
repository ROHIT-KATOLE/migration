"""Probe a .qvf binary to assess what's recoverable, without Qlik installed.

Read-only triage: reports the file header, detects compression/container
signatures, samples readable strings, counts Qlik object markers, and dumps all
strings to ``<name>.strings.txt`` for inspection. Run this first on an unfamiliar
app to confirm the binary reader will be able to parse it.

Usage: python tools/probe_qvf.py "App.qvf"
"""
import re
import sys
from pathlib import Path

SIGS = {
    b"PK\x03\x04": "ZIP archive (try unzipping)",
    b"SQLite format 3\x00": "SQLite database",
    b"\x1f\x8b": "gzip stream",
    b"\x78\x9c": "zlib stream", b"\x78\x01": "zlib stream", b"\x78\xda": "zlib stream",
}
MARKERS = ["qInfo", "qHyperCubeDef", "qMeasure", "qDim", "qFieldDefs", "qMetaDef",
           "visualization", "SecurityMetaAsBase64", "GenericMeasureProperties",
           "GenericDimensionProperties", "sheet", "LOAD", "SQL SELECT", "CONNECT"]


def main(path: str):
    data = Path(path).read_bytes()
    print(f"\n=== {path}  ({len(data) / 1e6:.1f} MB) ===")
    print("magic:", " ".join(f"{b:02x}" for b in data[:16]))
    for sig, name in SIGS.items():
        if sig in data[:8192]:
            print("  signature:", name)
    printable = sum(1 for b in data if 32 <= b < 127)
    pct = printable / len(data) * 100
    print(f"printable: {pct:.1f}%  "
          f"({'text/recoverable' if pct > 25 else 'mostly compressed/binary'})")

    ascii_s = re.findall(rb"[\x20-\x7e]{6,}", data)
    blob = b"\n".join(ascii_s).lower()
    print(f"ascii strings: {len(ascii_s)}")
    print("marker hits:")
    for m in MARKERS:
        c = blob.count(m.lower().encode())
        if c:
            print(f"  {m:<28} {c}")

    out = Path(path).with_suffix(".strings.txt")
    out.write_text("\n".join(s.decode("latin1") for s in ascii_s),
                   encoding="utf-8", errors="ignore")
    print(f"strings dumped -> {out.name}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python tools/probe_qvf.py <file.qvf>")
        sys.exit(1)
    main(sys.argv[1])
