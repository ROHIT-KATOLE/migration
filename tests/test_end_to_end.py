"""End-to-end: mock QVF -> .pbip that passes post-generation validation."""
from generation.dax_converter import DAXConverter
from generation.semantic_model import SemanticModelGenerator
from generation.visual_builder import VisualBuilder
from generation.pbip_assembler import PBIPAssembler
from generation.validator import validate_project


def _column_table_map(app):
    m = {}
    for ds in app.datasources:
        for f in ds.fields:
            m.setdefault(f.name, ds.table_name)
    return m


def test_full_pipeline_validates(extracted_app, tmp_path):
    out = tmp_path / "test_app"
    measures = DAXConverter(extracted_app.measures,
                            _column_table_map(extracted_app)).convert_all()
    SemanticModelGenerator(extracted_app, measures).generate(out)
    pages = VisualBuilder(extracted_app).build_pages()
    pbip = PBIPAssembler("test_app", out).assemble(pages)

    result = validate_project(pbip)
    assert result.valid, f"validation errors: {result.errors}"
    assert result.checked > 0


def test_report_json_matches_pbir_schema(extracted_app, tmp_path):
    import json
    out = tmp_path / "test_app"
    measures = DAXConverter(extracted_app.measures).convert_all()
    SemanticModelGenerator(extracted_app, measures).generate(out)
    pages = VisualBuilder(extracted_app).build_pages()
    PBIPAssembler("test_app", out).assemble(pages)

    rj = json.loads((out / "test_app.Report" / "definition" / "report.json")
                    .read_text(encoding="utf-8"))
    assert "version" not in rj                       # forbidden by report/1.0.0
    assert rj["layoutOptimization"] == "None"
    assert rj["themeCollection"]["baseTheme"]["type"] == "SharedResources"
    for key in ("name", "reportVersionAtImport"):
        assert key in rj["themeCollection"]["baseTheme"]


def test_blank_page_when_no_visuals(tmp_path):
    # An app with no sheets/visuals still produces a valid one-page report.
    from extraction.schemas import ExtractedApp, AppMetadata, DataSource, Field
    app = ExtractedApp(
        metadata=AppMetadata(name="Empty"),
        datasources=[DataSource(table_name="T",
                                fields=[Field(name="A", data_type="string")])],
    )
    out = tmp_path / "Empty"
    SemanticModelGenerator(app, []).generate(out)
    pbip = PBIPAssembler("Empty", out).assemble(VisualBuilder(app).build_pages())
    result = validate_project(pbip)
    assert result.valid, result.errors
    assert (out / "Empty.Report" / "definition" / "pages" / "pages.json").exists()


def test_pbip_structure_complete(extracted_app, tmp_path):
    out = tmp_path / "test_app"
    measures = DAXConverter(extracted_app.measures).convert_all()
    SemanticModelGenerator(extracted_app, measures).generate(out)
    pages = VisualBuilder(extracted_app).build_pages()
    PBIPAssembler("test_app", out).assemble(pages)

    assert (out / "test_app.pbip").exists()
    assert (out / "test_app.Report" / "definition.pbir").exists()
    assert (out / "test_app.SemanticModel" / "definition" / "model.tmdl").exists()
    # report references the semantic model that exists
    import json
    pbir = json.loads((out / "test_app.Report" / "definition.pbir").read_text(encoding="utf-8"))
    assert pbir["datasetReference"]["byPath"]["path"] == "../test_app.SemanticModel"


def test_dataset_reference_breakage_is_caught(extracted_app, tmp_path):
    out = tmp_path / "test_app"
    measures = DAXConverter(extracted_app.measures).convert_all()
    SemanticModelGenerator(extracted_app, measures).generate(out)
    pages = VisualBuilder(extracted_app).build_pages()
    pbip = PBIPAssembler("test_app", out).assemble(pages)

    # Break the link and confirm the validator flags it.
    import shutil
    shutil.rmtree(out / "test_app.SemanticModel")
    result = validate_project(pbip)
    assert not result.valid
    assert any("SemanticModel" in e or "model" in e.lower() for e in result.errors)
