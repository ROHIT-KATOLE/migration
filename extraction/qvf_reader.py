"""Read a Qlik Sense ``.qvf`` (proprietary binary) into raw Qlik Engine objects.

A ``.qvf`` is a container of named entries. Each entry has a plaintext JSON
header, e.g.::

    {"ContentHash":"…","Format":"gzjson","ParentId":"MBCxFRQ",
     "Type":"GenericMeasureProperties","SecurityMetaAsBase64":"<base64 json>"}

and a payload that, for ``"Format":"gzjson"``, is **gzip/zlib-compressed JSON**
following the public Qlik Engine API object model (``qInfo``, ``qMeasure``,
``qDim``, ``qHyperCubeDef``, sheet ``cells``/``qChildList`` …). The load script
is stored the same way.

This reader is format-general — it works on any Qlik Sense app, not specific
files. It does three things and nothing app-specific:

1. Inflate every compressed stream and ``json.loads`` what it can.
2. Parse the plaintext entry headers and decode their base64 metadata
   (object id, type, title, description).
3. Correlate payloads with headers by object id and return typed
   ``QlikObject`` records plus any recovered load-script text.

Interpretation of those objects into the domain model lives in ``extractor.py``;
the reader stays a pure, defensive decoder so it never crashes on an unexpected
app and degrades to "found less" rather than failing.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("migrate.reader")

# Compressed-stream signatures: gzip and the three common zlib header bytes.
_STREAM_SIGS = (b"\x1f\x8b\x08", b"\x78\x9c", b"\x78\xda", b"\x78\x01")

# Flat header objects carrying per-entry metadata. No nested braces inside, so a
# non-greedy [^{}] match is safe and cheap even on a large file.
_HEADER_RE = re.compile(rb"\{[^{}]*\"SecurityMetaAsBase64\"[^{}]*\}")

# Heuristics for spotting a Qlik load script among decoded blobs.
_SCRIPT_HINT = re.compile(r"\b(LOAD|SQL\s+SELECT|RESIDENT|AUTOGENERATE|CONNECT)\b",
                          re.IGNORECASE)

# Cap a single inflated blob so a pathological/giant data partition can't OOM us.
_MAX_BLOB = 64 * 1024 * 1024


class QVFReadError(RuntimeError):
    """Raised when a file cannot be opened/parsed as a Qlik Sense app at all."""


@dataclass
class QlikObject:
    """One recovered app object, payload + metadata correlated by id."""
    id: str
    qtype: str                      # measure | dimension | sheet | <viz type> | …
    props: Optional[dict] = None    # decoded gzjson payload (Engine object)
    title: Optional[str] = None
    description: Optional[str] = None
    meta: Optional[dict] = None     # decoded SecurityMetaAsBase64


@dataclass
class RawApp:
    """Everything the reader recovered from one .qvf."""
    app: Dict[str, Any] = field(default_factory=dict)   # name, description, theme
    objects: List[QlikObject] = field(default_factory=list)
    scripts: List[str] = field(default_factory=list)

    def by_type(self, qtype: str) -> List[QlikObject]:
        return [o for o in self.objects if o.qtype == qtype]


class QVFReader:
    def __init__(self, qvf_path: Path):
        self.path = Path(qvf_path)

    # ── public ───────────────────────────────────────────────────────

    def read(self) -> RawApp:
        if not self.path.exists():
            raise QVFReadError(f"File not found: {self.path}")
        data = self.path.read_bytes()
        if len(data) < 16:
            raise QVFReadError(f"File too small to be a Qlik app: {self.path}")

        payloads, raw_texts = self._inflate_streams(data)
        headers = self._parse_headers(data)

        if not payloads and not headers:
            raise QVFReadError(
                f"No Qlik objects found in {self.path.name}. The file may be "
                "encrypted (Qlik Cloud managed keys) or not a Qlik Sense .qvf. "
                "Ask the Qlik admin for an unencrypted export.")

        app = self._app_metadata(headers)
        if not app.get("name"):
            app = _app_from_payloads(payloads) or app
        objects = self._correlate(payloads, headers)
        scripts = self._recover_scripts(payloads, raw_texts)

        log.info(
            "Read %s: %d objects (%d measures, %d dimensions, %d sheets, "
            "%d visuals), %d script blob(s)",
            self.path.name, len(objects),
            sum(o.qtype == "measure" for o in objects),
            sum(o.qtype == "dimension" for o in objects),
            sum(o.qtype == "sheet" for o in objects),
            sum(_is_visual_type(o.qtype) for o in objects),
            len(scripts),
        )
        return RawApp(app=app, objects=objects, scripts=scripts)

    # ── stream inflation ─────────────────────────────────────────────

    def _inflate_streams(self, data: bytes):
        """Find and inflate every compressed stream.

        Returns (payloads, raw_texts) where payloads are decoded JSON objects
        and raw_texts are all successfully-inflated UTF-8 strings (used later
        to fish out the load script, which may not be a JSON object).
        """
        offsets = sorted({m.start()
                          for sig in _STREAM_SIGS
                          for m in re.finditer(re.escape(sig), data)})
        payloads: List[dict] = []
        raw_texts: List[str] = []
        seen_ids = set()
        for off in offsets:
            raw = self._try_inflate(data, off)
            if raw is None or len(raw) < 8:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            raw_texts.append(text)
            obj = _loads(text)
            if isinstance(obj, dict):
                # Flatten nested object trees (a sheet carries its charts in
                # qChildren), so visuals are recovered even when not stored as
                # their own top-level entry.
                for node in _explode(obj):
                    oid = _obj_id(node)
                    key = oid or id(node)
                    if key in seen_ids:
                        continue
                    seen_ids.add(key)
                    payloads.append(node)
        return payloads, raw_texts

    @staticmethod
    def _try_inflate(data: bytes, off: int) -> Optional[bytes]:
        # wbits=47 auto-detects gzip and zlib headers.
        try:
            d = zlib.decompressobj(47)
            out = d.decompress(data[off:off + _MAX_BLOB], _MAX_BLOB)
            out += d.flush()
            return out or None
        except (zlib.error, OverflowError):
            return None

    # ── header parsing ───────────────────────────────────────────────

    def _parse_headers(self, data: bytes) -> List[dict]:
        headers = []
        for m in _HEADER_RE.finditer(data):
            hdr = _loads(m.group().decode("utf-8", "ignore"))
            if not isinstance(hdr, dict):
                continue
            meta = _decode_meta(hdr.get("SecurityMetaAsBase64"))
            hdr["_meta"] = meta or {}
            headers.append(hdr)
        return headers

    @staticmethod
    def _app_metadata(headers: List[dict]) -> Dict[str, Any]:
        for hdr in headers:
            meta = hdr.get("_meta", {})
            if (hdr.get("Type") == "NxAppProperties"
                    or meta.get("_resourcetype") == "app"
                    or meta.get("_objecttype") == "app"):
                return {
                    "name": meta.get("name") or meta.get("title"),
                    "description": meta.get("description"),
                    "theme": meta.get("theme"),
                }
        return {}

    # ── correlation ──────────────────────────────────────────────────

    def _correlate(self, payloads: List[dict], headers: List[dict]) -> List[QlikObject]:
        # Index metadata (title/type/description) by object id.
        meta_by_id: Dict[str, dict] = {}
        for hdr in headers:
            meta = hdr.get("_meta", {})
            oid = meta.get("id") or hdr.get("ParentId")
            if oid:
                meta_by_id[oid] = meta

        objects: List[QlikObject] = []
        used_ids = set()
        for props in payloads:
            if _is_app_props(props):
                continue   # app layout object — used for metadata, not an object
            oid = _obj_id(props) or ""
            meta = meta_by_id.get(oid, {})
            qtype = (meta.get("_objecttype")
                     or _payload_qtype(props)
                     or "unknown")
            if qtype in ("app", "ap,object", "LoadModel"):
                continue
            objects.append(QlikObject(
                id=oid or qtype,
                qtype=qtype,
                props=props,
                title=meta.get("title") or _payload_title(props),
                description=meta.get("description"),
                meta=meta or None,
            ))
            if oid:
                used_ids.add(oid)

        # Header-only objects (metadata but no recovered payload) still count —
        # e.g. a master measure whose definition blob didn't inflate.
        for oid, meta in meta_by_id.items():
            if oid in used_ids:
                continue
            qtype = meta.get("_objecttype")
            if qtype in (None, "app", "appprops", "LoadModel"):
                continue
            objects.append(QlikObject(
                id=oid, qtype=qtype, props=None,
                title=meta.get("title"), description=meta.get("description"),
                meta=meta,
            ))
        return objects

    # ── script recovery ──────────────────────────────────────────────

    @staticmethod
    def _recover_scripts(payloads: List[dict], raw_texts: List[str]) -> List[str]:
        scripts: List[str] = []
        # Engine stores the script as a string field on a script/app object.
        for obj in payloads:
            for key in ("qScript", "script", "qStringExpression"):
                val = obj.get(key) if isinstance(obj, dict) else None
                if isinstance(val, str) and _SCRIPT_HINT.search(val):
                    scripts.append(val)
        # Fallback: any inflated text blob that clearly *is* a script.
        if not scripts:
            for text in raw_texts:
                if text.lstrip().startswith(("{", "[")):
                    continue
                if _SCRIPT_HINT.search(text) and "LOAD" in text.upper():
                    scripts.append(text)
        return scripts


# ── module helpers ───────────────────────────────────────────────────

def _is_app_props(obj: Any) -> bool:
    """The app layout object: carries qTitle + reload/usage metadata, no qInfo."""
    return (isinstance(obj, dict) and "qTitle" in obj
            and any(k in obj for k in
                    ("qLastReloadTime", "qUsage", "qHasSectionAccess")))


def _app_from_payloads(payloads: List[dict]) -> Dict[str, Any]:
    for obj in payloads:
        if _is_app_props(obj):
            return {"name": obj.get("qTitle"),
                    "description": obj.get("description"),
                    "theme": obj.get("theme")}
    return {}


def _explode(obj: dict) -> List[dict]:
    """Flatten an object tree into individual objects.

    Qlik stores a sheet (and its child charts) as a full property tree. Depending
    on the export, the real object hides under ``qRoot`` and/or ``qProperty``, and
    children sit in ``qChildren`` / ``qChildList``. This walks all of those so
    every sheet and visual surfaces as its own object.
    """
    out: List[dict] = []

    def walk(node: Any):
        if not isinstance(node, dict):
            return
        # {"qMetaData":..., "qRoot": <tree>} -> descend into the tree.
        if isinstance(node.get("qRoot"), dict):
            walk(node["qRoot"])
            return
        real = node.get("qProperty") if isinstance(node.get("qProperty"), dict) else node
        if _payload_qtype(real):
            out.append(real)
        # Children can hang off either the node or its qProperty.
        srcs = (node,) if real is node else (node, real)
        for src in srcs:
            for child_key in ("qChildren", "qChildList"):
                sub = src.get(child_key)
                items = sub.get("qItems") if isinstance(sub, dict) else sub
                for child in items or []:
                    walk(child)

    walk(obj)
    return out or [obj]


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Engine blobs sometimes carry a trailing NUL or padding.
        try:
            return json.loads(text.rstrip("\x00").strip())
        except (json.JSONDecodeError, ValueError):
            return None


def _decode_meta(b64: Optional[str]) -> Optional[dict]:
    if not b64:
        return None
    try:
        raw = base64.b64decode(b64)
    except (binascii.Error, ValueError):
        return None
    text = raw.rstrip(b"\x00").decode("utf-8", "ignore")
    return _loads(text)


def _obj_id(obj: dict) -> Optional[str]:
    info = obj.get("qInfo") if isinstance(obj, dict) else None
    if isinstance(info, dict):
        return info.get("qId")
    return obj.get("qId") if isinstance(obj, dict) else None


def _payload_qtype(obj: dict) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    # The visualization field is the authoritative chart type when present.
    if isinstance(obj.get("visualization"), str):
        return obj["visualization"]
    info = obj.get("qInfo")
    if isinstance(info, dict) and info.get("qType"):
        return info["qType"]
    return None


def _payload_title(obj: dict) -> Optional[str]:
    meta = obj.get("qMetaDef") if isinstance(obj, dict) else None
    if isinstance(meta, dict) and meta.get("title"):
        return meta["title"]
    title = obj.get("title")
    return title if isinstance(title, str) else None


# Qlik visualization object types we understand downstream.
_VISUAL_TYPES = {
    "barchart", "combochart", "linechart", "piechart", "scatterplot",
    "table", "pivot-table", "kpi", "gauge", "treemap", "map", "boxplot",
    "distributionplot", "histogram", "waterfallchart", "bulletchart",
    "filterpane", "listbox", "sankey", "funnel", "mekkochart",
}


def _is_visual_type(qtype: str) -> bool:
    return qtype in _VISUAL_TYPES
