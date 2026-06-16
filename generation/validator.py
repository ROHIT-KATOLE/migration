"""Post-generation validation of an assembled .pbip project.

Checks the folder structure is complete, every ``visual.json`` parses and has
the required PBIR keys, every ``.tmdl`` file is non-empty, and the report points
at the semantic model that actually exists. Returns a structured result with
specific errors — nothing fails silently.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

log = logging.getLogger("migrate.validator")


@dataclass
class ValidationResult:
    valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    checked: int = 0

    def error(self, msg: str):
        self.valid = False
        self.errors.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)


def validate_project(pbip_path: Path) -> ValidationResult:
    """Validate the .pbip and the report/model trees beside it."""
    res = ValidationResult()
    pbip = Path(pbip_path)

    if not pbip.exists():
        res.error(f"Missing .pbip file: {pbip}")
        return res

    project = pbip.stem
    root = pbip.parent
    report_dir = root / f"{project}.Report"
    model_dir = root / f"{project}.SemanticModel"

    # ── structure ────────────────────────────────────────────────────
    for required in (
        report_dir,
        report_dir / "definition.pbir",
        report_dir / "definition" / "report.json",
        report_dir / "definition" / "pages" / "pages.json",
        model_dir,
        model_dir / "definition.pbism",
        model_dir / "definition" / "model.tmdl",
        model_dir / "definition" / "database.tmdl",
    ):
        if not required.exists():
            res.error(f"Missing required path: {required.relative_to(root)}")

    # ── report.json is schema-shaped (themeCollection + layoutOptimization,
    #    and NO disallowed extra props like 'version') ──────────────────
    report_json = report_dir / "definition" / "report.json"
    if report_json.exists():
        try:
            rj = json.loads(report_json.read_text(encoding="utf-8"))
            for required in ("themeCollection", "layoutOptimization"):
                if required not in rj:
                    res.error(f"report.json missing required '{required}'")
            if "version" in rj:
                res.error("report.json has disallowed 'version' property")
        except (json.JSONDecodeError, OSError) as e:
            res.error(f"Cannot read report.json: {e}")

    # ── dataset reference resolves ───────────────────────────────────
    pbir = report_dir / "definition.pbir"
    if pbir.exists():
        try:
            ref = json.loads(pbir.read_text(encoding="utf-8"))
            path = ref.get("datasetReference", {}).get("byPath", {}).get("path", "")
            target = (report_dir / path).resolve()
            if target != model_dir.resolve():
                res.error(
                    f"definition.pbir datasetReference '{path}' does not point "
                    f"at {model_dir.name}")
        except (json.JSONDecodeError, OSError) as e:
            res.error(f"Cannot read definition.pbir: {e}")

    # ── TMDL files non-empty and parse-ish ───────────────────────────
    tables_dir = model_dir / "definition" / "tables"
    if tables_dir.exists():
        tmdls = list(tables_dir.glob("*.tmdl"))
        if not tmdls:
            res.error("No table .tmdl files generated")
        for tmdl in tmdls:
            res.checked += 1
            text = tmdl.read_text(encoding="utf-8").strip()
            if not text:
                res.error(f"Empty TMDL file: {tmdl.name}")
            elif not text.startswith("table "):
                res.error(f"TMDL {tmdl.name} does not start with 'table '")

    # ── every visual.json parses + has required keys ─────────────────
    pages_dir = report_dir / "definition" / "pages"
    visual_count = 0
    if pages_dir.exists():
        for visual_json in pages_dir.glob("*/visuals/*/visual.json"):
            res.checked += 1
            visual_count += 1
            try:
                visual = json.loads(visual_json.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                res.error(f"Invalid JSON in {visual_json.name}: {e}")
                continue
            _check_visual(visual, visual_json, res)

        for page_json in pages_dir.glob("*/page.json"):
            res.checked += 1
            try:
                json.loads(page_json.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                res.error(f"Invalid JSON in {page_json}: {e}")

    if visual_count == 0:
        res.warn("No visuals were generated")

    log.debug("Validation checked %d artifact(s)", res.checked)
    return res


def _check_visual(visual: dict, path: Path, res: ValidationResult):
    for key in ("name", "position", "visual"):
        if key not in visual:
            res.error(f"{path.parent.name}/visual.json missing '{key}'")
            return
    v = visual["visual"]
    if "visualType" not in v:
        res.error(f"{path.parent.name}/visual.json missing visualType")
        return
    if v["visualType"] == "textbox":
        return
    if not v.get("query", {}).get("queryState"):
        res.error(
            f"{path.parent.name}/visual.json ({v['visualType']}) missing "
            "query.queryState")
