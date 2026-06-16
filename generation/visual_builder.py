"""Build PBIR ``visual.json`` objects from extracted visualizations.

Targets the modern Power BI Enhanced Report (PBIR) format, where each visual is
its own ``visual.json`` carrying ``visual.query.queryState`` with role-based
projections (harvested from cyphou/Qlik-To-PowerBI and verified against a real
Power BI Desktop export). This is the format current Desktop opens in Developer
mode — it supersedes the older single-file ``prototypeQuery`` form.

Ten visual types are implemented as separate builders. An unknown Qlik type
never crashes the run: it becomes a textbox placeholder carrying a TODO.
"""
from __future__ import annotations

import logging
import uuid
from typing import Dict, List, Optional, Tuple

from extraction.schemas import ExtractedApp, Visualization
from .visual_schemas import (
    MEASURES_TABLE,
    PAGE_HEIGHT,
    PAGE_WIDTH,
    VISUAL_ROLES,
    resolve_visual_type,
)

log = logging.getLogger("migrate.visual")

VISUAL_SCHEMA = ("https://developer.microsoft.com/json-schemas/fabric/item/"
                 "report/definition/visualContainer/1.4.0/schema.json")

# Qlik grid units -> report-canvas pixels.
_COL_W = PAGE_WIDTH / 12.0
_ROW_H = 52.0

_NS = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def _vid(seed: str) -> str:
    return uuid.uuid5(_NS, seed).hex


def validate_visual_json(visual: dict) -> None:
    """Raise ValueError if the visual is missing required PBIR structure."""
    for key in ("name", "position", "visual"):
        if key not in visual:
            raise ValueError(f"Visual missing required key: {key}")
    v = visual["visual"]
    if "visualType" not in v:
        raise ValueError("Visual missing visualType")
    if v["visualType"] == "textbox":
        return  # placeholders carry no query
    query_state = v.get("query", {}).get("queryState")
    if not query_state:
        raise ValueError(
            f"Visual {v['visualType']} missing query.queryState")


