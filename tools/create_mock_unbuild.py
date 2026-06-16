"""Create a mock ``corectl unbuild`` folder for testing the corectl pipeline.

Mirrors what ``corectl unbuild`` emits against a real engine: per-object JSON
files (measures, dimensions), sheet objects carrying their child charts in a
``qChildren`` tree, a ``script.qvs`` and a ``corectl.yml``. Reuses the same app
content as ``create_mock_qvf`` so both extraction paths converge on the same
ExtractedApp.

Usage: python tools/create_mock_unbuild.py [output_dir]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running as a standalone script (python tools/create_mock_unbuild.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.create_mock_qvf import (  # noqa: E402 - sibling fixture data
    APP, MEASURES, DIMENSIONS, SHEETS, VISUALS, LOAD_SCRIPT,
)


def _measure_obj(mid, title, expr, fmt):
    return {"qInfo": {"qId": mid, "qType": "measure"},
            "qMeasure": {"qLabel": title, "qDef": expr,
                         "qNumFormat": {"qType": "F", "qFmt": fmt}},
            "qMetaDef": {"title": title}}


def _dimension_obj(did, name, field):
    return {"qInfo": {"qId": did, "qType": "dimension"},
            "qDim": {"qGrouping": "N", "qFieldDefs": [field],
                     "qFieldLabels": [name]},
            "qMetaDef": {"title": name}}


def _visual_child(vid, vtype, title, dim_ids, mea_ids, inline):
    qdims = [{"qLibraryId": d, "qDef": {"qFieldDefs": []}} for d in dim_ids]
    qmeas = [{"qLibraryId": m, "qDef": {}} for m in mea_ids]
    if inline:
        qmeas.append({"qDef": {"qLabel": inline[0], "qDef": inline[1]}})
    return {"qInfo": {"qId": vid, "qType": vtype},
            "visualization": vtype,
            "qMetaDef": {"title": title},
            "qHyperCubeDef": {"qDimensions": qdims, "qMeasures": qmeas}}


def _sheet_obj(sid, name, rank):
    cells, children = [], []
    for (vid, vtype, title, sheet, dim_ids, mea_ids, inline,
         col, row, cspan, rspan) in VISUALS:
        if sheet != sid:
            continue
        cells.append({"name": vid, "col": col, "row": row,
                      "colspan": cspan, "rowspan": rspan})
        children.append(_visual_child(vid, vtype, title, dim_ids, mea_ids, inline))
    return {"qInfo": {"qId": sid, "qType": "sheet"},
            "qMetaDef": {"title": name}, "rank": rank,
            "cells": cells, "qChildren": children}


def create_mock_unbuild(output_dir: str = "test_app-unbuild") -> Path:
    root = Path(output_dir)
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)

    for mid, title, expr, fmt in MEASURES:
        _write(src / f"measure-{mid}.json", _measure_obj(mid, title, expr, fmt))
    for did, name, field in DIMENSIONS:
        _write(src / f"dimension-{did}.json", _dimension_obj(did, name, field))
    for sid, name, rank in SHEETS:
        _write(src / f"object-{sid}.json", _sheet_obj(sid, name, rank))

    (src / "script.qvs").write_text(LOAD_SCRIPT, encoding="utf-8")
    (root / "corectl.yml").write_text(
        f"app: {APP['name']}.qvf\nengine: localhost:9076\n"
        "script: src/script.qvs\n", encoding="utf-8")
    return root


def _write(path: Path, obj: dict):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "test_app-unbuild"
    print(f"Created mock corectl unbuild folder: {create_mock_unbuild(out).resolve()}")
