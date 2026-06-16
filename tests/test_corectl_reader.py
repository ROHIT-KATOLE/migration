"""corectl unbuild folder reader → ExtractedApp (the engine-backed path)."""
import json

import pytest

from tools.create_mock_unbuild import create_mock_unbuild
from extraction.corectl_reader import CorectlReader
from extraction.extractor import Extractor
from extraction.qvf_reader import QVFReadError


@pytest.fixture
def unbuild_dir(tmp_path):
    return create_mock_unbuild(str(tmp_path / "test_app-unbuild"))


@pytest.fixture
def corectl_app(unbuild_dir):
    return Extractor(CorectlReader(unbuild_dir).read()).extract()


def test_reads_objects_from_folder(unbuild_dir):
    raw = CorectlReader(unbuild_dir).read()
    assert len(raw.by_type("measure")) == 7
    assert len(raw.by_type("dimension")) == 5
    assert len(raw.by_type("sheet")) == 3
    assert raw.scripts


def test_flattens_sheet_child_visuals(unbuild_dir):
    raw = CorectlReader(unbuild_dir).read()
    visuals = [o for o in raw.objects if o.qtype in ("barchart", "linechart",
               "piechart", "table", "pivot-table", "kpi", "gauge", "filterpane")]
    assert len(visuals) == 10            # children pulled out of sheet trees


def test_maps_to_same_contract_as_binary(corectl_app):
    # Same app content as the binary mock -> same extracted shape.
    assert corectl_app.metadata.name == "Global SIOP Dashboard"
    assert len(corectl_app.datasources) == 3
    assert {d.table_name for d in corectl_app.datasources} == \
        {"Sales", "Product", "Customer"}
    assert len(corectl_app.visualizations) == 10
    assert any(m.name == "Inline Avg Price" for m in corectl_app.measures)


def test_source_types_from_script(corectl_app):
    by = {d.table_name: d for d in corectl_app.datasources}
    assert by["Sales"].source_type == "snowflake"
    assert by["Customer"].source_type == "csv"


def test_app_name_from_folder_when_no_config(tmp_path):
    d = tmp_path / "My Report-unbuild" / "src"
    d.mkdir(parents=True)
    (d / "measure-x.json").write_text(json.dumps(
        {"qInfo": {"qId": "x", "qType": "measure"},
         "qMeasure": {"qDef": "=Sum(Sales)", "qLabel": "S"}}), encoding="utf-8")
    raw = CorectlReader(tmp_path / "My Report-unbuild").read()
    assert raw.app["name"] == "My Report"


def test_empty_folder_raises(tmp_path):
    empty = tmp_path / "empty-unbuild"
    empty.mkdir()
    with pytest.raises(QVFReadError):
        CorectlReader(empty).read()
