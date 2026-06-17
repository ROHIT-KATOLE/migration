"""Convert Qlik measure expressions to DAX.

This is deliberately *not* a blind regex replacement. Each expression is
tokenised; function names are mapped through ``DAX_MAPPINGS`` only when the
identifier is actually a function call, and bare field references are qualified
as ``'Table'[Field]``. Anything the converter cannot prove it understands —
set analysis, dollar-expansion, an unknown function — is flagged ``fallback``
and emitted as a TODO placeholder rather than silently mis-converted.

There is NO DAX optimizer. The reference repo's optimizer corrupted output;
we do not reintroduce one.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from .dax_mappings import DAX_MAPPINGS

log = logging.getLogger("migrate.dax")

DEFAULT_TABLE = "T"

# Function-name lookup, case-insensitive.
_FUNC_MAP = {q.lower(): m["dax"] for q, m in DAX_MAPPINGS.items()}

# Identifiers that are values/operators, not column references — never qualified.
_KEYWORDS = {"true", "false", "null"}

# Markers that mean "do not attempt automatic conversion".
_SET_ANALYSIS_RE = re.compile(r"\{\s*<")          # Sum({<Year={2023}>} ...)
_DOLLAR_EXPAND_RE = re.compile(r"\$\(")            # $(vVariable)
_AGGR_RE = re.compile(r"\bAggr\s*\(", re.IGNORECASE)
_TOTAL_RE = re.compile(r"\bTOTAL\b", re.IGNORECASE)

# Qlik logical-operator keywords -> DAX operators (these are not functions).
_OPERATOR_WORDS = {"and": "&&", "or": "||", "not": "NOT", "xor": "<>"}

_TOKEN_RE = re.compile(
    r"""(?P<str>'[^']*'|"[^"]*")      # string literal
       |(?P<field>\[[^\]]+\])         # [Bracketed Field Name]
       |(?P<num>\d+\.?\d*)            # number
       |(?P<ident>[A-Za-z_][A-Za-z0-9_]*)
       |(?P<ws>\s+)
       |(?P<other>.)                  # any single other char
    """,
    re.VERBOSE | re.DOTALL,
)


class UnknownFunction(Exception):
    """Raised internally when a function call has no known DAX mapping."""


class UnknownColumn(Exception):
    """Raised when a column can't be resolved to a real table (avoids a phantom
    'T' reference that would break the model)."""


class DAXConverter:
    def __init__(
        self,
        measures: List[dict],
        column_table_map: Optional[Dict[str, str]] = None,
    ):
        # Accept Measure pydantic objects or plain dicts.
        self.measures = [m if isinstance(m, dict) else m.model_dump()
                         for m in measures]
        self.column_table_map = column_table_map or {}
        self.converted_count = 0
        self.fallback_count = 0
        self.failed_count = 0

    def convert_all(self) -> List[dict]:
        """Convert every measure. Returns one result dict per measure with keys
        name, original, dax_expression, status (converted|fallback|failed)."""
        results = []
        for m in self.measures:
            results.append(self._convert_one(m))
        return results

    def _convert_one(self, measure: dict) -> dict:
        name = measure.get("name", "Measure")
        original = (measure.get("expression") or "").strip()

        if not original:
            self.fallback_count += 1
            return self._result(name, original, "fallback")

        if (_SET_ANALYSIS_RE.search(original) or _DOLLAR_EXPAND_RE.search(original)
                or _AGGR_RE.search(original) or _TOTAL_RE.search(original)):
            self.fallback_count += 1
            log.debug("Fallback (set analysis / dynamic) for measure %s", name)
            return self._result(name, original, "fallback")

        try:
            dax = self._tokenize_and_convert(original)
        except UnknownFunction as e:
            self.fallback_count += 1
            log.debug("Fallback (unknown function %s) for measure %s", e, name)
            return self._result(name, original, "fallback")
        except UnknownColumn as e:
            self.fallback_count += 1
            log.debug("Fallback (unresolved column %s) for measure %s", e, name)
            return self._result(name, original, "fallback")
        except Exception as e:  # noqa: BLE001 - converter must never crash a run
            self.failed_count += 1
            log.warning("Conversion failed for measure %s: %s", name, e)
            return self._result(name, original, "failed")

        self.converted_count += 1
        return {"name": name, "original": original,
                "dax_expression": dax, "status": "converted"}

    def _tokenize_and_convert(self, expr: str) -> str:
        tokens = list(_TOKEN_RE.finditer(expr))
        out: List[str] = []
        for i, tok in enumerate(tokens):
            kind = tok.lastgroup
            text = tok.group()
            if kind == "field":
                # [Bracketed Field Name] is always a column reference.
                out.append(self._qualify_column(text[1:-1].strip()))
            elif kind == "ident":
                low = text.lower()
                if low in _OPERATOR_WORDS:
                    out.append(_OPERATOR_WORDS[low])
                elif self._is_function_call(tokens, i):
                    out.append(self._map_function(text))
                elif low in _KEYWORDS:
                    out.append(text)
                else:
                    out.append(self._qualify_column(text))
            else:
                out.append(text)
        return "".join(out)

    @staticmethod
    def _is_function_call(tokens, i) -> bool:
        """True if the identifier at index i is immediately followed (skipping
        whitespace) by an opening parenthesis."""
        j = i + 1
        while j < len(tokens) and tokens[j].lastgroup == "ws":
            j += 1
        return j < len(tokens) and tokens[j].group() == "("

    def _map_function(self, name: str) -> str:
        dax = _FUNC_MAP.get(name.lower())
        if dax is None:
            raise UnknownFunction(name)
        return dax

    def _qualify_column(self, field: str) -> str:
        table = self.column_table_map.get(field)
        if table is None:
            # With a real model loaded, an unmapped column means we can't be sure
            # which table it belongs to -> fall back rather than invent 'T'.
            if self.column_table_map:
                raise UnknownColumn(field)
            table = DEFAULT_TABLE
        return f"'{table}'[{field}]"

    @staticmethod
    def _result(name: str, original: str, status: str) -> dict:
        # Comment EVERY line of the original so a multi-line Qlik expression
        # can't leak uncommented lines into the DAX. The measure evaluates to 0.
        commented = "\n".join("-- " + ln for ln in (original or "").split("\n"))
        placeholder = (
            "-- TODO: Manual conversion required\n"
            "-- Original Qlik:\n"
            f"{commented}\n"
            "0  // placeholder"
        )
        return {"name": name, "original": original,
                "dax_expression": placeholder, "status": status}
