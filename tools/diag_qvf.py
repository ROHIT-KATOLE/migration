"""Decisive decode test for a .qvf: which strategy (if any) recovers JSON objects.

The object payloads inside a .qvf are marked ``"Format":"gzjson"``. That can mean
several byte encodings; this tries each and reports which one yields valid Qlik
Engine JSON, dumping a few samples so we can see real measure/visual definitions.

Strategies tested:
  A. raw gzip stream      (bytes start 1f 8b 08)
  B. raw zlib stream      (bytes start 78 9c / da / 01)
  C. raw DEFLATE          (no header)
  D. base64 text -> gzip/zlib/deflate -> JSON   ("gzjson" = base64 of gzipped json)

Usage: python tools/diag_qvf.py "App.qvf"
"""
import base64
import json
import re
import sys
import zlib
from pathlib import Path


def try_inflate(b: bytes):
    for wbits in (47, -15, 15):
        try:
            d = zlib.decompressobj(wbits)
            out = d.decompress(b) + d.flush()
            if out:
                return out
        except (zlib.error, OverflowError):
            continue
    return None


def is_qlik_json(text: str) -> bool:
    return any(k in text for k in ("qInfo", "qMeasure", "qHyperCubeDef",
                                   "qDim", "qMetaDef", "qScript"))


def main(path: str):
    data = Path(path).read_bytes()
    print(f"{path}: {len(data) / 1e6:.2f} MB\n")
    counts = {"A_raw_gzip": 0, "B_raw_zlib": 0, "C_deflate": 0, "D_base64": 0}
    samples = []

    # A/B/C: raw compressed streams at signature offsets.
    for sig, label in ((b"\x1f\x8b\x08", "A_raw_gzip"),
                       (b"\x78\x9c", "B_raw_zlib"),
                       (b"\x78\xda", "B_raw_zlib"),
                       (b"\x78\x01", "B_raw_zlib")):
        start = 0
        while True:
            j = data.find(sig, start)
            if j < 0:
                break
            start = j + 1
            out = try_inflate(data[j:j + 5_000_000])
            if out and len(out) > 20:
                _record(out, label, j, counts, samples)

    # D: base64 text runs -> decode -> inflate -> JSON.
    for m in re.finditer(rb"[A-Za-z0-9+/]{120,}={0,2}", data):
        try:
            raw = base64.b64decode(m.group(), validate=False)
        except Exception:
            continue
        out = try_inflate(raw) or raw
        if out and len(out) > 20:
            _record(out, "D_base64", m.start(), counts, samples)

    print("decoded JSON objects per strategy:")
    for k, v in counts.items():
        print(f"  {k:<14} {v}")
    print(f"\n{'='*60}")
    if not samples:
        print("NO JSON recovered by any strategy -> payloads need a Qlik engine "
              "(see corectl unbuild). Pure-binary decode is not viable for this app.")
    else:
        print(f"RECOVERED {len(samples)} sample object(s) — binary decode IS viable.")
        for label, off, text in samples[:6]:
            print(f"\n--- {label} @ {off} ---\n{text[:400]}")


def _record(out: bytes, label, off, counts, samples):
    try:
        text = out.decode("utf-8")
        json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    counts[label] += 1
    if is_qlik_json(text) and len(samples) < 12:
        samples.append((label, off, text))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python tools/diag_qvf.py <file.qvf>")
        sys.exit(1)
    main(sys.argv[1])