class VisualBuilder:
    def __init__(self, extracted: ExtractedApp):
        self.app = extracted
        self.fact_table = self._fact_table()
        self.dim_lookup = {d.name: (d.table or self._field_table(d.field), d.field)
                           for d in extracted.dimensions}
        self.field_table = self._build_field_table()

    # ── public API ───────────────────────────────────────────────────

    def build_pages(self) -> List[dict]:
        """Return one page dict per sheet: name, displayName, ordinal, visuals."""
        sheets = sorted(self.app.sheets, key=lambda s: s.rank)
        if not sheets:
            sheets = []
        by_sheet: Dict[str, List[Visualization]] = {}
        for viz in self.app.visualizations:
            by_sheet.setdefault(viz.sheet_id, []).append(viz)

        pages = []
        for sheet in sheets:
            visuals = []
            for viz in by_sheet.get(sheet.id, []):
                visuals.append((viz.id, self.build_visual(viz)))
            pages.append({
                "name": "p" + _vid("page:" + sheet.id)[:16],
                "displayName": sheet.name,
                "ordinal": sheet.rank,
                "visuals": visuals,
            })
        # Visualizations on unknown sheets still get a home.
        orphan = [v for v in self.app.visualizations
                  if v.sheet_id not in {s.id for s in sheets}]
        if orphan:
            pages.append({
                "name": "p" + _vid("page:_orphan")[:16],
                "displayName": "Other",
                "ordinal": len(pages),
                "visuals": [(v.id, self.build_visual(v)) for v in orphan],
            })
        return pages

    def build_visual(self, viz: Visualization) -> dict:
        """Dispatch to the right builder; unknown types become a placeholder."""
        pbi_type = resolve_visual_type(viz.type)
        if pbi_type is None:
            log.warning("Unsupported visual type '%s' (%s) -> placeholder",
                        viz.type, viz.id)
            return self.build_placeholder(viz)
        try:
            visual = self._build_typed(viz, pbi_type)
            validate_visual_json(visual)
            return visual
        except Exception as e:  # noqa: BLE001 - one bad visual must not kill the run
            log.warning("Failed to build visual %s (%s): %s -> placeholder",
                        viz.id, viz.type, e)
            return self.build_placeholder(viz)

    # ── the 10 typed builders ────────────────────────────────────────
    # Each delegates to _assemble with the resolved visualType; the role layout
    # is data-driven from VISUAL_ROLES so the builders stay declarative.

    def build_bar_chart(self, viz):    return self._assemble(viz, "clusteredBarChart")
    def build_column_chart(self, viz): return self._assemble(viz, "clusteredColumnChart")
    def build_line_chart(self, viz):   return self._assemble(viz, "lineChart")
    def build_pie_chart(self, viz):    return self._assemble(viz, "pieChart")
    def build_donut_chart(self, viz):  return self._assemble(viz, "donutChart")
    def build_waterfall(self, viz):    return self._assemble(viz, "waterfallChart")
    def build_table(self, viz):        return self._assemble(viz, "tableEx")
    def build_matrix(self, viz):       return self._assemble(viz, "matrix")
    def build_card(self, viz):         return self._assemble(viz, "card")
    def build_kpi(self, viz):          return self._assemble(viz, "kpi")
    def build_slicer(self, viz):       return self._assemble(viz, "slicer")

    _DISPATCH = {
        "clusteredBarChart": "build_bar_chart",
        "clusteredColumnChart": "build_column_chart",
        "lineChart": "build_line_chart",
        "pieChart": "build_pie_chart",
        "donutChart": "build_donut_chart",
        "waterfallChart": "build_waterfall",
        "tableEx": "build_table",
        "matrix": "build_matrix",
        "card": "build_card",
        "kpi": "build_kpi",
        "slicer": "build_slicer",
    }

    def _build_typed(self, viz: Visualization, pbi_type: str) -> dict:
        method = getattr(self, self._DISPATCH[pbi_type])
        return method(viz)

    # ── assembly ─────────────────────────────────────────────────────

    def _assemble(self, viz: Visualization, pbi_type: str) -> dict:
        roles = VISUAL_ROLES[pbi_type]
        dim_projs = [self._column_projection(*self._resolve_dim(d))
                     for d in viz.dimensions]
        meas_projs = [self._measure_projection(*self._resolve_measure(m))
                      for m in viz.measures]

        query_state = self._build_query_state(pbi_type, roles, dim_projs, meas_projs)

        visual = {
            "$schema": VISUAL_SCHEMA,
            "name": _vid("visual:" + viz.id),
            "position": self._position(viz),
            "visual": {
                "visualType": pbi_type,
                "query": {"queryState": query_state},
                "objects": self._title_object(viz.title),
                "drillFilterOtherVisuals": True,
            },
        }
        return visual

    @staticmethod
    def _build_query_state(pbi_type, roles, dim_projs, meas_projs) -> dict:
        state: Dict[str, dict] = {}
        if "single_role" in roles:  # tableEx
            projs = dim_projs + meas_projs
            if projs:
                state[roles["single_role"]] = {"projections": projs}
            return state

        if pbi_type == "matrix":
            if dim_projs:
                state["Rows"] = {"projections": [dim_projs[0]]}
            if len(dim_projs) > 1:
                state["Columns"] = {"projections": dim_projs[1:]}
            if meas_projs:
                state["Values"] = {"projections": meas_projs}
            return state

        if roles.get("dimension_roles") and dim_projs:
            state[roles["dimension_roles"][0]] = {"projections": dim_projs}
        if roles.get("measure_roles") and meas_projs:
            state[roles["measure_roles"][0]] = {"projections": meas_projs}
        return state

    def build_placeholder(self, viz: Visualization) -> dict:
        """A textbox carrying a TODO — used for unsupported/failed visuals."""
        note = f"TODO: unsupported Qlik visual '{viz.type}' — {viz.title or viz.id}"
        return {
            "$schema": VISUAL_SCHEMA,
            "name": _vid("visual:" + viz.id),
            "position": self._position(viz),
            "visual": {
                "visualType": "textbox",
                "objects": {
                    "general": [{
                        "properties": {
                            "paragraphs": [{
                                "textRuns": [{"value": note}],
                            }],
                        },
                    }],
                },
                "drillFilterOtherVisuals": True,
            },
        }

    # ── projections & resolution ─────────────────────────────────────

    @staticmethod
    def _column_projection(table: str, field: str) -> dict:
        return {
            "field": {
                "Column": {
                    "Expression": {"SourceRef": {"Entity": table}},
                    "Property": field,
                },
            },
            "queryRef": f"{table}.{field}",
            "nativeQueryRef": field,
            "active": True,
        }

    @staticmethod
    def _measure_projection(table: str, name: str) -> dict:
        return {
            "field": {
                "Measure": {
                    "Expression": {"SourceRef": {"Entity": table}},
                    "Property": name,
                },
            },
            "queryRef": f"{table}.{name}",
            "nativeQueryRef": name,
        }

    def _resolve_dim(self, name: str) -> Tuple[str, str]:
        if name in self.dim_lookup:
            return self.dim_lookup[name]
        if name in self.field_table:
            return self.field_table[name], name
        return self.fact_table or "Table", name

    def _resolve_measure(self, name: str) -> Tuple[str, str]:
        # All measures live on the dedicated measures table (see semantic_model).
        return MEASURES_TABLE, name

    # ── small helpers ────────────────────────────────────────────────

    def _fact_table(self) -> Optional[str]:
        if not self.app.datasources:
            return None
        return max(self.app.datasources, key=lambda d: len(d.fields)).table_name

    def _build_field_table(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for ds in self.app.datasources:
            for f in ds.fields:
                out.setdefault(f.name, ds.table_name)
        return out

    def _field_table(self, field: str) -> str:
        # Used while building dim_lookup, so reference datasources directly.
        for ds in self.app.datasources:
            for f in ds.fields:
                if f.name == field:
                    return ds.table_name
        return self.fact_table or "Table"

    @staticmethod
    def _position(viz: Visualization) -> dict:
        x = round(viz.x * _COL_W)
        y = round(viz.y * _ROW_H)
        w = max(round(viz.width * _COL_W), 120)
        h = max(round(viz.height * _ROW_H), 80)
        # Keep visuals on canvas.
        x = min(x, PAGE_WIDTH - 120)
        return {"x": x, "y": y, "z": 0, "width": w, "height": h, "tabOrder": 0}

    @staticmethod
    def _title_object(title: Optional[str]) -> dict:
        if not title:
            return {}
        return {
            "title": [{
                "properties": {
                    "show": {"expr": {"Literal": {"Value": "true"}}},
                    "text": {"expr": {"Literal": {"Value": f"'{title}'"}}},
                },
            }],
        }
