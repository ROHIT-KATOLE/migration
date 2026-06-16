"""Generate Power Query (M) expressions for table partitions.

Five connectors, each harvested from cyphou/Qlik-To-PowerBI
(qlik_export/m_query_generator.py) and cross-checked against the M that Power BI
Desktop itself emits: Snowflake, CSV, Excel, SQL Server, and a QVD placeholder.

Every expression is a complete ``let ... in ...`` block ready to drop into a
TMDL partition source. Connection details come from the datasource's
``connection`` dict; missing values get clearly-fake placeholders the user must
replace (never a silently-wrong default).
"""
from __future__ import annotations

from typing import List

from extraction.schemas import DataSource, Field

# Power BI type -> M type keyword for the "Changed Type" coercion step.
_M_TYPE = {
    "string": "type text",
    "integer": "Int64.Type",
    "double": "type number",
    "dateTime": "type datetime",
    "boolean": "type logical",
}


def _type_step(fields: List[Field], prev_step: str) -> str | None:
    """Build a Table.TransformColumnTypes step from typed fields, or None."""
    if not fields:
        return None
    pairs = ", ".join(
        f'{{"{f.name}", {_M_TYPE.get(f.data_type, "type text")}}}' for f in fields
    )
    return f'    ChangedTypes = Table.TransformColumnTypes({prev_step}, {{{pairs}}})'


def generate_m_query(ds: DataSource) -> str:
    """Return a complete M expression for the datasource's table partition."""
    source_type = (ds.source_type or "").lower()
    dispatch = {
        "snowflake": _snowflake,
        "csv": _csv,
        "excel": _excel,
        "sqlserver": _sql_server,
        "qvd": _qvd,
    }
    builder = dispatch.get(source_type, _generic_placeholder)
    return builder(ds)


# ── connectors ───────────────────────────────────────────────────────

def _snowflake(ds: DataSource) -> str:
    c = ds.connection or {}
    server = c.get("server", "account.snowflakecomputing.com")
    warehouse = c.get("warehouse", "COMPUTE_WH")
    database = c.get("database", "DB")
    schema = c.get("schema", "PUBLIC")
    table = ds.table_name
    return "\n".join([
        "let",
        f'    Source = Snowflake.Databases("{server}", "{warehouse}"),',
        f'    DB = Source{{[Name="{database}"]}}[Data],',
        f'    Schema = DB{{[Name="{schema}"]}}[Data],',
        f'    Data = Schema{{[Name="{table}"]}}[Data]',
        "in",
        "    Data",
    ])


def _csv(ds: DataSource) -> str:
    c = ds.connection or {}
    path = c.get("path", f"C:\\Data\\{ds.table_name}.csv")
    delimiter = c.get("delimiter", ",")
    lines = [
        "let",
        f'    Source = Csv.Document(File.Contents("{path}"), '
        f'[Delimiter="{delimiter}", Encoding=65001, QuoteStyle=QuoteStyle.None]),',
        "    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),",
    ]
    step = _type_step(ds.fields, "PromotedHeaders")
    if step:
        lines.append(step)
        lines += ["in", "    ChangedTypes"]
    else:
        lines += ["in", "    PromotedHeaders"]
    return "\n".join(lines)


def _excel(ds: DataSource) -> str:
    c = ds.connection or {}
    path = c.get("path", f"C:\\Data\\{ds.table_name}.xlsx")
    sheet = c.get("sheet", ds.table_name)
    lines = [
        "let",
        f'    Source = Excel.Workbook(File.Contents("{path}"), null, true),',
        f'    Sheet = Source{{[Item="{sheet}",Kind="Sheet"]}}[Data],',
        "    PromotedHeaders = Table.PromoteHeaders(Sheet, [PromoteAllScalars=true]),",
    ]
    step = _type_step(ds.fields, "PromotedHeaders")
    if step:
        lines.append(step)
        lines += ["in", "    ChangedTypes"]
    else:
        lines += ["in", "    PromotedHeaders"]
    return "\n".join(lines)


def _sql_server(ds: DataSource) -> str:
    c = ds.connection or {}
    server = c.get("server", c.get("host", "localhost"))
    database = c.get("database", c.get("db", "master"))
    table = ds.table_name
    schema, tbl = ("dbo", table) if "." not in table else table.split(".", 1)
    return "\n".join([
        "let",
        f'    Source = Sql.Database("{server}", "{database}"),',
        f'    Data = Source{{[Schema="{schema}",Item="{tbl}"]}}[Data]',
        "in",
        "    Data",
    ])


def _qvd(ds: DataSource) -> str:
    """QVD has no native Power Query connector. Emit a documented placeholder
    that loads an empty typed table so the model still opens, with a TODO."""
    return "\n".join([
        "let",
        "    // TODO: QVD is not natively supported by Power Query.",
        f"    //   Re-export '{ds.table_name}' as CSV/Parquet, or read via an ODBC/",
        "    //   custom connector, then replace this Source step.",
        "    Source = #table(type table [], {})",
        "in",
        "    Source",
    ])


def _generic_placeholder(ds: DataSource) -> str:
    return "\n".join([
        "let",
        f"    // TODO: Unknown source type '{ds.source_type}' for table "
        f"'{ds.table_name}'. Configure the connector manually.",
        "    Source = #table(type table [], {})",
        "in",
        "    Source",
    ])
