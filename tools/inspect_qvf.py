"""
tools/inspect_qvf.py

Run this on the REAL .qvf files at the office BEFORE running the full migration.
It tells you exactly what's inside the QVF without touching anything.

This is your ground truth — use the output to verify the extraction layer
is reading the right tables and fields.

Usage:
    python tools/inspect_qvf.py "Global SIOP Dashboard.qvf"
    python tools/inspect_qvf.py "Global SIOP Dashboard.qvf" --full
    python tools/inspect_qvf.py "Global SIOP Dashboard.qvf" --dump-script
"""

import sqlite3
import json
import argparse
import sys
from pathlib import Path

# Windows consoles default to cp1252; force UTF-8 so the ✓/✗ glyphs print.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def inspect_qvf(qvf_path: str, full: bool = False, dump_script: bool = False):
    """
    Inspect a .qvf file and print a structured summary.
    Does NOT modify anything — read-only.
    """
    path = Path(qvf_path)

    if not path.exists():
        print(f"ERROR: File not found: {qvf_path}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"QVF INSPECTOR")
    print(f"{'='*60}")
    print(f"File:  {path.name}")
    print(f"Size:  {path.stat().st_size / (1024*1024):.2f} MB")
    print()

    # ── Try to open as SQLite ──────────────────────────────────
    try:
        conn = sqlite3.connect(str(path))
        cursor = conn.cursor()
    except sqlite3.DatabaseError as e:
        print(f"ERROR: Cannot open as SQLite: {e}")
        print()
        print("Possible reasons:")
        print("  1. File is encrypted (Qlik Cloud managed keys)")
        print("  2. File is not a valid .qvf (try re-exporting from Qlik)")
        print("  3. File is corrupted")
        sys.exit(1)

    # ── List all SQLite tables ─────────────────────────────────
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    all_tables = [row[0] for row in cursor.fetchall()]

    print(f"SQLite tables found ({len(all_tables)}):")
    for t in all_tables:
        cursor.execute(f"SELECT COUNT(*) FROM [{t}]")
        count = cursor.fetchone()[0]
        print(f"  {t:<40} {count:>6} rows")

    print()

    # ── Known table checks ─────────────────────────────────────
    known = {
        "AppProperties": "App metadata (name, description, theme)",
        "DataModel":     "Tables and field definitions",
        "ScriptCode":    "Qlik load script sections",
        "SheetList":     "Report sheets/pages",
        "ObjectList":    "All visual objects (charts, KPIs, filters)",
        "MasterObjects": "Master measures and dimensions",
        "VariableList":  "Qlik variables",
        "BookmarkList":  "Saved bookmarks",
    }

    print("Known Qlik table status:")
    for table_name, description in known.items():
        if table_name in all_tables:
            cursor.execute(f"SELECT COUNT(*) FROM [{table_name}]")
            count = cursor.fetchone()[0]
            print(f"  ✓ {table_name:<20} {count:>4} rows  — {description}")
        else:
            print(f"  ✗ {table_name:<20}  NOT FOUND  — {description}")

    print()

    # ── App metadata ───────────────────────────────────────────
    if "AppProperties" in all_tables:
        print("App Metadata:")
        try:
            cursor.execute("SELECT key, value FROM AppProperties")
            for key, value in cursor.fetchall():
                if len(str(value)) > 80:
                    value = str(value)[:80] + "..."
                print(f"  {key:<25} {value}")
        except Exception as e:
            print(f"  Could not read: {e}")
        print()

    # ── Data model ─────────────────────────────────────────────
    if "DataModel" in all_tables:
        print("Data Model — Tables and Fields:")
        try:
            cursor.execute("SELECT * FROM DataModel")
            rows = cursor.fetchall()
            for row in rows:
                # Try to parse JSON field list
                table_name = row[0] if len(row) > 0 else "unknown"
                field_data = row[1] if len(row) > 1 else "[]"
                try:
                    fields = json.loads(field_data)
                    field_names = [f.get("name", f) if isinstance(f, dict) else str(f)
                                   for f in fields]
                    print(f"\n  Table: {table_name} ({len(field_names)} fields)")
                    for fn in field_names[:10]:
                        print(f"    - {fn}")
                    if len(field_names) > 10:
                        print(f"    ... and {len(field_names) - 10} more")
                except (json.JSONDecodeError, TypeError):
                    print(f"  Table: {table_name} — raw data: {str(field_data)[:100]}")
        except Exception as e:
            # Try alternate column structure
            try:
                cursor.execute("PRAGMA table_info(DataModel)")
                cols = cursor.fetchall()
                print(f"  DataModel columns: {[c[1] for c in cols]}")
                cursor.execute("SELECT * FROM DataModel LIMIT 3")
                for row in cursor.fetchall():
                    print(f"  Sample row: {str(row)[:200]}")
            except Exception as e2:
                print(f"  Could not read DataModel: {e2}")
        print()

    # ── Measures ───────────────────────────────────────────────
    if "MasterObjects" in all_tables:
        print("Master Measures:")
        try:
            cursor.execute("SELECT * FROM MasterObjects WHERE object_type = 'measure'")
            measures = cursor.fetchall()
            if measures:
                for row in measures[:10 if not full else 999]:
                    try:
                        data = json.loads(row[-1])
                        name = data.get("name", row[0])
                        expr = data.get("expression", "?")
                        if len(expr) > 60:
                            expr = expr[:60] + "..."
                        print(f"  [{name}]  →  {expr}")
                    except Exception:
                        print(f"  {str(row)[:120]}")
                if not full and len(measures) > 10:
                    print(f"  ... and {len(measures) - 10} more (use --full to see all)")
            else:
                # Try without type filter
                cursor.execute("SELECT * FROM MasterObjects LIMIT 5")
                rows = cursor.fetchall()
                print(f"  (no 'measure' type found, sample rows:)")
                for row in rows:
                    print(f"  {str(row)[:150]}")
        except Exception as e:
            print(f"  Could not read measures: {e}")
        print()

    # ── Sheets ─────────────────────────────────────────────────
    if "SheetList" in all_tables:
        print("Sheets / Pages:")
        try:
            cursor.execute("SELECT * FROM SheetList")
            sheets = cursor.fetchall()
            for row in sheets:
                try:
                    data = json.loads(row[-1])
                    name = data.get("name", row[0])
                    rank = data.get("rank", "?")
                    print(f"  [{rank}] {name}")
                except Exception:
                    print(f"  {str(row)[:120]}")
        except Exception as e:
            print(f"  Could not read sheets: {e}")
        print()

    # ── Visualizations ─────────────────────────────────────────
    if "ObjectList" in all_tables:
        print("Visualizations (ObjectList):")
        try:
            cursor.execute("SELECT object_type, COUNT(*) as cnt FROM ObjectList GROUP BY object_type ORDER BY cnt DESC")
            type_counts = cursor.fetchall()
            if type_counts:
                for obj_type, count in type_counts:
                    print(f"  {obj_type:<20} {count:>4} objects")
            else:
                cursor.execute("SELECT COUNT(*) FROM ObjectList")
                total = cursor.fetchone()[0]
                print(f"  Total objects: {total}")

            if full:
                print()
                print("  All objects:")
                cursor.execute("SELECT * FROM ObjectList")
                for row in cursor.fetchall():
                    try:
                        data = json.loads(row[-1])
                        title = data.get("title", row[0])
                        obj_type = row[1] if len(row) > 1 else "?"
                        sheet = row[2] if len(row) > 2 else "?"
                        print(f"    [{obj_type}] {title} (sheet: {sheet})")
                    except Exception:
                        print(f"    {str(row)[:150]}")
        except Exception as e:
            print(f"  Could not read objects: {e}")
        print()

    # ── Load script ────────────────────────────────────────────
    if "ScriptCode" in all_tables and dump_script:
        print("Load Script:")
        print("-" * 60)
        try:
            cursor.execute("SELECT * FROM ScriptCode")
            for row in cursor.fetchall():
                script = row[-1] if len(row) > 0 else ""
                print(script)
        except Exception as e:
            print(f"Could not read script: {e}")
        print("-" * 60)
        print()
    elif "ScriptCode" in all_tables:
        print("Load Script: (use --dump-script to display)")
        try:
            cursor.execute("SELECT LENGTH(script_content) FROM ScriptCode")
            lengths = cursor.fetchall()
            for i, (length,) in enumerate(lengths):
                print(f"  Section {i+1}: {length} characters")
        except Exception as e:
            try:
                cursor.execute("SELECT COUNT(*) FROM ScriptCode")
                count = cursor.fetchone()[0]
                print(f"  {count} sections found")
            except Exception:
                print(f"  Could not read: {e}")
        print()

    # ── Unknown tables — show samples ──────────────────────────
    unknown_tables = [t for t in all_tables if t not in known]
    if unknown_tables and full:
        print(f"Unknown tables ({len(unknown_tables)}) — may contain additional data:")
        for table_name in unknown_tables:
            try:
                cursor.execute(f"PRAGMA table_info([{table_name}])")
                cols = [c[1] for c in cursor.fetchall()]
                print(f"  {table_name}: columns = {cols}")
            except Exception:
                print(f"  {table_name}: (could not read schema)")
        print()

    conn.close()

    # ── Summary recommendation ─────────────────────────────────
    print("=" * 60)
    print("MIGRATION READINESS")
    print("=" * 60)

    has_data_model    = "DataModel"    in all_tables
    has_script        = "ScriptCode"   in all_tables
    has_sheets        = "SheetList"    in all_tables
    has_objects       = "ObjectList"   in all_tables
    has_master_items  = "MasterObjects" in all_tables

    score = sum([has_data_model, has_script, has_sheets, has_objects, has_master_items])

    print(f"  Data model present:     {'✓' if has_data_model else '✗'}")
    print(f"  Load script present:    {'✓' if has_script else '✗'}")
    print(f"  Sheets present:         {'✓' if has_sheets else '✗'}")
    print(f"  Visualizations present: {'✓' if has_objects else '✗'}")
    print(f"  Master items present:   {'✓' if has_master_items else '✗'}")
    print()

    if score == 5:
        print("  ✓ READY — all expected tables found, proceed with migration")
    elif score >= 3:
        print(f"  ⚠ PARTIAL — {score}/5 tables found, some features may not migrate")
    else:
        print(f"  ✗ NOT READY — only {score}/5 tables found")
        print("    This QVF may be encrypted or use a different internal schema")
        print("    Try exporting from Qlik as JSON instead of .qvf")

    print()
    print("  Next step:")
    if score >= 3:
        print(f"    python migrate.py \"{qvf_path}\" --verbose")
    else:
        print(f"    Contact the Qlik admin to export as JSON from Qlik Management Console")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Inspect a Qlik .qvf file structure (read-only)"
    )
    parser.add_argument("qvf_file", help="Path to .qvf file")
    parser.add_argument("--full", action="store_true",
                        help="Show all items (not just first 10)")
    parser.add_argument("--dump-script", action="store_true",
                        help="Print the full Qlik load script")
    args = parser.parse_args()

    inspect_qvf(args.qvf_file, full=args.full, dump_script=args.dump_script)
