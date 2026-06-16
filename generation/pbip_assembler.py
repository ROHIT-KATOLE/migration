"""Assemble the ``.pbip`` project and its ``<App>.Report`` folder.

Produces the PBIR report tree that pairs with the ``<App>.SemanticModel`` the
semantic_model generator wrote:

    <App>.pbip
    <App>.Report/
        .platform
        definition.pbir              -> datasetReference byPath ../<App>.SemanticModel
        definition/
            version.json
            report.json
            pages/
                pages.json
                <page>/
                    page.json
                    visuals/<id>/visual.json

Folder names use the project name (== output dir name), so the report and the
semantic model reference each other consistently. Schema URLs and structure are
harvested from cyphou/Qlik-To-PowerBI (powerbi_import/pbip_generator.py) and
verified against a real Desktop PBIR export.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import List

from .visual_schemas import PAGE_HEIGHT, PAGE_WIDTH

log = logging.getLogger("migrate.pbip")

_NS = uuid.UUID("99999999-8888-7777-6666-555555555555")


def _logical_id(seed: str) -> str:
    return str(uuid.uuid5(_NS, seed))


class PBIPAssembler:
    def __init__(self, app_name: str, out_dir: Path):
        self.app_name = app_name
        self.out = Path(out_dir)
        self.project = self.out.name  # keep report/model folder names aligned

    def assemble(self, pages: List[dict]) -> Path:
        self.out.mkdir(parents=True, exist_ok=True)
        pbip_path = self._write_pbip()
        report_dir = self.out / f"{self.project}.Report"
        self._write_report_shell(report_dir)
        self._write_pages(report_dir / "definition" / "pages", pages)
        log.info("Assembled .pbip with %d page(s)", len(pages))
        return pbip_path

    # ── .pbip root ───────────────────────────────────────────────────

    def _write_pbip(self) -> Path:
        pbip = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                       "pbip/pbipProperties/1.0.0/schema.json",
            "version": "1.0",
            "artifacts": [{"report": {"path": f"{self.project}.Report"}}],
            "settings": {"enableAutoRecovery": True},
        }
        path = self.out / f"{self.project}.pbip"
        _write_json(path, pbip)
        _write_text(self.out / ".gitignore", ".pbi/\n")
        return path

    # ── report shell ─────────────────────────────────────────────────

    def _write_report_shell(self, report_dir: Path):
        def_dir = report_dir / "definition"
        def_dir.mkdir(parents=True, exist_ok=True)

        _write_json(report_dir / ".platform", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                       "gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": "Report", "displayName": self.project},
            "config": {"version": "2.0", "logicalId": _logical_id("report:" + self.project)},
        })

        _write_json(report_dir / "definition.pbir", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                       "item/report/definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {
                "byPath": {"path": f"../{self.project}.SemanticModel"},
            },
        })

        _write_json(def_dir / "version.json", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                       "item/report/definition/versionMetadata/1.0.0/schema.json",
            "version": "2.0.0",
        })

        # report.json (report/1.0.0): requires themeCollection + layoutOptimization
        # and forbids any other top-level property (no "version").
        _write_json(def_dir / "report.json", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                       "item/report/definition/report/1.0.0/schema.json",
            "themeCollection": {
                "baseTheme": {
                    "name": "CY24SU06",
                    "type": "SharedResources",
                    "reportVersionAtImport": "5.55",
                },
            },
            "layoutOptimization": "None",
        })

    # ── pages & visuals ──────────────────────────────────────────────

    def _write_pages(self, pages_dir: Path, pages: List[dict]):
        pages_dir.mkdir(parents=True, exist_ok=True)
        if not pages:
            # A PBIR report must have at least one page. When extraction found no
            # sheets/visuals, emit a single blank page so the project still opens.
            log.warning("No pages extracted; writing one blank page")
            pages = [{"name": "DefaultPage", "displayName": "Page 1",
                      "ordinal": 0, "visuals": []}]
        page_names = []
        for page in pages:
            page_name = page["name"]
            page_names.append(page_name)
            page_dir = pages_dir / page_name
            visuals_dir = page_dir / "visuals"
            visuals_dir.mkdir(parents=True, exist_ok=True)

            _write_json(page_dir / "page.json", {
                "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                           "item/report/definition/page/1.0.0/schema.json",
                "name": page_name,
                "displayName": page["displayName"],
                "displayOption": "FitToPage",
                "width": PAGE_WIDTH,
                "height": PAGE_HEIGHT,
            })

            for viz_id, visual in page["visuals"]:
                vdir = visuals_dir / visual["name"]
                vdir.mkdir(parents=True, exist_ok=True)
                _write_json(vdir / "visual.json", visual)

        _write_json(pages_dir / "pages.json", {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                       "item/report/definition/pagesMetadata/1.0.0/schema.json",
            "pageOrder": page_names,
            "activePageName": page_names[0] if page_names else "",
        })


# ── io helpers ───────────────────────────────────────────────────────

def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def _write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
