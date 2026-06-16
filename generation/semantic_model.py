"""Generate the ``<App>.SemanticModel`` folder in TMDL format.

Emits ``.platform``, ``definition.pbism`` and a ``definition/`` tree containing
``database.tmdl``, ``model.tmdl``, ``relationships.tmdl`` and one ``.tmdl`` per
table (plus an auto-generated ``Calendar`` date table). The TMDL syntax is
harvested from cyphou/Qlik-To-PowerBI (powerbi_import/tmdl_generator.py) and
verified against a real Power BI Desktop TMDL export.

All measures are placed on the fact table (the datasource with the most fields),
matching how Desktop organises model-level measures.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Dict, List

from extraction.schemas import DataSource, ExtractedApp, Measure
from .dax_mappings import dax_format_string
from .m_query import generate_m_query
from .visual_schemas import MEASURES_TABLE

log = logging.getLogger("migrate.tmdl")

_DATATYPE = {
    "string": "string",
    "integer": "int64",
    "double": "double",
    "dateTime": "dateTime",
    "boolean": "boolean",
}

# Deterministic namespace so lineage tags are stable across runs (no Date.now()).
_NS = uuid.UUID("12345678-1234-5678-1234-567812345678")


def _quote(name: str) -> str:
    """Quote a TMDL object name when it contains anything but word characters."""
    return f"'{name}'" if re.search(r"[^A-Za-z0-9_]", name or "") else name


def _tag(seed: str) -> str:
    return str(uuid.uuid5(_NS, seed))


class SemanticModelGenerator:
    def __init__(self, extracted: ExtractedApp, measures: List[dict]):
        self.app = extracted
        self.measures = measures  # converted DAX results

    def generate(self, out_dir: Path) -> Path:
        out = Path(out_dir)
        project = out.name
        sm_dir = out / f"{project}.SemanticModel"
        def_dir = sm_dir / "definition"
        tables_dir = def_dir / "tables"
        tables_dir.mkdir(parents=True, exist_ok=True)

        self._write_platform(sm_dir, project)
        self._write_pbism(sm_dir)

        table_names: List[str] = []

        # Data tables carry columns only — never measures (avoids name clashes).
        for ds in self.app.datasources:
            self._write_table(tables_dir, ds, measures=[])
            table_names.append(ds.table_name)

        # All measures live on one dedicated, column-free table.
        self._write_measures_table(tables_dir)
        table_names.append(MEASURES_TABLE)

        # Auto Calendar date table.
        self._write_calendar(tables_dir)
        table_names.append("Calendar")

        self._write_database(def_dir)
        self._write_model(def_dir, table_names)
        self._write_relationships(def_dir)

        log.info("TMDL: %d tables (+Calendar), %d measures, %d relationships",
                 len(self.app.datasources), len(self._converted_measures()),
                 len(self.app.associations))
        return sm_dir

    # ── helpers ──────────────────────────────────────────────────────

    def _fact_table_name(self) -> str | None:
        if not self.app.datasources:
            return None
        return max(self.app.datasources, key=lambda d: len(d.fields)).table_name

    def _converted_measures(self) -> List[dict]:
        return list(self.measures)

    def _measure_format(self, name: str) -> str | None:
        for m in self.app.measures:
            if m.name == name:
                return dax_format_string(m.format_string)
        return None

    # ── file writers ─────────────────────────────────────────────────

    @staticmethod
    def _write_platform(sm_dir: Path, project: str):
        platform = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                       "gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": "SemanticModel", "displayName": project},
            "config": {"version": "2.0", "logicalId": _tag(f"sm:{project}")},
        }
        _write_json(sm_dir / ".platform", platform)

    @staticmethod
    def _write_pbism(sm_dir: Path):
        pbism = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                       "item/semanticModel/definitionProperties/1.0.0/schema.json",
            "version": "4.2",
            "settings": {},
        }
        _write_json(sm_dir / "definition.pbism", pbism)

    @staticmethod
    def _write_database(def_dir: Path):
        _write_text(def_dir / "database.tmdl",
                    "database\n\tcompatibilityLevel: 1567\n")

    def _write_model(self, def_dir: Path, table_names: List[str]):
        culture = "en-US"
        lines = [
            "model Model",
            f"\tculture: {culture}",
            "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
            "\tsourceQueryCulture: en-US",
            "\tdataAccessOptions",
            "\t\tlegacyRedirects",
            "\t\treturnErrorValuesAsNull",
            "",
            f"annotation PBI_QueryOrder = {json.dumps(table_names)}",
            "",
        ]
        for tname in table_names:
            lines.append(f"ref table {_quote(tname)}")
        lines.append("")
        _write_text(def_dir / "model.tmdl", "\n".join(lines) + "\n")

    def _write_table(self, tables_dir: Path, ds: DataSource, measures: List[dict]):
        lines = [f"table {_quote(ds.table_name)}",
                 f"\tlineageTag: {_tag('table:' + ds.table_name)}",
                 ""]

        for m in measures:
            self._emit_measure(lines, m)

        for f in ds.fields:
            self._emit_column(lines, ds.table_name, f.name,
                              _DATATYPE.get(f.data_type, "string"),
                              is_key=f.is_key, source_column=f.name)

        self._emit_partition(lines, ds.table_name, generate_m_query(ds))

        lines.append("\tannotation PBI_ResultType = Table")
        lines.append("")
        _write_text(tables_dir / f"{_safe(ds.table_name)}.tmdl",
                    "\n".join(lines) + "\n")

    def _emit_measure(self, lines: List[str], m: dict):
        name = _quote(m["name"])
        expr = m["dax_expression"]
        if "\n" in expr:
            lines.append(f"\tmeasure {name} = ```")
            for ln in expr.split("\n"):
                lines.append(f"\t\t\t{ln}")
            lines.append("\t\t\t```")
        else:
            lines.append(f"\tmeasure {name} = {expr}")
        fmt = self._measure_format(m["name"])
        if fmt:
            lines.append(f'\t\tformatString: {fmt}')
        lines.append(f"\t\tlineageTag: {_tag('measure:' + m['name'])}")
        lines.append("")

    @staticmethod
    def _emit_column(lines, table_name, name, data_type,
                     is_key=False, source_column=None, is_hidden=False):
        lines.append(f"\tcolumn {_quote(name)}")
        lines.append(f"\t\tdataType: {data_type}")
        # Scope the lineageTag by table so columns sharing a name across tables
        # (ProductID, Year, …) get distinct tags.
        lines.append(f"\t\tlineageTag: {_tag('column:' + table_name + '.' + name)}")
        lines.append("\t\tsummarizeBy: none")
        lines.append(f"\t\tsourceColumn: {_quote(source_column or name)}")
        if is_key:
            lines.append("\t\tisKey")
        if is_hidden:
            lines.append("\t\tisHidden")
        lines.append("")
        lines.append("\t\tannotation SummarizationSetBy = Automatic")
        lines.append("")

    @staticmethod
    def _emit_partition(lines, table_name, m_expr):
        part_name = f"{table_name}-{_tag('partition:' + table_name)}"
        lines.append(f"\tpartition {_quote(part_name)} = m")
        lines.append("\t\tmode: import")
        lines.append("\t\tsource =")
        for ln in m_expr.split("\n"):
            lines.append(f"\t\t\t\t{ln}")
        lines.append("")

    def _write_calendar(self, tables_dir: Path):
        cal_start, cal_end = 2020, 2030
        calendar_m = (
            "let\n"
            f"    StartDate = #date({cal_start}, 1, 1),\n"
            f"    EndDate = #date({cal_end}, 12, 31),\n"
            "    DayCount = Duration.Days(EndDate - StartDate) + 1,\n"
            "    DateList = List.Dates(StartDate, DayCount, #duration(1, 0, 0, 0)),\n"
            '    Source = Table.FromList(DateList, Splitter.SplitByNothing(), '
            '{"Date"}, null, ExtraValues.Error),\n'
            '    Typed = Table.TransformColumnTypes(Source, {{"Date", type date}}),\n'
            '    AddYear = Table.AddColumn(Typed, "Year", each Date.Year([Date]), Int64.Type),\n'
            '    AddMonth = Table.AddColumn(AddYear, "MonthNumber", each Date.Month([Date]), Int64.Type),\n'
            '    AddMonthName = Table.AddColumn(AddMonth, "Month", each Date.MonthName([Date]), type text),\n'
            '    AddQuarter = Table.AddColumn(AddMonthName, "Quarter", '
            'each "Q" & Text.From(Date.QuarterOfYear([Date])), type text)\n'
            "in\n"
            "    AddQuarter"
        )
        lines = [
            "table Calendar",
            f"\tlineageTag: {_tag('table:Calendar')}",
            "",
        ]
        cols = [
            ("Date", "dateTime", True),
            ("Year", "int64", False),
            ("MonthNumber", "int64", False),
            ("Month", "string", False),
            ("Quarter", "string", False),
        ]
        for cname, ctype, ckey in cols:
            self._emit_column(lines, "Calendar", cname, ctype,
                              is_key=ckey, source_column=cname)
        self._emit_partition(lines, "Calendar", calendar_m)
        lines.append("\tannotation PBI_ResultType = Table")
        lines.append("")
        _write_text(tables_dir / "Calendar.tmdl", "\n".join(lines) + "\n")

    def _write_measures_table(self, tables_dir: Path):
        """One column-free table holding every measure (avoids name clashes)."""
        lines = [f"table {_quote(MEASURES_TABLE)}",
                 f"\tlineageTag: {_tag('table:' + MEASURES_TABLE)}",
                 ""]
        for m in self._converted_measures():
            self._emit_measure(lines, m)
        # A hidden placeholder column so the table is a valid import table.
        self._emit_column(lines, MEASURES_TABLE, "_dummy", "int64",
                          source_column="_dummy", is_hidden=True)
        empty_m = ("let\n"
                   "    Source = #table(type table [_dummy = Int64.Type], {})\n"
                   "in\n"
                   "    Source")
        self._emit_partition(lines, MEASURES_TABLE, empty_m)
        lines.append("\tannotation PBI_ResultType = Table")
        lines.append("")
        _write_text(tables_dir / f"{_safe(MEASURES_TABLE)}.tmdl",
                    "\n".join(lines) + "\n")

    def _write_relationships(self, def_dir: Path):
        if not self.app.associations:
            _write_text(def_dir / "relationships.tmdl", "")
            return
        lines: List[str] = []
        for a in self.app.associations:
            rel_id = _tag(f"rel:{a.from_table}.{a.from_field}->{a.to_table}.{a.to_field}")
            lines.append(f"relationship {rel_id}")
            lines.append(f"\tfromColumn: {_quote(a.from_table)}.{_quote(a.from_field)}")
            lines.append(f"\ttoColumn: {_quote(a.to_table)}.{_quote(a.to_field)}")
            # many-to-one is TMDL's default; only emit non-defaults.
            if a.cardinality == "one-to-one":
                lines.append("\tfromCardinality: one")
                lines.append("\ttoCardinality: one")
            lines.append("\tcrossFilteringBehavior: oneDirection")
            lines.append("")
        _write_text(def_dir / "relationships.tmdl", "\n".join(lines) + "\n")


# ── io helpers ───────────────────────────────────────────────────────

def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def _write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\- ]", "_", name)
