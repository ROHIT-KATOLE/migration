"""Pydantic models — the 8 typed contracts between extraction and generation.

These are the validation gate of Layer 1. Nothing reaches Layer 2 unless it
parses into ``ExtractedApp`` cleanly, so every downstream generator can trust
the shape of its input.
"""
from __future__ import annotations

from typing import List, Optional, Literal

from pydantic import BaseModel, field_validator

# The closed set of Power BI data types we map Qlik types onto.
PBIDataType = Literal["string", "integer", "double", "dateTime", "boolean"]


class Field(BaseModel):
    name: str
    data_type: PBIDataType
    is_key: bool = False


class DataSource(BaseModel):
    table_name: str
    fields: List[Field]
    # csv, excel, snowflake, sqlserver, qvd — drives the M-query connector.
    source_type: Optional[str] = None
    connection: Optional[dict] = None


class Measure(BaseModel):
    name: str
    expression: str
    label: Optional[str] = None
    format_string: Optional[str] = None
    color: Optional[str] = None


class Dimension(BaseModel):
    name: str
    field: str
    table: Optional[str] = None
    label: Optional[str] = None
    is_calculated: bool = False


class Visualization(BaseModel):
    id: str
    type: str
    title: Optional[str] = None
    sheet_id: str
    measures: List[str] = []
    dimensions: List[str] = []
    x: float = 0
    y: float = 0
    width: float = 4
    height: float = 4


class Sheet(BaseModel):
    id: str
    name: str
    rank: int = 0


class Variable(BaseModel):
    name: str
    value: str
    var_type: str = "static"


class Association(BaseModel):
    from_table: str
    to_table: str
    from_field: str
    to_field: str
    cardinality: str = "many-to-one"


class AppMetadata(BaseModel):
    name: str
    description: Optional[str] = None
    theme: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("App metadata name must not be blank")
        return v


class ExtractedApp(BaseModel):
    """The complete validated extraction — the typed contract for Layer 2."""

    metadata: AppMetadata
    datasources: List[DataSource] = []
    measures: List[Measure] = []
    dimensions: List[Dimension] = []
    visualizations: List[Visualization] = []
    sheets: List[Sheet] = []
    variables: List[Variable] = []
    associations: List[Association] = []


# Maps each domain to the JSON filename it is written to. Drives write_json and
# the extraction round-trip test, so the two never drift apart.
JSON_FILES = {
    "metadata": "app_metadata.json",
    "datasources": "datasources.json",
    "measures": "measures.json",
    "dimensions": "dimensions.json",
    "visualizations": "visualizations.json",
    "sheets": "sheets.json",
    "variables": "variables.json",
    "associations": "associations.json",
}
