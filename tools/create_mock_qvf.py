"""Create a mock ``.qvf`` in the *real* Qlik Sense container format for testing.

Mirrors the structure of a genuine app (verified against the strings dump of a
production file): a sequence of entries, each a plaintext JSON header carrying a
base64 ``SecurityMetaAsBase64`` blob, followed by the object payload as
**gzip-compressed Qlik Engine JSON** (``qInfo`` / ``qMeasure`` / ``qDim`` /
``qHyperCubeDef`` / sheet ``cells``), plus a gzip'd load script.

This is what `extraction/qvf_reader.py` parses, so the test suite exercises the
true format rather than a SQLite stand-in.

Usage: python tools/create_mock_qvf.py [output_path]
"""
from __future__ import annotations

import base64
import gzip
import json
import sys
from pathlib import Path

# ── app content ──────────────────────────────────────────────────────

APP = {"id": "siop-app", "name": "Global SIOP Dashboard",
       "description": "Sales, Inventory & Operations Planning overview"}

# (id, title, qlik_expression, qFmt)
MEASURES = [
    ("MEA_TS", "Total Sales", "=Sum(Amount)", "#,##0.00"),
    ("MEA_TQ", "Total Quantity", "=Sum(Quantity)", "#,##0"),
    ("MEA_AOV", "Avg Order Value", "=Avg(Amount)", "#,##0.00"),
    ("MEA_OC", "Order Count", "=Count(OrderID)", "#,##0"),
    ("MEA_DC", "Distinct Customers", "=CountDistinct(CustomerID)", "#,##0"),
    ("MEA_MAX", "Max Order", "=Max(Amount)", "#,##0.00"),
    # Set analysis -> must fall back during DAX conversion.
    ("MEA_S23", "Sales 2023", "=Sum({<Year={2023}>} Amount)", "#,##0.00"),
]

# (id, title, field)
DIMENSIONS = [
    ("DIM_REG", "Region", "Region"),
    ("DIM_CAT", "Category", "Category"),
    ("DIM_CTRY", "Country", "Country"),
    ("DIM_SEG", "Segment", "Segment"),
    ("DIM_PROD", "Product Name", "ProductName"),
]

# (id, name, rank)
SHEETS = [
    ("SH_OVR", "Overview", 0),
    ("SH_SALES", "Sales Detail", 1),
    ("SH_CUST", "Customer Analysis", 2),
]

# (id, qlik_type, title, sheet, [dim master ids], [measure master ids],
#  inline_measure or None, col,row,colspan,rowspan)
VISUALS = [
    ("V_BAR", "barchart", "Sales by Region", "SH_OVR",
     ["DIM_REG"], ["MEA_TS"], None, 0, 0, 12, 8),
    ("V_LINE", "linechart", "Orders by Country", "SH_OVR",
     ["DIM_CTRY"], ["MEA_OC"], None, 12, 0, 12, 8),
    ("V_PIE", "piechart", "Sales by Segment", "SH_OVR",
     ["DIM_SEG"], ["MEA_TS"], None, 0, 8, 12, 8),
    ("V_KPI", "kpi", "Total Sales", "SH_OVR",
     [], ["MEA_TS"], None, 12, 8, 6, 4),
    ("V_GAUGE", "gauge", "Avg Order Value", "SH_OVR",
     [], ["MEA_AOV"], None, 18, 8, 6, 4),
    ("V_TABLE", "table", "Product Detail", "SH_SALES",
     ["DIM_PROD"], ["MEA_TS", "MEA_MAX"], None, 0, 0, 12, 10),
    ("V_PIVOT", "pivot-table", "Region x Category", "SH_SALES",
     ["DIM_REG", "DIM_CAT"], ["MEA_TS"], None, 12, 0, 12, 10),
    # Inline measure (no master item) to exercise that path.
    ("V_BAR2", "barchart", "Avg by Category", "SH_SALES",
     ["DIM_CAT"], [], ("Inline Avg Price", "=Avg(Price)"), 0, 10, 24, 8),
    ("V_SLICER", "filterpane", "Filter: Country", "SH_CUST",
     ["DIM_CTRY"], [], None, 0, 0, 6, 16),
    ("V_CUST", "barchart", "Customers by Country", "SH_CUST",
     ["DIM_CTRY"], ["MEA_DC"], None, 6, 0, 18, 16),
]

