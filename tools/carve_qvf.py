"""Inflate the compressed object payloads inside a .qvf and dump them as JSON.

Diagnostic companion to the binary reader: decompresses every gzip/zlib stream,
parses each as JSON, classifies it (measure / dimension / sheet / visualization
/ loadmodel), and writes one ``.json`` per object into ``<name>_carved/``. Use it
to inspect exactly what an app contains, or to debug why a particular object
isn't being mapped.

Usage: python tools/carve_qvf.py "App.qvf"
"""
import json
import sys
import zlib
from pathlib import Path

SIGS = (b"\x1f\x8b\x08", b"\x78\x9c", b"\x78\xda", b"\x78\x01")


def classify(text: str) -> str:
    if "qHyperCubeDef" in text:
        return "visualization"
    if '"qType":"measure"' in text or "qMeasure" in text:
        return "measure"
    if '"qType":"dimension"' in text or "qDim" in text:
        return "dimension"
    if '"qType":"sheet"' in text or "cells" in text:
        return "sheet"
    if "qScript" in text or "LoadModel" in text:
        return "loadmodel"
    return "other"


def main(path: str):
    data = Path(path).read_bytes()
    out_dir = Path(path).parent / (Path(path).stem + "_carved")
    out_dir.mkdir(exist_ok=True)

    offsets = sorted({i for sig in SIGS
                      for i in _find_all(data, sig)})
    found = parsed = 0
    types: dict = {}
    for off in offsets:
        try:
            d = zlib.decompressobj(47)
            raw = d.decompress(data[off:off + 64_000_000], 64_000_000)
            raw += d.flush()
        except (zlib.error, OverflowError):
            continue
        if len(raw) < 16:
            continue
        try:
            text = raw.decode("utf-8")
            obj = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        found += 1
        parsed += 1
        t = classify(text[:600])
        types[t] = types.get(t, 0) + 1
        (out_dir / f"{off:08x}_{t}.json").write_text(
            json.dumps(obj, indent=2, ensure_ascii=False)[:300_000],
            encoding="utf-8")

    print(f"{path}: {len(offsets)} candidate streams, {parsed} JSON objects")
    print("types:", types)
    print("written ->", out_dir)


def _find_all(data: bytes, sig: bytes):
    i = 0
    while True:
        j = data.find(sig, i)
        if j == -1:
            return
        yield j
        i = j + 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python tools/carve_qvf.py <file.qvf>")
        sys.exit(1)
    main(sys.argv[1])
