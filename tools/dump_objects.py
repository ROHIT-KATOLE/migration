"""Diagnose what the binary reader recovers from a .qvf — focused on the
sheets/visuals gap (Challenge A in docs/PROJECT_STATUS.md).

Runs the *same* reader the pipeline uses, then reports:
  * how many objects were recovered and their type histogram,
  * how many inflated blobs contain a visual (`qHyperCubeDef`), a sheet layout
    (`cells`), or a container (`qChildList`/`qChildren`) — even if they were
    classified as "unknown",
  * a per-object dump (id, type, top-level keys) to <App>_objects/.

The visual/sheet marker counts tell us which fix Challenge A needs:
  - markers > 0 but sheets/visuals classified 0  -> classification/flatten fix
  - markers == 0                                 -> inflation fix (try diag_qvf)

Usage: python tools/dump_objects.py "App.qvf"
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extraction.qvf_reader import QVFReader  # noqa: E402


def _markers(props: dict) -> set:
    blob = json.dumps(props)[:200000] if isinstance(props, dict) else ""
    found = set()
    for key in ("qHyperCubeDef", "qListObjectDef", "cells", "qChildList",
                "qChildren", "visualization"):
        if key in blob:
            found.add(key)
    return found


def main(path: str):
    raw = QVFReader(Path(path)).read()
    out_dir = Path(path).with_suffix("")
    out_dir = out_dir.parent / (out_dir.name + "_objects")
    out_dir.mkdir(exist_ok=True)

    print(f"\n=== {path} ===")
    print(f"app name: {raw.app.get('name')!r}")
    print(f"objects recovered: {len(raw.objects)} | scripts: {len(raw.scripts)}")

    types = Counter(o.qtype for o in raw.objects)
    print("\ntype histogram:")
    for t, n in types.most_common():
        print(f"  {t:<24} {n}")

    marker_counts = Counter()
    visual_like = []
    for o in raw.objects:
        ms = _markers(o.props or {})
        for m in ms:
            marker_counts[m] += 1
        if {"qHyperCubeDef", "qListObjectDef", "cells"} & ms:
            visual_like.append(o)

    print("\nmarker counts across recovered objects "
          "(these reveal hidden sheets/visuals):")
    for m, n in marker_counts.most_common():
        print(f"  {m:<20} {n}")

    print(f"\nobjects that look like a sheet/visual but may be mis-typed: "
          f"{len(visual_like)}")
    for o in visual_like[:15]:
        keys = list((o.props or {}).keys())[:8]
        print(f"  id={o.id!r} qtype={o.qtype!r} title={o.title!r} keys={keys}")

    # Full per-object dump for offline inspection.
    for i, o in enumerate(raw.objects):
        rec = {"id": o.id, "qtype": o.qtype, "title": o.title,
               "top_level_keys": list((o.props or {}).keys()),
               "props": o.props}
        (out_dir / f"{i:04d}_{o.qtype}.json").write_text(
            json.dumps(rec, indent=2, ensure_ascii=False)[:200000],
            encoding="utf-8")
    print(f"\nper-object dump -> {out_dir}")
    print("\nINTERPRETATION:")
    if marker_counts.get("qHyperCubeDef") or marker_counts.get("cells"):
        print("  Visuals/sheets ARE inside the file but mis-classified.")
        print("  -> Fix is in qvf_reader.py classification/flattening.")
    else:
        print("  No visual/sheet markers found in inflated objects.")
        print("  -> They aren't inflating; run tools/diag_qvf.py to find the")
        print("     right decode strategy, then widen qvf_reader's stream scan.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python tools/dump_objects.py <file.qvf>")
        sys.exit(1)
    main(sys.argv[1])
