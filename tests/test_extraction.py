"""Extraction layer: parsing the real Qlik container format into the contract."""
import gzip
import json

import pytest

from extraction.qvf_reader import QVFReader, QVFReadError
from extraction.extractor import Extractor, ExtractionError, map_qlik_type, merge_apps
from extraction.schemas import ExtractedApp, JSON_FILES


# ── reader ───────────────────────────────────────────────────────────

def test_reader_recovers_objects(raw_app):
    assert raw_app.app["name"] == "Global SIOP Dashboard"
    assert len(raw_app.by_type("measure")) == 7
    assert len(raw_app.by_type("dimension")) == 5
    assert len(raw_app.by_type("sheet")) == 3
    assert raw_app.scripts, "load script should be recovered"


def test_reader_rejects_unreadable_file(tmp_path):
    bogus = tmp_path / "encrypted.qvf"
    bogus.write_bytes(b"\x00\x01 random bytes that are not a qlik app \xff" * 4)
    with pytest.raises(QVFReadError):
        QVFReader(bogus).read()


def test_explode_unwraps_qroot_sheet_tree():
    # Real apps wrap a sheet + its charts as {"qMetaData", "qRoot": {...}}.
    from extraction.qvf_reader import _explode, _payload_qtype
    obj = {
        "qMetaData": {"title": "Overview"},
        "qRoot": {
            "qProperty": {
                "qInfo": {"qId": "SH1", "qType": "sheet"},
                "cells": [{"name": "V1", "col": 0, "row": 0,
                           "colspan": 12, "rowspan": 8}],
            },
            "qChildren": [
                {"qProperty": {
                    "qInfo": {"qId": "V1", "qType": "barchart"},
                    "visualization": "barchart",
                    "qHyperCubeDef": {"qDimensions": [{"qLibraryId": "D1"}],
                                      "qMeasures": [{"qLibraryId": "M1"}]},
                }},
            ],
        },
    }
    types = [_payload_qtype(o) for o in _explode(obj)]
    assert "sheet" in types
    assert "barchart" in types


def test_safe_name_rejects_expressions():
    from extraction.extractor import _safe_name
    assert _safe_name("Total Sales", "fb") == "Total Sales"
    assert _safe_name("Unconstraint ASP (Valid)", "fb") == "Unconstraint ASP (Valid)"
    assert _safe_name("=Sum(X)", "fb") == "fb"          # starts with '='
    assert _safe_name("sum({<Y={2023}>} X)", "fb") == "fb"   # set analysis
    assert _safe_name("x" * 200, "fb") == "fb"          # too long
    assert _safe_name("a\nb", "fb") == "a b"            # newline collapsed, kept


def test_inline_multiline_measure_gets_clean_tmdl_safe_name():
    from extraction.qvf_reader import RawApp, QlikObject
    expr = "Sum({<Year={2023}>}\n   Amount\n   * 2)"
    viz = QlikObject(
        id="V1", qtype="barchart", title="Chart",
        props={"qInfo": {"qId": "V1", "qType": "barchart"},
               "visualization": "barchart",
               "qHyperCubeDef": {"qDimensions": [],
                                 "qMeasures": [{"qDef": {"qDef": "=" + expr,
                                                         "qLabel": ""}}]}})
    app = Extractor(RawApp(app={"name": "A"}, objects=[viz], scripts=[])).extract()
    assert app.measures, "inline measure should enter the model"
    for m in app.measures:
        assert "\n" not in m.name and not m.name.startswith("=")
        assert "'" not in m.name and "{" not in m.name


def test_app_name_recovered_from_layout_payload():
    from extraction.qvf_reader import _app_from_payloads, _is_app_props
    payload = {"qTitle": "Global SIOP Dashboard",
               "qLastReloadTime": "2026-04-29T05:44:21Z",
               "description": "S&OP overview"}
    assert _is_app_props(payload)
    app = _app_from_payloads([payload])
    assert app["name"] == "Global SIOP Dashboard"
    assert app["description"] == "S&OP overview"


def test_reader_inflates_gzjson(raw_app):
    # A measure payload was gzip-compressed Engine JSON; confirm qDef survived.
    measure = next(o for o in raw_app.by_type("measure")
                   if o.title == "Total Sales")
    assert measure.props["qMeasure"]["qDef"] == "=Sum(Amount)"


