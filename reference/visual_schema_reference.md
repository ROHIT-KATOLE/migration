# Visual Schema Reference

Documents the harvested visual structures in
[`generation/visual_schemas.py`](../generation/visual_schemas.py) and how
[`generation/visual_builder.py`](../generation/visual_builder.py) assembles a
PBIR `visual.json`.

## Target format: PBIR (modern), not the legacy single-file report

Power BI has two report formats:

- **Legacy** — one `report.json` whose visuals carry `singleVisual.prototypeQuery`.
- **PBIR** (Power BI Enhanced Report) — a `definition/` tree with one
  `visual.json` per visual, each carrying `visual.query.queryState`.

We target **PBIR**, because that is what current Power BI Desktop opens in
Developer mode and what `.pbip` projects use. This is a deliberate deviation
from the task's example `validate_visual_json`, which checked for
`prototypeQuery` — that key belongs to the legacy format. Our validator checks
for `query.queryState` instead.

## Source

- `visualType` strings: `cyphou/Qlik-To-PowerBI`,
  `powerbi_import/visual_generator.py` (its `VISUAL_TYPE_MAP`).
- Role layout (`Category`/`Y`/`Rows`/`Columns`/`Values`/…):
  the same repo's `VISUAL_DATA_ROLES` and `build_query_state`.
- Both cross-checked against a real Power BI Desktop PBIR export.

## The 10 supported visual types

| Builder | visualType | Dimension role(s) | Measure role(s) |
|---------|-----------|-------------------|-----------------|
| `build_bar_chart` | `clusteredBarChart` | Category | Y |
| `build_column_chart` | `clusteredColumnChart` | Category | Y |
| `build_line_chart` | `lineChart` | Category | Y |
| `build_pie_chart` | `pieChart` | Category | Y |
| `build_donut_chart` | `donutChart` | Category | Y |
| `build_table` | `tableEx` | — all fields → **Values** — | |
| `build_matrix` | `matrix` | Rows (1st dim), Columns (rest) | Values |
| `build_card` | `card` | — | Values |
| `build_kpi` | `kpi` | — | Indicator |
| `build_slicer` | `slicer` | Values | — |

Unknown Qlik types map to `None` and the builder emits a **textbox placeholder**
with a TODO, never crashing the run.

## visual.json structure

Top level (`visualContainer/1.4.0/schema.json`):

```jsonc
{
  "name": "<deterministic guid>",
  "position": { "x", "y", "z", "width", "height", "tabOrder" },
  "visual": {
    "visualType": "clusteredBarChart",
    "query": { "queryState": { "<role>": { "projections": [ <proj>, ... ] } } },
    "objects": { "title": [ ... ] },
    "drillFilterOtherVisuals": true
  }
}
```

### Projection — column (dimension)

```json
{
  "field": { "Column": {
      "Expression": { "SourceRef": { "Entity": "Sales" } },
      "Property": "Region" } },
  "queryRef": "Sales.Region",
  "nativeQueryRef": "Region",
  "active": true
}
```

### Projection — measure

```json
{
  "field": { "Measure": {
      "Expression": { "SourceRef": { "Entity": "Sales" } },
      "Property": "Total Sales" } },
  "queryRef": "Sales.Total Sales",
  "nativeQueryRef": "Total Sales"
}
```

`Entity` is the **table name**, which must match a `table` in the semantic
model. Measures resolve to the fact table (the datasource with the most fields),
matching where `semantic_model.py` places them; dimensions resolve via the
extracted dimension list, then by field→table lookup.

## Verification status

Structure is verified against the harvested reference and the PBIR schema, and
every builder is asserted to produce a `validate_visual_json`-passing object in
[`tests/test_visual_builder.py`](../tests/test_visual_builder.py). The final
"renders in Desktop with correct data" check requires Power BI Desktop and must
be run on the target machine — see the README's *Verification status* section.
