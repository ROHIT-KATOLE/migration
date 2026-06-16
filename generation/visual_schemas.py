"""Harvested visual.json reference templates (data only).

Two harvested tables, both verified against a real Power BI Desktop PBIR export:

1. ``QLIK_TO_PBI_VISUAL`` — Qlik object type -> Power BI ``visualType`` string.
   Source: cyphou/Qlik-To-PowerBI (powerbi_import/visual_generator.py).

2. ``VISUAL_ROLES`` — for each supported ``visualType``, which data roles the
   dimensions and measures project into inside ``query.queryState``. Source:
   the same repo's ``VISUAL_DATA_ROLES``, cross-checked against Desktop.

See ``reference/visual_schema_reference.md`` for the documented provenance of
each entry.
"""
from __future__ import annotations

from typing import Dict

# Qlik object type (lower-cased) -> Power BI visualType.
QLIK_TO_PBI_VISUAL: Dict[str, str] = {
    "barchart": "clusteredBarChart",
    "bar": "clusteredBarChart",
    "horizontalbar": "clusteredBarChart",
    "columnchart": "clusteredColumnChart",
    "column": "clusteredColumnChart",
    "combochart": "clusteredColumnChart",
    "histogram": "clusteredColumnChart",
    "linechart": "lineChart",
    "line": "lineChart",
    "area": "lineChart",
    "piechart": "pieChart",
    "pie": "pieChart",
    "donutchart": "donutChart",
    "donut": "donutChart",
    "waterfallchart": "waterfallChart",
    "barpluschart": "clusteredColumnChart",
    "table": "tableEx",
    "straighttable": "tableEx",
    "straight-table": "tableEx",
    "matrix": "matrix",
    "pivottable": "matrix",
    "pivot-table": "matrix",
    "kpi": "card",
    "card": "card",
    "scorecard": "card",
    "gauge": "kpi",
    "filterpane": "slicer",
    "slicer": "slicer",
    "listbox": "slicer",
}

# visualType -> role assignment.
#   dimension_roles : roles that dimensions fill, in order
#   measure_roles   : roles that measures fill, in order
#   single_role     : if set, ALL dims+measures go into this one role (tableEx)
VISUAL_ROLES: Dict[str, dict] = {
    "clusteredBarChart":    {"dimension_roles": ["Category"], "measure_roles": ["Y"]},
    "clusteredColumnChart": {"dimension_roles": ["Category"], "measure_roles": ["Y"]},
    "lineChart":            {"dimension_roles": ["Category"], "measure_roles": ["Y"]},
    "pieChart":             {"dimension_roles": ["Category"], "measure_roles": ["Y"]},
    "donutChart":           {"dimension_roles": ["Category"], "measure_roles": ["Y"]},
    "waterfallChart":       {"dimension_roles": ["Category"], "measure_roles": ["Y"]},
    "tableEx":              {"single_role": "Values"},
    # matrix: first dim -> Rows, additional dims -> Columns, measures -> Values
    "matrix":               {"dimension_roles": ["Rows", "Columns"], "measure_roles": ["Values"]},
    "card":                 {"dimension_roles": [], "measure_roles": ["Values"]},
    "kpi":                  {"dimension_roles": [], "measure_roles": ["Indicator"]},
    "slicer":               {"dimension_roles": ["Values"], "measure_roles": []},
}

# Default canvas size of a Power BI report page, in the units position uses.
PAGE_WIDTH = 1280
PAGE_HEIGHT = 720

# All measures live on one dedicated, column-free table (Power BI best practice).
# This avoids any measure-vs-column name collision and keeps measures together.
MEASURES_TABLE = "Key Measures"


def resolve_visual_type(qlik_type: str | None) -> str | None:
    """Map a Qlik object type to a Power BI visualType, or None if unsupported."""
    if not qlik_type:
        return None
    return QLIK_TO_PBI_VISUAL.get(str(qlik_type).strip().lower())
