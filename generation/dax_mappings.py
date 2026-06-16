"""Harvested Qlik -> DAX function mappings (data only, no logic).

Each entry maps a Qlik function to its DAX equivalent and carries its own
verification case: ``test = (qlik_expression, expected_dax)``. The converter is
driven entirely by this table, and ``tests/test_dax_converter.py`` asserts that
every single entry round-trips through the converter to its expected DAX. A
mapping with no passing test does not belong here.

Source: function names cross-checked against
``cyphou/Qlik-To-PowerBI`` (qlik_export/dax_converter.py) and the official DAX
function reference. Test cases assume the default unqualified table name 'T',
which the converter uses when no column->table map is supplied.
"""
from __future__ import annotations

from typing import Dict, Tuple, TypedDict


class Mapping(TypedDict):
    dax: str
    test: Tuple[str, str]


# Qlik function name -> DAX function name + a verification case.
# Only 1:1 name renames whose single-/multi-column form is valid DAX are listed;
# anything requiring structural rewriting is intentionally absent and falls back.
DAX_MAPPINGS: Dict[str, Mapping] = {
    # ── Aggregations ────────────────────────────────────────────────
    "Sum":           {"dax": "SUM",           "test": ("Sum(Sales)", "SUM('T'[Sales])")},
    "Avg":           {"dax": "AVERAGE",       "test": ("Avg(Price)", "AVERAGE('T'[Price])")},
    "Count":         {"dax": "COUNTA",        "test": ("Count(OrderID)", "COUNTA('T'[OrderID])")},
    "CountDistinct": {"dax": "DISTINCTCOUNT", "test": ("CountDistinct(CustomerID)", "DISTINCTCOUNT('T'[CustomerID])")},
    "Min":           {"dax": "MIN",           "test": ("Min(Amount)", "MIN('T'[Amount])")},
    "Max":           {"dax": "MAX",           "test": ("Max(Amount)", "MAX('T'[Amount])")},
    "Median":        {"dax": "MEDIAN",        "test": ("Median(Score)", "MEDIAN('T'[Score])")},
    "Stdev":         {"dax": "STDEV.S",       "test": ("Stdev(Score)", "STDEV.S('T'[Score])")},
    "Sqrt":          {"dax": "SQRT",          "test": ("Sqrt(Variance)", "SQRT('T'[Variance])")},

    # ── Math (single column arg, valid DAX) ─────────────────────────
    "Abs":           {"dax": "ABS",           "test": ("Abs(Profit)", "ABS('T'[Profit])")},
    "Exp":           {"dax": "EXP",           "test": ("Exp(Rate)", "EXP('T'[Rate])")},
    "Sign":          {"dax": "SIGN",          "test": ("Sign(Delta)", "SIGN('T'[Delta])")},
    "Int":           {"dax": "INT",           "test": ("Int(Amount)", "INT('T'[Amount])")},

    # ── Math (multi arg) ────────────────────────────────────────────
    "Round":         {"dax": "ROUND",         "test": ("Round(Amount, 2)", "ROUND('T'[Amount], 2)")},
    "Div":           {"dax": "DIVIDE",        "test": ("Div(Profit, Sales)", "DIVIDE('T'[Profit], 'T'[Sales])")},
    "Pow":           {"dax": "POWER",         "test": ("Pow(Base, 2)", "POWER('T'[Base], 2)")},

    # ── Date / time (single date column) ────────────────────────────
    "Year":          {"dax": "YEAR",          "test": ("Year(OrderDate)", "YEAR('T'[OrderDate])")},
    "Month":         {"dax": "MONTH",         "test": ("Month(OrderDate)", "MONTH('T'[OrderDate])")},
    "Day":           {"dax": "DAY",           "test": ("Day(OrderDate)", "DAY('T'[OrderDate])")},
    "Hour":          {"dax": "HOUR",          "test": ("Hour(EventTime)", "HOUR('T'[EventTime])")},
    "Minute":        {"dax": "MINUTE",        "test": ("Minute(EventTime)", "MINUTE('T'[EventTime])")},
    "Second":        {"dax": "SECOND",        "test": ("Second(EventTime)", "SECOND('T'[EventTime])")},
    "Week":          {"dax": "WEEKNUM",       "test": ("Week(OrderDate)", "WEEKNUM('T'[OrderDate])")},
    "WeekDay":       {"dax": "WEEKDAY",       "test": ("WeekDay(OrderDate)", "WEEKDAY('T'[OrderDate])")},

    # ── String (single column) ──────────────────────────────────────
    "Upper":         {"dax": "UPPER",         "test": ("Upper(Region)", "UPPER('T'[Region])")},
    "Lower":         {"dax": "LOWER",         "test": ("Lower(Region)", "LOWER('T'[Region])")},
    "Len":           {"dax": "LEN",           "test": ("Len(Region)", "LEN('T'[Region])")},
    "Trim":          {"dax": "TRIM",          "test": ("Trim(Region)", "TRIM('T'[Region])")},

    # ── Logic / null ────────────────────────────────────────────────
    "If":            {"dax": "IF",            "test": ("If(Amount > 0, Amount, 0)", "IF('T'[Amount] > 0, 'T'[Amount], 0)")},
    "IsNull":        {"dax": "ISBLANK",       "test": ("IsNull(Amount)", "ISBLANK('T'[Amount])")},
    "IsNum":         {"dax": "ISNUMBER",      "test": ("IsNum(Amount)", "ISNUMBER('T'[Amount])")},
    "IsText":        {"dax": "ISTEXT",        "test": ("IsText(Region)", "ISTEXT('T'[Region])")},
}


# Qlik field/measure format string -> DAX format string.
# Harvested from cyphou/Qlik-To-PowerBI (qlik_export/dax_converter.py).
QLIK_TO_DAX_FORMAT: Dict[str, str] = {
    "#,##0": "#,0",
    "#,##0.00": "#,0.00",
    "0%": "0%",
    "0.00%": "0.00%",
    "$ #,##0": "$#,0",
    "$ #,##0.00": "$#,0.00",
    "YYYY-MM-DD": "yyyy-mm-dd",
    "DD/MM/YYYY": "dd/mm/yyyy",
    "MM/DD/YYYY": "mm/dd/yyyy",
}


def dax_format_string(qlik_format: str | None) -> str | None:
    """Translate a Qlik format string to its DAX form, passing unknowns through."""
    if not qlik_format:
        return None
    return QLIK_TO_DAX_FORMAT.get(qlik_format, qlik_format)