# ── extractor / mapping ──────────────────────────────────────────────

def test_extract_produces_valid_app(extracted_app):
    assert isinstance(extracted_app, ExtractedApp)
    assert extracted_app.metadata.name == "Global SIOP Dashboard"
    assert len(extracted_app.datasources) == 3
    assert {m.name for m in extracted_app.measures} >= {"Total Sales", "Sales 2023"}


def test_tables_and_source_types_from_script(extracted_app):
    by_name = {d.table_name: d for d in extracted_app.datasources}
    assert by_name["Sales"].source_type == "snowflake"
    assert by_name["Customer"].source_type == "csv"
    assert len(by_name["Sales"].fields) == 7


def test_associations_inferred_from_shared_fields(extracted_app):
    pairs = {(a.from_table, a.to_table, a.from_field)
             for a in extracted_app.associations}
    assert ("Sales", "Customer", "CustomerID") in pairs
    assert ("Sales", "Product", "ProductID") in pairs


def test_master_measure_expression_recovered(extracted_app):
    m = next(m for m in extracted_app.measures if m.name == "Total Sales")
    assert m.expression == "Sum(Amount)"          # leading '=' stripped
    assert m.format_string == "#,##0.00"


def test_inline_visual_measure_added_to_model(extracted_app):
    # A visual defined a measure inline (no master item); it must enter the model.
    assert any(m.name == "Inline Avg Price" and m.expression == "Avg(Price)"
               for m in extracted_app.measures)


def test_visual_resolves_master_refs(extracted_app):
    bar = next(v for v in extracted_app.visualizations if v.id == "V_BAR")
    assert bar.dimensions == ["Region"]
    assert bar.measures == ["Total Sales"]
    assert bar.sheet_id == "SH_OVR"


def test_type_mapping():
    assert map_qlik_type("integer") == "integer"
    assert map_qlik_type("timestamp") == "dateTime"
    assert map_qlik_type(None) == "string"
    assert map_qlik_type("weird") == "string"


def test_write_json_emits_eight_files(raw_app, tmp_path):
    out = tmp_path / "extracted"
    Extractor(raw_app).write_json(out)
    for filename in JSON_FILES.values():
        assert (out / filename).exists()
        json.loads((out / filename).read_text(encoding="utf-8"))


def test_merge_layered_apps(raw_app):
    app = Extractor(raw_app).extract()
    merged = merge_apps([app, app], "Merged")
    # de-duplicated by natural key / id, not doubled
    assert len(merged.measures) == len(app.measures)
    assert len(merged.datasources) == len(app.datasources)
    assert len(merged.sheets) == len(app.sheets)
    assert len(merged.visualizations) == len(app.visualizations)
    assert merged.metadata.name == "Merged"


def test_merge_unions_distinct_apps(raw_app):
    from extraction.schemas import ExtractedApp, AppMetadata, Sheet
    a = Extractor(raw_app).extract()
    b = ExtractedApp(metadata=AppMetadata(name="B"),
                     sheets=[Sheet(id="OTHER", name="Extra", rank=9)])
    merged = merge_apps([a, b], "Merged")
    assert len(merged.sheets) == len(a.sheets) + 1   # distinct id added


def test_fallback_data_model_when_no_script():
    # An app with master items but no recoverable script still validates.
    from extraction.qvf_reader import RawApp, QlikObject
    raw = RawApp(
        app={"name": "No Script App"},
        objects=[
            QlikObject(id="m1", qtype="measure", title="Total",
                       props={"qInfo": {"qId": "m1"},
                              "qMeasure": {"qDef": "=Sum(Revenue)"}}),
            QlikObject(id="d1", qtype="dimension", title="Region",
                       props={"qInfo": {"qId": "d1"},
                              "qDim": {"qFieldDefs": ["Region"]}}),
        ],
        scripts=[],
    )
    app = Extractor(raw).extract()
    assert len(app.datasources) == 1
    cols = {f.name for f in app.datasources[0].fields}
    assert "Revenue" in cols and "Region" in cols
