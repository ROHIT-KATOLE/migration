"""Every harvested DAX mapping must pass its own embedded test case."""
import pytest

from generation.dax_mappings import DAX_MAPPINGS
from generation.dax_converter import DAXConverter


@pytest.mark.parametrize(
    "qlik,dax",
    [v["test"] for v in DAX_MAPPINGS.values()],
    ids=list(DAX_MAPPINGS.keys()),
)
def test_harvested_mappings(qlik, dax):
    result = DAXConverter([{"name": "m", "expression": qlik}]).convert_all()
    assert result[0]["status"] == "converted"
    assert result[0]["dax_expression"] == dax


def test_set_analysis_is_fallback():
    r = DAXConverter([{"name": "m", "expression": "Sum({<Year={2023}>} Amount)"}]).convert_all()
    assert r[0]["status"] == "fallback"
    assert "TODO" in r[0]["dax_expression"]
    assert "Sum({<Year={2023}>} Amount)" in r[0]["dax_expression"]


def test_dollar_expansion_is_fallback():
    r = DAXConverter([{"name": "m", "expression": "Sum($(vSales))"}]).convert_all()
    assert r[0]["status"] == "fallback"


def test_aggr_is_fallback():
    r = DAXConverter([{"name": "m", "expression": "Aggr(Sum(Sales), Customer)"}]).convert_all()
    assert r[0]["status"] == "fallback"


def test_unknown_function_is_fallback():
    r = DAXConverter([{"name": "m", "expression": "FooBar(Sales)"}]).convert_all()
    assert r[0]["status"] == "fallback"


def test_empty_expression_is_fallback():
    r = DAXConverter([{"name": "m", "expression": ""}]).convert_all()
    assert r[0]["status"] == "fallback"


def test_column_table_map_qualifies_with_real_table():
    r = DAXConverter(
        [{"name": "m", "expression": "Sum(Amount)"}],
        column_table_map={"Amount": "Sales"},
    ).convert_all()
    assert r[0]["dax_expression"] == "SUM('Sales'[Amount])"


def test_bracketed_field_name_with_spaces():
    # Qlik [Field With Spaces] must stay one column, not split into words.
    r = DAXConverter([{"name": "m", "expression": "Sum([Net Backorder Qty])"}]).convert_all()
    assert r[0]["status"] == "converted"
    assert r[0]["dax_expression"] == "SUM('T'[Net Backorder Qty])"


def test_bracketed_field_qualified_with_real_table():
    r = DAXConverter(
        [{"name": "m", "expression": "Sum([Net Backorder Qty])"}],
        column_table_map={"Net Backorder Qty": "Orders"},
    ).convert_all()
    assert r[0]["dax_expression"] == "SUM('Orders'[Net Backorder Qty])"


def test_and_or_operators_convert():
    r = DAXConverter(
        [{"name": "m", "expression": "If([A] > 0 and [B] < 5, [A], 0)"}]
    ).convert_all()
    assert r[0]["status"] == "converted"
    assert "&&" in r[0]["dax_expression"]
    assert "'T'[A]" in r[0]["dax_expression"]
    assert "'T'[and]" not in r[0]["dax_expression"]


def test_counts_are_tracked():
    conv = DAXConverter([
        {"name": "a", "expression": "Sum(Amount)"},
        {"name": "b", "expression": "Sum({<Y={2023}>} Amount)"},
        {"name": "c", "expression": "FooBar(X)"},
    ])
    conv.convert_all()
    assert conv.converted_count == 1
    assert conv.fallback_count == 2
    assert conv.failed_count == 0
