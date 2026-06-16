"""Load-script parser: reconstructing tables, fields, sources, associations."""
from extraction.qlik_script_parser import parse_script, parse_scripts


SCRIPT = """
LIB CONNECT TO 'Snowflake_Prod';

Orders:
LOAD
    OrderID,
    CustomerID,
    Amount as NetAmount,
    OrderDate
;
SQL SELECT OrderID, CustomerID, Amount, OrderDate FROM SALES.PUBLIC.ORDERS;

Customers:
LOAD
    CustomerID,
    [Customer Name],
    Country
FROM [lib://Files/customers.csv]
(txt, embedded labels, delimiter is ',');
"""


def test_parses_tables_and_fields():
    result = parse_script(SCRIPT)
    tables = {t["table_name"]: t for t in result["tables"]}
    assert set(tables) == {"Orders", "Customers"}
    order_fields = [f["name"] for f in tables["Orders"]["fields"]]
    assert order_fields == ["OrderID", "CustomerID", "NetAmount", "OrderDate"]


def test_detects_source_types():
    tables = {t["table_name"]: t for t in parse_script(SCRIPT)["tables"]}
    assert tables["Orders"]["source_type"] == "snowflake"   # via LIB CONNECT
    assert tables["Customers"]["source_type"] == "csv"      # via .csv FROM


def test_strips_brackets_and_aliases():
    tables = {t["table_name"]: t for t in parse_script(SCRIPT)["tables"]}
    cust_fields = [f["name"] for f in tables["Customers"]["fields"]]
    assert "Customer Name" in cust_fields          # [Customer Name] -> Customer Name
    assert "NetAmount" in [f["name"] for f in tables["Orders"]["fields"]]


def test_infers_associations():
    result = parse_script(SCRIPT)
    pairs = {(a["from_table"], a["to_table"], a["from_field"])
             for a in result["associations"]}
    assert ("Orders", "Customers", "CustomerID") in pairs


def test_handles_empty_and_garbage():
    assert parse_script("")["tables"] == []
    assert parse_script("SET x = 1; LET y = 2;")["tables"] == []


def test_parse_scripts_merges_sections():
    a = "T1:\nLOAD A, B FROM x.qvd (qvd);"
    b = "T2:\nLOAD B, C FROM y.qvd (qvd);"
    merged = parse_scripts([a, b])
    assert {t["table_name"] for t in merged["tables"]} == {"T1", "T2"}
    # shared field B -> association
    assert any(r["from_field"] == "B" for r in merged["associations"])
