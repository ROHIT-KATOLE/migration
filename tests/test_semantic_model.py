"""Semantic model: TMDL generation produces valid, parseable files."""
import pytest

from generation.dax_converter import DAXConverter
from generation.semantic_model import SemanticModelGenerator, _quote
from generation.m_query import generate_m_query
from extraction.schemas import DataSource, Field


@pytest.fixture
def generated_model(extracted_app, tmp_path):
    out = tmp_path / "MyApp"
    out.mkdir()
    col_map = {f.name: ds.table_name
               for ds in extracted_app.datasources for f in ds.fields}
    measures = DAXConverter(extracted_app.measures, col_map).convert_all()
    sm_dir = SemanticModelGenerator(extracted_app, measures).generate(out)
    return sm_dir


def test_core_tmdl_files_exist(generated_model):
    def_dir = generated_model / "definition"
    for name in ("model.tmdl", "database.tmdl", "relationships.tmdl"):
        assert (def_dir / name).exists()
    assert (def_dir / "tables" / "Calendar.tmdl").exists()
    assert (def_dir / "tables" / "Sales.tmdl").exists()


def test_platform_and_pbism(generated_model):
    assert (generated_model / ".platform").exists()
    assert (generated_model / "definition.pbism").exists()


def test_table_tmdl_well_formed(generated_model):
    text = (generated_model / "definition" / "tables" / "Sales.tmdl").read_text(encoding="utf-8")
    assert text.startswith("table Sales")
    assert "partition" in text
    assert "Snowflake.Databases" in text  # M query embedded
    assert "annotation PBI_ResultType = Table" in text


def test_measures_on_dedicated_table(generated_model):
    mt = (generated_model / "definition" / "tables" / "Key Measures.tmdl").read_text(encoding="utf-8")
    assert "measure 'Total Sales' = SUM('Sales'[Amount])" in mt
    assert "isHidden" in mt          # the dummy column is hidden
    # data tables must NOT carry measures
    sales = (generated_model / "definition" / "tables" / "Sales.tmdl").read_text(encoding="utf-8")
    assert "measure 'Total Sales'" not in sales


def test_quoting():
    assert _quote("Region") == "Region"
    assert _quote("Total Sales") == "'Total Sales'"


def test_relationships_generated(generated_model):
    rels = (generated_model / "definition" / "relationships.tmdl").read_text(encoding="utf-8")
    assert "relationship" in rels
    assert "fromColumn: Sales.ProductID" in rels


@pytest.mark.parametrize("source_type,needle", [
    ("snowflake", "Snowflake.Databases"),
    ("csv", "Csv.Document"),
    ("excel", "Excel.Workbook"),
    ("sqlserver", "Sql.Database"),
    ("qvd", "TODO"),
])
def test_m_query_connectors(source_type, needle):
    ds = DataSource(table_name="T",
                    fields=[Field(name="A", data_type="string")],
                    source_type=source_type, connection={})
    m = generate_m_query(ds)
    assert needle in m
    assert m.startswith("let")
