"""Read a ``corectl unbuild`` output folder into the same ``RawApp`` the
binary reader produces.

This is the **primary, reliable** extraction path. ``corectl unbuild`` (run
against a Qlik Associative Engine — see ``docker/`` and ``tools/qlik_unbuild.py``)
exports an app's measures, dimensions, generic objects (sheets with their child
charts), the reload script and connections as JSON/YAML + a ``corectl.yml``.

Because corectl emits the public Qlik Engine object model — exactly the shapes
``extractor.py`` already maps (``qInfo`` / ``qMeasure`` / ``qDim`` /
``qHyperCubeDef`` / sheet ``cells``) — this reader just loads the folder into the
shared ``RawApp`` and the rest of the pipeline is unchanged.

It is layout-agnostic on purpose: it recursively globs every ``*.json``,
classifies each object by content, and flattens sheet child trees (``qChildren``
/ ``qChildList``). That tolerates corectl version differences in file naming.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List

from .qvf_reader import (
    QVFReadError,
    QlikObject,
    RawApp,
    _explode,
    _obj_id,
    _payload_qtype,
    _payload_title,
)

log = logging.getLogger("migrate.corectl")

# corectl wrapper / config files we must not treat as app objects.
_SKIP_FILES = {"corectl.yml", "corectl.yaml", "connections.yml", "connections.yaml"}
_SCRIPT_EXT = {".qvs", ".txt"}
_APP_QTYPES = {"app", "appprops", "appproperties"}


class CorectlReader:
    def __init__(self, unbuild_dir: Path):
        self.dir = Path(unbuild_dir)

    def read(self) -> RawApp:
        if not self.dir.is_dir():
            raise QVFReadError(f"Not an unbuild directory: {self.dir}")

        json_files = [p for p in self.dir.rglob("*.json")
                      if p.name.lower() not in _SKIP_FILES]
        scripts = self._read_scripts()

        collected: List[dict] = []
        for path in json_files:
            obj = _load_json(path)
            if isinstance(obj, dict):
                _flatten(obj, collected)
            elif isinstance(obj, list):
                for item in obj:
                    if isinstance(item, dict):
                        _flatten(item, collected)

        objects, app_meta = self._build_objects(collected)

        if not objects and not scripts:
            raise QVFReadError(
                f"No Qlik objects found under {self.dir}. Is this a corectl "
                "unbuild folder? Expected per-object .json files and a .qvs script.")

        app = app_meta or {"name": self._app_name_from_config()}
        log.info(
            "Read unbuild %s: %d objects (%d measures, %d dimensions, "
            "%d sheets, %d visuals), %d script(s)",
            self.dir.name, len(objects),
            sum(o.qtype == "measure" for o in objects),
            sum(o.qtype == "dimension" for o in objects),
            sum(o.qtype == "sheet" for o in objects),
            sum(_is_visual_type(o.qtype) for o in objects),
            len(scripts))
        return RawApp(app=app, objects=objects, scripts=scripts)

    # ── internals ────────────────────────────────────────────────────

    def _read_scripts(self) -> List[str]:
        scripts = []
        for path in self.dir.rglob("*"):
            if path.suffix.lower() in _SCRIPT_EXT and path.is_file():
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if text.strip():
                    scripts.append(text)
        return scripts

    def _build_objects(self, collected: List[dict]):
        objects: List[QlikObject] = []
        app_meta: Dict[str, Any] = {}
        seen = set()
        for props in collected:
            qtype = _payload_qtype(props) or "unknown"
            oid = _obj_id(props) or ""
            if qtype in _APP_QTYPES:
                app_meta = _app_meta_from_props(props)
                continue
            key = oid or id(props)
            if key in seen:
                continue
            seen.add(key)
            objects.append(QlikObject(
                id=oid or qtype,
                qtype=qtype,
                props=props,
                title=_payload_title(props),
                description=_nested(props, "qMetaDef", "description"),
                meta=None,
            ))
        return objects, app_meta

    def _app_name_from_config(self) -> str:
        cfg = self.dir / "corectl.yml"
        if cfg.exists():
            try:
                text = cfg.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            m = re.search(r"^\s*app:\s*['\"]?([^'\"\n]+)", text, re.MULTILINE)
            if m:
                name = Path(m.group(1).strip()).stem
                if name:
                    return name
        # Fall back to the folder name, stripping a trailing -unbuild.
        return re.sub(r"-unbuild$", "", self.dir.name) or "Migrated Qlik App"


# ── flattening & helpers ─────────────────────────────────────────────

def _flatten(obj: dict, collected: List[dict]):
    """Collect this object and any nested child objects (sheet → charts).

    Shares the binary reader's tree walker, which handles the qRoot / qProperty
    wrappers and qChildren / qChildList nesting uniformly.
    """
    if isinstance(obj, dict):
        collected.extend(_explode(obj))


def _app_meta_from_props(props: dict) -> Dict[str, Any]:
    meta = props.get("qMetaDef") or props.get("qMeta") or {}
    return {
        "name": (meta.get("title") or props.get("qTitle")
                 or props.get("title")),
        "description": meta.get("description") or props.get("description"),
        "theme": props.get("theme"),
    }


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Skipping unreadable JSON %s: %s", path.name, e)
        return None


def _nested(d: Any, *keys) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _is_visual_type(qtype: str) -> bool:
    from .qvf_reader import _is_visual_type as visual
    return visual(qtype)
