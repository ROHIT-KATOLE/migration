#!/usr/bin/env python3
"""Qlik to Power BI Migration Tool — convert .qvf apps into Power BI .pbip projects.

Accepts one or more .qvf files. By default each becomes its own .pbip; with
``--merge`` they are combined into a single project (for layered Extract /
Transform / UI Qlik apps that together form one dashboard).
"""
import argparse
import logging
import sys
from pathlib import Path

import re

from extraction.qvf_reader import QVFReader, QVFReadError
from extraction.corectl_reader import CorectlReader
from extraction.extractor import Extractor, ExtractionError, merge_apps
from generation.dax_converter import DAXConverter
from generation.semantic_model import SemanticModelGenerator
from generation.visual_builder import VisualBuilder
from generation.pbip_assembler import PBIPAssembler
from generation.validator import validate_project

# Windows consoles default to cp1252; force UTF-8 so status glyphs print.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

log = logging.getLogger("migrate")


def _column_table_map(extracted) -> dict:
    mapping = {}
    for ds in extracted.datasources:
        for f in ds.fields:
            mapping.setdefault(f.name, ds.table_name)
    return mapping


def _extract(source: Path):
    """Read + validate one input into an ExtractedApp.

    A directory is treated as a ``corectl unbuild`` folder (the reliable,
    engine-backed path); a ``.qvf`` file uses the best-effort binary reader.
    """
    log.info("Reading %s", source.name)
    if source.is_dir():
        raw = CorectlReader(source).read()
    else:
        raw = QVFReader(source).read()
    return Extractor(raw).extract()


def _app_name(source: Path) -> str:
    if source.is_dir():
        return re.sub(r"-unbuild$", "", source.name) or source.name
    return source.stem


def _generate(extracted, app_name: str, out: Path) -> Path:
    """Run the full generation pipeline for one app, returning the .pbip path."""
    out.mkdir(parents=True, exist_ok=True)
    dax = DAXConverter(extracted.measures, _column_table_map(extracted))
    measures = dax.convert_all()
    log.info("  DAX: %d converted, %d fallback, %d failed",
             dax.converted_count, dax.fallback_count, dax.failed_count)

    SemanticModelGenerator(extracted, measures).generate(out)
    pages = VisualBuilder(extracted).build_pages()
    pbip = PBIPAssembler(app_name, out).assemble(pages)

    result = validate_project(pbip)
    for warn in result.warnings:
        log.warning("  ! %s", warn)
    if not result.valid:
        for err in result.errors:
            log.error("  ✗ %s", err)
        raise RuntimeError(f"Post-generation validation failed for {app_name}")

    total_visuals = sum(len(pg["visuals"]) for pg in pages)
    print(f"\n✓ {app_name} -> {out}")
    print(f"  Tables: {len(extracted.datasources)} | Measures: {len(measures)} | "
          f"Pages: {len(pages)} | Visuals: {total_visuals} | "
          f"DAX: {dax.converted_count} converted, {dax.fallback_count} fallback")
    print(f"  Open in Power BI Desktop (Developer Mode): {pbip}")
    return pbip


def main():
    p = argparse.ArgumentParser(description="Convert Qlik .qvf apps to Power BI .pbip")
    p.add_argument("qvf_files", nargs="+", help="one or more .qvf files")
    p.add_argument("--output-dir", default="output")
    p.add_argument("--merge", action="store_true",
                   help="combine all inputs into a single .pbip "
                        "(for layered Extract/Transform/UI apps)")
    p.add_argument("--merge-name", default="Merged Dashboard",
                   help="project name when --merge is used")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s")

    paths = [Path(f) for f in args.qvf_files]
    missing = [str(p_) for p_ in paths if not p_.exists()]
    if missing:
        log.error("File(s) not found: %s", ", ".join(missing))
        sys.exit(1)

    out_root = Path(args.output_dir)
    failures = 0

    try:
        if args.merge:
            apps = []
            for qvf in paths:
                app = _extract(qvf)
                if app:
                    apps.append(app)
            if not apps:
                log.error("Nothing extracted from any input")
                sys.exit(1)
            name = args.merge_name
            merged = merge_apps(apps, name)
            _generate(merged, _safe_name(name), out_root / _safe_name(name))
        else:
            for qvf in paths:
                try:
                    app = _extract(qvf)
                    name = _app_name(qvf)
                    _generate(app, name, out_root / name)
                except (QVFReadError, ExtractionError, RuntimeError) as e:
                    failures += 1
                    log.error("Migration failed for %s: %s", qvf.name, e)
    except Exception as e:  # noqa: BLE001 - top-level guard, full traceback
        log.error("Migration failed: %s", e, exc_info=True)
        sys.exit(1)

    if failures:
        sys.exit(1)


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\- ]", "_", name).strip() or "Merged"


if __name__ == "__main__":
    main()
