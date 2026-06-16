"""Drive ``corectl unbuild`` against a Qlik engine to export apps to JSON.

Step 2 of the pipeline (after the engine is up — see docker/docker-compose.yml,
before migrate.py). For each app it runs ``corectl unbuild`` against the engine
and writes one folder per app under ``--out``, which ``migrate.py`` then consumes.

The engine must already see the app: with the Docker engine, drop the .qvf into
``docker/apps/`` first; ``corectl --app`` refers to it by file name.

Usage:
    python tools/qlik_unbuild.py "Global SIOP Dashboard.qvf" --out unbuild
    python tools/qlik_unbuild.py docker/apps/*.qvf --engine localhost:9076 --out unbuild

Requires the ``corectl`` binary on PATH (https://github.com/qlik-oss/corectl),
or pass ``--corectl <path>``.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def unbuild_one(app: str, engine: str, out_dir: Path, corectl: str) -> bool:
    app_name = Path(app).name           # engine refers to apps by file name
    target = out_dir / Path(app).stem
    cmd = [corectl, "unbuild", "--engine", engine, "--app", app_name,
           "--dir", str(target)]
    print(f"  $ {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except FileNotFoundError:
        print(f"ERROR: '{corectl}' not found. Install corectl: "
              "https://github.com/qlik-oss/corectl/releases", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print(f"ERROR: corectl timed out for {app_name}", file=sys.stderr)
        return False
    if proc.returncode != 0:
        print(f"ERROR unbuilding {app_name}:\n{proc.stderr.strip()}", file=sys.stderr)
        return False
    print(f"  ✓ {app_name} -> {target}")
    return True


def main():
    p = argparse.ArgumentParser(description="Unbuild Qlik apps to JSON via corectl")
    p.add_argument("apps", nargs="+", help="app file names or .qvf paths")
    p.add_argument("--engine", default="localhost:9076",
                   help="Qlik engine host:port (default localhost:9076)")
    p.add_argument("--out", default="unbuild", help="output root folder")
    p.add_argument("--corectl", default="corectl", help="corectl binary path")
    args = p.parse_args()

    if not shutil.which(args.corectl) and not Path(args.corectl).exists():
        print(f"WARNING: '{args.corectl}' not found on PATH. Install the corectl "
              "binary from https://github.com/qlik-oss/corectl/releases",
              file=sys.stderr)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    ok = 0
    for app in args.apps:
        if unbuild_one(app, args.engine, out_dir, args.corectl):
            ok += 1

    print(f"\nUnbuilt {ok}/{len(args.apps)} app(s) into {out_dir}/")
    if ok:
        folders = " ".join(f'"{out_dir / Path(a).stem}"' for a in args.apps)
        print("Next:")
        print(f"  python migrate.py {folders} --verbose")
        print(f"  # or, to combine layered E/T/UI apps into one report:")
        print(f"  python migrate.py {folders} --merge --merge-name \"Global SIOP\"")
    sys.exit(0 if ok == len(args.apps) else 1)


if __name__ == "__main__":
    main()
