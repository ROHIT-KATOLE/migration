"""Visual builder: every supported type produces a schema-valid PBIR visual."""
import pytest

from extraction.schemas import Visualization
from generation.visual_builder import VisualBuilder, validate_visual_json
from generation.visual_schemas import resolve_visual_type


@pytest.fixture
def builder(extracted_app):
    return VisualBuilder(extracted_app)


def _viz(vtype, dims, meas):
    return Visualization(id=f"id_{vtype}", type=vtype, title="T",
                         sheet_id="s", dimensions=dims, measures=meas)


@pytest.mark.parametrize("qlik_type,pbi_type", [
    ("barchart", "clusteredBarChart"),
    ("columnchart", "clusteredColumnChart"),
    ("linechart", "lineChart"),
    ("piechart", "pieChart"),
    ("donutchart", "donutChart"),
    ("table", "tableEx"),
    ("matrix", "matrix"),
    ("kpi", "card"),
    ("gauge", "kpi"),
    ("filterpane", "slicer"),
])
def test_each_type_builds_valid_visual(builder, qlik_type, pbi_type):
    viz = _viz(qlik_type, ["Region"], ["Total Sales"])
    visual = builder.build_visual(viz)
    assert visual["visual"]["visualType"] == pbi_type
    validate_visual_json(visual)  # must not raise


def test_unknown_type_becomes_placeholder(builder):
    viz = _viz("sankey-diagram-3000", ["Region"], ["Total Sales"])
    visual = builder.build_visual(viz)
    assert visual["visual"]["visualType"] == "textbox"
    validate_visual_json(visual)


def test_resolve_visual_type_unknown_is_none():
    assert resolve_visual_type("not-a-real-type") is None


def test_table_puts_all_fields_in_values(builder):
    viz = _viz("table", ["Region"], ["Total Sales", "Max Order"])
    state = builder.build_visual(viz)["visual"]["query"]["queryState"]
    assert "Values" in state
    assert len(state["Values"]["projections"]) == 3


def test_matrix_splits_rows_and_columns(builder):
    viz = _viz("matrix", ["Region", "Category"], ["Total Sales"])
    state = builder.build_visual(viz)["visual"]["query"]["queryState"]
    assert state["Rows"]["projections"][0]["nativeQueryRef"] == "Region"
    assert state["Columns"]["projections"][0]["nativeQueryRef"] == "Category"
    assert state["Values"]["projections"][0]["nativeQueryRef"] == "Total Sales"


def test_measure_projection_points_at_measures_table(builder):
    viz = _viz("barchart", ["Region"], ["Total Sales"])
    state = builder.build_visual(viz)["visual"]["query"]["queryState"]
    proj = state["Y"]["projections"][0]
    assert proj["field"]["Measure"]["Expression"]["SourceRef"]["Entity"] == "Key Measures"


def test_validate_rejects_missing_keys():
    with pytest.raises(ValueError):
        validate_visual_json({"name": "x", "position": {}})


def test_build_pages_groups_by_sheet(builder):
    pages = builder.build_pages()
    assert len(pages) == 3
    total = sum(len(p["visuals"]) for p in pages)
    assert total == 10
    # ordered by sheet rank
    assert [p["displayName"] for p in pages] == \
        ["Overview", "Sales Detail", "Customer Analysis"]