LOAD_SCRIPT = """// Global SIOP Dashboard load script
LIB CONNECT TO 'Snowflake_SIOP';

Sales:
LOAD
    OrderID,
    CustomerID,
    ProductID,
    OrderDate,
    Amount,
    Quantity,
    Region
;
SQL SELECT OrderID, CustomerID, ProductID, OrderDate, Amount, Quantity, Region
FROM SIOP_DB.PUBLIC.SALES;

Product:
LOAD
    ProductID,
    ProductName,
    Category,
    Price
;
SQL SELECT ProductID, ProductName, Category, Price
FROM SIOP_DB.PUBLIC.PRODUCT;

Customer:
LOAD
    CustomerID,
    CustomerName,
    Country,
    Segment
FROM [lib://DataFiles/customers.csv]
(txt, codepage is 28591, embedded labels, delimiter is ',', msq);
"""


# ── container writer ─────────────────────────────────────────────────

def _entry(buf: bytearray, obj_id: str, type_str: str, meta: dict, payload):
    meta_b64 = base64.b64encode(
        json.dumps(meta).encode("utf-8") + b"\x00").decode("ascii")
    header = {
        "ContentHash": "0" * 44,
        "Format": "gzjson",
        "ParentId": obj_id,
        "Type": type_str,
        "SharedStatus": "Published",
        "SecurityMetaAsBase64": meta_b64,
        "IsTemporary": False,
    }
    buf += obj_id.encode("utf-8") + b"\x00"
    buf += json.dumps(header).encode("utf-8") + b"\x00"
    if payload is not None:
        buf += gzip.compress(json.dumps(payload).encode("utf-8")) + b"\x00\x00"


def create_mock_qvf(output_path: str = "test_app.qvf") -> Path:
    buf = bytearray(b"\xff\xff\x01\x00")  # plausible binary header prefix

    # App properties.
    _entry(buf, "siop-app", "NxAppProperties",
           {"_objecttype": "app", "_resourcetype": "app",
            "id": "siop-app", "name": APP["name"],
            "description": APP["description"]},
           {"qInfo": {"qId": "siop-app", "qType": "appprops"}})

    # Load script (recovered via the qScript field).
    _entry(buf, "LoadModel", "GenericAppObjectEntry",
           {"_objecttype": "LoadModel", "id": "LoadModel"},
           {"qInfo": {"qId": "LoadModel", "qType": "LoadModel"},
            "qScript": LOAD_SCRIPT})

    # Master measures.
    for mid, title, expr, fmt in MEASURES:
        _entry(buf, mid, "GenericMeasureProperties",
               {"_objecttype": "measure", "id": mid, "title": title},
               {"qInfo": {"qId": mid, "qType": "measure"},
                "qMeasure": {"qLabel": title, "qDef": expr,
                             "qNumFormat": {"qType": "F", "qFmt": fmt}},
                "qMetaDef": {"title": title}})

    # Master dimensions.
    for did, name, field in DIMENSIONS:
        _entry(buf, did, "GenericDimensionProperties",
               {"_objecttype": "dimension", "id": did, "title": name},
               {"qInfo": {"qId": did, "qType": "dimension"},
                "qDim": {"qGrouping": "N", "qFieldDefs": [field],
                         "qFieldLabels": [name]},
                "qMetaDef": {"title": name}})

    # Sheets (cells reference the visuals by id).
    sheet_cells = {sid: [] for sid, _, _ in SHEETS}
    for vid, _vt, _t, sheet, _d, _m, _inl, col, row, cspan, rspan in VISUALS:
        sheet_cells[sheet].append(
            {"name": vid, "col": col, "row": row,
             "colspan": cspan, "rowspan": rspan})
    for sid, name, rank in SHEETS:
        _entry(buf, sid, "GenericObjectProperties",
               {"_objecttype": "sheet", "id": sid, "title": name},
               {"qInfo": {"qId": sid, "qType": "sheet"},
                "qMetaDef": {"title": name}, "rank": rank,
                "cells": sheet_cells[sid]})

    # Visualizations.
    for (vid, vtype, title, _sheet, dim_ids, mea_ids, inline,
         _c, _r, _cs, _rs) in VISUALS:
        qdims = [{"qLibraryId": d, "qDef": {"qFieldDefs": []}} for d in dim_ids]
        qmeas = [{"qLibraryId": m, "qDef": {}} for m in mea_ids]
        if inline:
            qmeas.append({"qDef": {"qLabel": inline[0], "qDef": inline[1]}})
        _entry(buf, vid, "GenericObjectProperties",
               {"_objecttype": vtype, "id": vid, "title": title},
               {"qInfo": {"qId": vid, "qType": vtype},
                "visualization": vtype,
                "qMetaDef": {"title": title},
                "qHyperCubeDef": {"qDimensions": qdims, "qMeasures": qmeas}})

    path = Path(output_path)
    path.write_bytes(bytes(buf))
    return path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "test_app.qvf"
    print(f"Created mock QVF (real container format): {create_mock_qvf(out).resolve()}")
