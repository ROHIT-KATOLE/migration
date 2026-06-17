"""Reconstruct the data model from a recovered Qlik load script.

The Engine's data model (tables, fields, keys) is the hardest thing to recover
from a ``.qvf`` without a running engine, but the **load script** carries most
of it in readable form once decompressed. This parser is tolerant and best-effort
— it extracts what it can and leaves the rest to downstream defaults, never
raising on unfamiliar syntax.

Recovered:
* tables + their fields (from ``LOAD`` / ``SQL SELECT`` blocks)
* the source type per table (csv / excel / qvd / snowflake / sqlserver / …)
* connection hints (from ``LIB CONNECT TO`` / ``CONNECT`` and SQL dialects)
* associations (Qlik's associative model links tables by identical field names)
"""
from __future__ import annotations

import re
from typing import Dict, List

# A table load block: `Label:` then LOAD/SQL ... up to the terminating `;`.
_TABLE_RE = re.compile(
    r"(?:^|\n)\s*([A-Za-z_][\w ]*?):\s*\n?\s*"
    r"(LOAD|SQL\s+SELECT|DIRECT\s+QUERY)\b(.*?);",
    re.IGNORECASE | re.DOTALL,
)

_CONNECT_RE = re.compile(
    r"(?:LIB\s+)?CONNECT(?:\s+TO)?\s+'?\[?([^'\];\n]+)\]?'?",
    re.IGNORECASE,
)

# Source-type detection from connection text / FROM clauses.
_SOURCE_HINTS = [
    ("snowflake", re.compile(r"snowflake", re.I)),
    ("sqlserver", re.compile(r"\b(sql\s*server|mssql|sqlserver|sqlncli|sqloledb)\b", re.I)),
    ("excel", re.compile(r"\.xlsx?\b|biff|ooxml|excel", re.I)),
    ("csv", re.compile(r"\.csv\b|\.txt\b|delimiter\s+is|txt,\s*", re.I)),
    ("qvd", re.compile(r"\.qvd\b|\(qvd\)", re.I)),
]


def parse_script(text: str) -> Dict[str, list]:
    """Parse one load-script string into {tables, connections, associations}."""
    connections = _parse_connections(text)
    default_conn_type = connections[0]["source_type"] if connections else None

    tables: List[dict] = []
    seen = set()
    for m in _TABLE_RE.finditer(text):
        name = m.group(1).strip()
        verb = m.group(2).upper()
        body = m.group(3)
        if not name or name.upper() in ("SET", "LET", "CALL", "SUB") or name in seen:
            continue
        seen.add(name)
        fields = _parse_fields(body, is_sql="SELECT" in verb)
        if not fields:
            continue
        tables.append({
            "table_name": name,
            "fields": fields,
            "source_type": _detect_source(body) or default_conn_type,
            "connection": connections[0]["connection"] if connections else None,
        })

    associations = _infer_associations(tables)
    return {"tables": tables, "connections": connections,
            "associations": associations}


def parse_scripts(scripts: List[str]) -> Dict[str, list]:
    """Merge the parse of several script blobs (multi-section apps)."""
    merged = {"tables": [], "connections": [], "associations": []}
    seen_tables = set()
    for s in scripts:
        part = parse_script(s)
        for t in part["tables"]:
            if t["table_name"] not in seen_tables:
                seen_tables.add(t["table_name"])
                merged["tables"].append(t)
        merged["connections"].extend(part["connections"])
    merged["associations"] = _infer_associations(merged["tables"])
    return merged


# ── internals ────────────────────────────────────────────────────────

def _parse_connections(text: str) -> List[dict]:
    out = []
    for m in _CONNECT_RE.finditer(text):
        name = m.group(1).strip()
        out.append({"name": name, "source_type": _detect_source(name),
                    "connection": {"name": name}})
    return out


def _detect_source(blob: str) -> str | None:
    for source_type, rx in _SOURCE_HINTS:
        if rx.search(blob):
            return source_type
    return None


def _parse_fields(body: str, is_sql: bool) -> List[dict]:
    """Pull field names from a LOAD/SELECT field list (up to FROM/RESIDENT)."""
    # Trim everything from the source clause onward.
    head = re.split(r"\b(FROM|RESIDENT|INLINE|AUTOGENERATE|;)\b", body,
                    maxsplit=1, flags=re.IGNORECASE)[0]
    fields = []
    for raw in _split_top_level(head):
        token = raw.strip()
        if not token or token == "*":
            continue
        name = _field_name(token, is_sql)
        if name and name not in (f["name"] for f in fields):
            fields.append({"name": name, "type": _guess_type(name), "is_key": False})
    return fields


def _field_name(token: str, is_sql: bool) -> str | None:
    # `expr as Alias` / `expr AS [Alias]` -> Alias is the field name.
    m = re.search(r"\bas\b\s+(\[[^\]]+\]|\"[^\"]+\"|`[^`]+`|\S+)\s*$", token,
                  re.IGNORECASE)
    if m:
        return _strip_quotes(m.group(1))
    # SQL `table.column` -> column.
    if is_sql and "." in token and " " not in token:
        token = token.rsplit(".", 1)[-1]
    # Bare `[Field Name]` or identifier.
    m = re.match(r"\s*(\[[^\]]+\]|\"[^\"]+\"|`[^`]+`|[A-Za-z_]\w*)", token)
    return _strip_quotes(m.group(1)) if m else None


def _strip_quotes(s: str) -> str:
    return s.strip().strip("[]\"`'").strip()


def _split_top_level(s: str) -> List[str]:
    """Split on commas that are not inside () or []."""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def _guess_type(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ("date", "time", "month", "year", "day", "period")):
        return "date"
    if any(k in n for k in ("amount", "qty", "quantity", "value", "price",
                            "cost", "revenue", "count", "sum", "total", "id",
                            "number", "num", "pct", "percent", "margin")):
        return "double" if "id" not in n else "integer"
    return "string"


# Words that mark a field as a measure/amount, not a join key. Relationships on
# these are almost always spurious (you don't join tables on a quantity).
_NON_KEY_HINT = re.compile(
    r"(?i)(qty|quantity|amount|amt|cogs|sales|revenue|net|value|price|cost|"
    r"count|sum|total|interval|size|rows|fields|pct|percent|%|margin|"
    r"balance|gap|forecast|budget|asp|stock|inventory)")


def _looks_like_key(field: str) -> bool:
    """A field worth joining on: not a measure/amount, and reasonably short."""
    name = (field or "").strip()
    if not name or len(name) > 60:
        return False
    return not _NON_KEY_HINT.search(name)


def _infer_associations(tables: List[dict]) -> List[dict]:
    """Qlik links tables by identical field names, but Power BI needs a clean
    relationship graph. We are conservative: only key-like shared fields, and at
    most ONE relationship per table pair (avoids ambiguous/duplicate-pair errors
    that block the model from loading)."""
    field_to_tables: Dict[str, List[str]] = {}
    sizes = {t["table_name"]: len(t["fields"]) for t in tables}
    for t in tables:
        for f in t["fields"]:
            if _looks_like_key(f["name"]):
                field_to_tables.setdefault(f["name"], []).append(t["table_name"])

    associations = []
    seen_pairs = set()
    for field, owners in field_to_tables.items():
        owners = sorted(set(owners), key=lambda n: sizes.get(n, 0), reverse=True)
        if len(owners) < 2:
            continue
        many, one = owners[0], owners[-1]
        pair = frozenset((many, one))
        if many == one or pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        associations.append({
            "from_table": many, "to_table": one,
            "from_field": field, "to_field": field,
            "cardinality": "many-to-one",
        })
    return associations
