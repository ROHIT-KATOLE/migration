"""Map recovered Qlik Engine objects into the validated ``ExtractedApp``.

Consumes a ``RawApp`` from ``qvf_reader`` (master measures/dimensions, sheets,
visualizations) plus the data model reconstructed from the load script, and
produces the single typed contract the whole ``generation/`` layer runs on.

This is the validation gate: mapping happens here, Pydantic validates, and if
anything fails the offending field is logged and **no output is written**.

It is written to the public Qlik Engine object schema (``qMeasure.qDef``,
``qDim.qFieldDefs``, ``qHyperCubeDef.qDimensions/qMeasures``, sheet ``cells``),
so it generalises across apps rather than matching any one file. Visuals may
reference master items by ``qLibraryId`` or define them inline; both are handled.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from .qvf_reader import RawApp, QlikObject
from .qlik_script_parser import parse_scripts
from .schemas import (
    Association,
    AppMetadata,
    DataSource,
    Dimension,
    ExtractedApp,
    Field,
    Measure,
    Sheet,
    Variable,
    Visualization,
    JSON_FILES,
)

log = logging.getLogger("migrate.extractor")

QLIK_TYPE_MAP = {
    "string": "string", "text": "string", "char": "string", "ascii": "string",
    "integer": "integer", "int": "integer", "bigint": "integer",
    "double": "double", "num": "double", "number": "double", "numeric": "double",
    "float": "double", "money": "double", "real": "double", "decimal": "double",
    "date": "dateTime", "datetime": "dateTime", "timestamp": "dateTime",
    "time": "dateTime", "interval": "dateTime",
    "boolean": "boolean", "bool": "boolean",
}

# Grid the visuals are normalised onto (≈ Power BI page proportions).
_GRID_W = 12.0
_GRID_H = 13.0

# Qlik functions to ignore when scavenging field names from an expression.
_QLIK_FUNCS = {
    "sum", "avg", "count", "min", "max", "only", "median", "mode", "stdev",
    "countdistinct", "if", "alt", "rangesum", "aggr", "total", "num", "money",
    "date", "year", "month", "day", "div", "fabs", "round", "floor", "ceil",
}


def map_qlik_type(qlik_type: str | None) -> str:
    if not qlik_type:
        return "string"
    return QLIK_TYPE_MAP.get(str(qlik_type).strip().lower(), "string")


class ExtractionError(RuntimeError):
    """Raised when recovered objects cannot be validated into the contract."""


class Extractor:
    def __init__(self, raw: RawApp):
        self.raw = raw
        # Master-item lookups for resolving visual references.
        self._measure_by_id: Dict[str, str] = {}
        self._dim_by_id: Dict[str, Tuple[str, str]] = {}   # id -> (name, field)
        self._inline_measures: Dict[str, str] = {}         # name -> expression

    # ── public ───────────────────────────────────────────────────────

    def extract(self) -> ExtractedApp:
        measures = self._measures()
        dimensions = self._dimensions()
        sheets, cell_map = self._sheets()
        visualizations = self._visualizations(cell_map)

        # Inline visual measures discovered during _visualizations join the model.
        for name, expr in self._inline_measures.items():
            if name not in {m.name for m in measures}:
                measures.append(Measure(name=name, expression=expr))

        datasources, associations = self._data_model(measures, dimensions,
                                                      visualizations)
        try:
            app = ExtractedApp(
                metadata=self._metadata(),
                datasources=datasources,
                measures=measures,
                dimensions=dimensions,
                visualizations=visualizations,
                sheets=sheets,
                variables=self._variables(),
                associations=associations,
            )
        except ValidationError as e:
            for err in e.errors():
                loc = ".".join(str(p) for p in err["loc"])
                log.error("Validation failed at %s: %s (input=%r)",
                          loc, err["msg"], err.get("input"))
            raise ExtractionError(
                f"Extraction failed validation ({len(e.errors())} error(s)); "
                "no output written") from e

        log.info(
            "Extracted: %d tables, %d measures, %d dimensions, %d visuals, "
            "%d sheets, %d associations",
            len(app.datasources), len(app.measures), len(app.dimensions),
            len(app.visualizations), len(app.sheets), len(app.associations))
        return app

    # ── metadata ─────────────────────────────────────────────────────

    def _metadata(self) -> AppMetadata:
        app = self.raw.app or {}
        return AppMetadata(
            name=app.get("name") or "Migrated Qlik App",
            description=app.get("description"),
            theme=app.get("theme"),
        )

    # ── measures ─────────────────────────────────────────────────────

    def _measures(self) -> List[Measure]:
        out = []
        for obj in self.raw.by_type("measure"):
            props = obj.props or {}
            qm = props.get("qMeasure", {}) if isinstance(props, dict) else {}
            name = _safe_name(obj.title or qm.get("qLabel") or obj.id,
                              fallback=_synthetic_name(obj.id))
            expr = _strip_eq(qm.get("qDef", ""))
            self._measure_by_id[obj.id] = name
            if not expr:
                # Header-only / undecoded measure — keep the name, flag the gap.
                expr = ""
            out.append(Measure(
                name=name,
                expression=expr,
                label=qm.get("qLabel") or obj.title,
                format_string=_num_format(qm.get("qNumFormat")),
                color=_measure_color(props),
            ))
        return _dedupe_by_name(out)

    # ── dimensions ───────────────────────────────────────────────────

    def _dimensions(self) -> List[Dimension]:
        out = []
        for obj in self.raw.by_type("dimension"):
            props = obj.props or {}
            qd = props.get("qDim", {}) if isinstance(props, dict) else {}
            field_defs = qd.get("qFieldDefs") or []
            field = field_defs[0] if field_defs else (obj.title or obj.id)
            name = obj.title or (qd.get("qFieldLabels") or [None])[0] or field
            self._dim_by_id[obj.id] = (name, field)
            out.append(Dimension(
                name=name,
                field=_strip_brackets(field),
                label=obj.title,
                is_calculated=str(field).startswith("="),
            ))
        return _dedupe_by_name(out)

    # ── sheets ───────────────────────────────────────────────────────

    def _sheets(self) -> Tuple[List[Sheet], Dict[str, dict]]:
        """Return sheets plus a map: visual object id -> {sheet_id, x,y,w,h}."""
        sheets: List[Sheet] = []
        cell_map: Dict[str, dict] = {}
        for rank, obj in enumerate(self.raw.by_type("sheet")):
            props = obj.props or {}
            name = obj.title or _nested(props, "qMetaDef", "title") or obj.id
            rank_val = _coerce_int(props.get("rank"), rank)
            sheets.append(Sheet(id=obj.id, name=name, rank=rank_val))
            self._collect_cells(obj, cell_map)
        sheets.sort(key=lambda s: s.rank)
        return sheets, cell_map

    @staticmethod
    def _collect_cells(sheet: QlikObject, cell_map: Dict[str, dict]):
        cells = (sheet.props or {}).get("cells") or []
        if not cells:
            return
        gw = max((c.get("col", 0) + c.get("colspan", 0) for c in cells), default=0) or 24
        gh = max((c.get("row", 0) + c.get("rowspan", 0) for c in cells), default=0) or 12
        for c in cells:
            oid = c.get("name")
            if not oid:
                continue
            cell_map[oid] = {
                "sheet_id": sheet.id,
                "x": c.get("col", 0) / gw * _GRID_W,
                "y": c.get("row", 0) / gh * _GRID_H,
                "width": max(c.get("colspan", 1), 1) / gw * _GRID_W,
                "height": max(c.get("rowspan", 1), 1) / gh * _GRID_H,
            }

    # ── visualizations ───────────────────────────────────────────────

    def _visualizations(self, cell_map: Dict[str, dict]) -> List[Visualization]:
        out = []
        default_sheet = self.raw.by_type("sheet")
        default_sheet_id = default_sheet[0].id if default_sheet else ""
        for obj in self.raw.objects:
            if not _is_visual(obj):
                continue
            props = obj.props or {}
            dims, meas = self._hypercube(props)
            pos = cell_map.get(obj.id, {})
            out.append(Visualization(
                id=obj.id,
                type=obj.qtype,
                title=obj.title or _nested(props, "qMetaDef", "title"),
                sheet_id=pos.get("sheet_id") or default_sheet_id,
                dimensions=dims,
                measures=meas,
                x=pos.get("x", 0.0),
                y=pos.get("y", 0.0),
                width=pos.get("width", 4.0),
                height=pos.get("height", 4.0),
            ))
        return out

    def _hypercube(self, props: dict) -> Tuple[List[str], List[str]]:
        hc = props.get("qHyperCubeDef") or props.get("qListObjectDef") or {}
        dims: List[str] = []
        for d in hc.get("qDimensions", []) or []:
            lib = d.get("qLibraryId")
            if lib and lib in self._dim_by_id:
                dims.append(self._dim_by_id[lib][0])
            else:
                fdefs = _nested(d, "qDef", "qFieldDefs") or []
                if fdefs:
                    dims.append(_strip_brackets(fdefs[0]))
        # qListObjectDef (filterpane/listbox) holds a single field, either an
        # inline field def or a master-dimension reference (qLibraryId).
        if not dims:
            lib = hc.get("qLibraryId")
            field_defs = _nested(hc, "qDef", "qFieldDefs")
            if lib and lib in self._dim_by_id:
                dims.append(self._dim_by_id[lib][0])
            elif field_defs:
                dims.append(_strip_brackets(field_defs[0]))

        meas: List[str] = []
        for m in hc.get("qMeasures", []) or []:
            lib = m.get("qLibraryId")
            if lib and lib in self._measure_by_id:
                meas.append(self._measure_by_id[lib])
            else:
                qdef = m.get("qDef", {})
                expr = _strip_eq(qdef.get("qDef", ""))
                if expr:
                    # No label -> a stable synthetic name (never the expression,
                    # which would put newlines/quotes into a TMDL measure name).
                    name = _safe_name(qdef.get("qLabel") or "",
                                      fallback=_synthetic_name(expr))
                    self._inline_measures[name] = expr
                    meas.append(name)
        return dims, meas

    # ── data model (from load script, with a structural fallback) ─────

    def _data_model(self, measures, dimensions, visuals):
        parsed = parse_scripts(self.raw.scripts) if self.raw.scripts else \
            {"tables": [], "associations": []}
        datasources = self._tables_to_datasources(parsed["tables"])

        if datasources:
            associations = [Association(**a) for a in parsed["associations"]]
            return datasources, associations

        # Fallback: no script recovered -> synthesise one table whose columns
        # are every field the measures/dimensions reference, so the model still
        # validates and visuals resolve. Flagged clearly in the log.
        log.warning("No load script recovered; synthesising a flat data model "
                    "from referenced fields. Verify tables/relationships manually.")
        fields = self._scavenge_fields(measures, dimensions)
        table = DataSource(
            table_name="Data",
            fields=[Field(name=f, data_type=map_qlik_type(_guess(f)))
                    for f in fields] or [Field(name="Column1", data_type="string")],
            source_type=None,
        )
        return [table], []

    @staticmethod
    def _tables_to_datasources(tables: List[dict]) -> List[DataSource]:
        out = []
        for t in tables:
            fields = [Field(name=_strip_brackets(f["name"]),
                            data_type=map_qlik_type(f.get("type")),
                            is_key=bool(f.get("is_key")))
                      for f in t.get("fields", [])]
            if not fields:
                continue
            out.append(DataSource(
                table_name=t["table_name"],
                fields=fields,
                source_type=t.get("source_type"),
                connection=t.get("connection"),
            ))
        return out

    def _scavenge_fields(self, measures, dimensions) -> List[str]:
        names: List[str] = []
        for d in dimensions:
            if d.field and not d.is_calculated:
                _add_unique(names, d.field)
        for m in measures:
            for tok in re.findall(r"[A-Za-z_]\w*", m.expression or ""):
                if tok.lower() not in _QLIK_FUNCS and not tok.isdigit():
                    _add_unique(names, tok)
        return names

    # ── variables ────────────────────────────────────────────────────

    def _variables(self) -> List[Variable]:
        out = []
        for obj in self.raw.by_type("variable"):
            props = obj.props or {}
            out.append(Variable(
                name=obj.title or props.get("qName") or obj.id,
                value=str(props.get("qDefinition", props.get("qContent", ""))),
            ))
        return out

    # ── output ───────────────────────────────────────────────────────

    def write_json(self, output_dir: Path) -> ExtractedApp:
        app = self.extract()
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        payloads = {
            "metadata": app.metadata.model_dump(),
            "datasources": [d.model_dump() for d in app.datasources],
            "measures": [m.model_dump() for m in app.measures],
            "dimensions": [d.model_dump() for d in app.dimensions],
            "visualizations": [v.model_dump() for v in app.visualizations],
            "sheets": [s.model_dump() for s in app.sheets],
            "variables": [v.model_dump() for v in app.variables],
            "associations": [a.model_dump() for a in app.associations],
        }
        for domain, filename in JSON_FILES.items():
            with open(out / filename, "w", encoding="utf-8") as fh:
                json.dump(payloads[domain], fh, indent=2, ensure_ascii=False)
        return app


def merge_apps(apps: List[ExtractedApp], name: str) -> ExtractedApp:
    """Merge several extracted apps into one (for layered E_/T_/UI Qlik apps).

    Datasources, measures, dimensions and associations are de-duplicated by their
    natural key; sheets and visualizations are concatenated (Qlik ids are unique
    across layers). Description/theme come from the first app that has them.
    """
    apps = [a for a in apps if a is not None]
    if len(apps) == 1:
        return apps[0]

    def _dedupe(seq, key):
        seen, out = set(), []
        for item in seq:
            k = key(item)
            if k not in seen:
                seen.add(k)
                out.append(item)
        return out

    description = next((a.metadata.description for a in apps
                        if a.metadata.description), None)
    theme = next((a.metadata.theme for a in apps if a.metadata.theme), None)
    return ExtractedApp(
        metadata=AppMetadata(name=name, description=description, theme=theme),
        datasources=_dedupe([d for a in apps for d in a.datasources],
                            lambda d: d.table_name),
        measures=_dedupe([m for a in apps for m in a.measures], lambda m: m.name),
        dimensions=_dedupe([d for a in apps for d in a.dimensions], lambda d: d.name),
        visualizations=_dedupe([v for a in apps for v in a.visualizations],
                               lambda v: v.id),
        sheets=_dedupe([s for a in apps for s in a.sheets], lambda s: s.id),
        variables=_dedupe([v for a in apps for v in a.variables], lambda v: v.name),
        associations=_dedupe(
            [a2 for a in apps for a2 in a.associations],
            lambda r: (r.from_table, r.to_table, r.from_field, r.to_field)),
    )


# ── helpers ──────────────────────────────────────────────────────────

def _is_visual(obj: QlikObject) -> bool:
    from .qvf_reader import _is_visual_type
    return _is_visual_type(obj.qtype)


_EXPR_NAME_RE = re.compile(r"(?i)^(sum|count|avg|aggr|if|min|max|only|"
                           r"rangesum|num|pick|firstsortedvalue)\s*\(")


def _safe_name(raw: Any, fallback: str) -> str:
    """A TMDL/Power BI-safe object name: single line, no quotes, bounded length.

    Names that are really expressions (multi-line, start with '=', or look like
    ``func(...)``) are rejected in favour of the fallback, so a measure name can
    never be a raw Qlik formula.
    """
    s = re.sub(r"\s+", " ", str(raw or "")).replace("'", "").replace('"', "").strip()
    if (not s or len(s) > 100 or s.startswith("=") or "{" in s
            or _EXPR_NAME_RE.match(s)):
        return fallback
    return s


def _synthetic_name(seed: str) -> str:
    digest = hashlib.md5(str(seed).encode("utf-8")).hexdigest()[:10]
    return f"Measure {digest}"


def _strip_eq(expr: Any) -> str:
    s = str(expr or "").strip()
    return s[1:].strip() if s.startswith("=") else s


def _strip_brackets(name: Any) -> str:
    return str(name or "").strip().strip("[]").strip()


def _num_format(qfmt: Any) -> Optional[str]:
    if isinstance(qfmt, dict):
        return qfmt.get("qFmt") or None
    return None


def _measure_color(props: dict) -> Optional[str]:
    color = _nested(props, "qMeasure", "coloring", "baseColor", "color")
    return color if isinstance(color, str) else None


def _nested(d: Any, *keys) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dedupe_by_name(items: list) -> list:
    seen, out = set(), []
    for it in items:
        if it.name not in seen:
            seen.add(it.name)
            out.append(it)
    return out


def _add_unique(lst: List[str], value: str):
    if value and value not in lst:
        lst.append(value)


def _guess(field: str) -> str:
    from .qlik_script_parser import _guess_type
    return _guess_type(field)
