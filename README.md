# Qlik → Power BI Migration Tool

Converts Qlik Sense `.qvf` apps into Power BI `.pbip` projects (PBIR format) that
open in Power BI Desktop's Developer mode. General, enterprise tool — it works on
any Qlik Sense app.

## How it reads a `.qvf`

A production `.qvf` is Qlik's proprietary **binary** container (not SQLite, not a
ZIP). The object definitions inside are not parseable by any public library
without a Qlik engine — confirmed: the GitHub `qvf` topic is empty and Qlik's own
answer is "you can't read it directly without specialized tools."

So this tool uses the **industry-standard, reliable path**: Qlik's own
[`corectl unbuild`](https://github.com/qlik-oss/corectl) runs against a Qlik
Associative Engine and exports every object (measures, dimensions, sheets +
charts, reload script, connections) to JSON. We run the engine in **Docker**, so
no Qlik Sense install or server is required.

```
.qvf ──► [ Docker: qlik engine ] ──► corectl unbuild ──► JSON folder
                                                              │
                                              extraction/corectl_reader.py
                                                              │ → ExtractedApp  ⛔ validation
                                              generation/ (DAX, TMDL, visuals)
                                                              ▼
                                                       output/<App>.pbip
```

A best-effort **direct binary reader** (`extraction/qvf_reader.py`) also exists
as a fallback for apps whose payloads happen to be inflatable, but the engine
path is the supported one.

## Install

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
python -m pytest tests\ -q        # 96 tests
```

Python 3.10+, plus **Docker** (for the engine) and the
[`corectl`](https://github.com/qlik-oss/corectl/releases) binary on PATH.
The Python side uses only `pydantic` (+ `pytest`).

## The pipeline (3 steps)

```powershell
# 1. Start the Qlik engine and point it at your apps.
#    Put your .qvf files in docker/apps/ first.
cd docker
$env:ACCEPT_EULA="yes"; docker compose up -d
cd ..

# 2. Export each app to a JSON folder via corectl.
python tools/qlik_unbuild.py "Global SIOP Dashboard.qvf" `
    "T_Global SIOP Dashboard.qvf" `
    "E_Snowflake_Brazil Data from LS Sales SA database.qvf" --out unbuild

# 3. Generate the Power BI project(s).
#    Separate .pbip per app:
python migrate.py unbuild/* --verbose
#    …or merge the layered Extract/Transform/UI apps into ONE report:
python migrate.py unbuild/E_Snowflake* unbuild/T_Global* "unbuild/Global SIOP Dashboard" `
    --merge --merge-name "Global SIOP"
```

`migrate.py` accepts **either** a corectl unbuild **folder** (engine path,
recommended) **or** a raw `.qvf` **file** (best-effort binary path). Output lands
in `output/<App>/<App>.pbip` with its `.Report` and `.SemanticModel` folders.

### If the Docker engine is unavailable

`qlikcore/engine` is a deprecated Qlik Core distribution; the image may not pull
or may require a licence in some environments. If so, use **Qlik Sense Desktop**
(free) as the engine — it runs an engine locally and `corectl` can target it:

```powershell
python tools/qlik_unbuild.py "Global SIOP Dashboard.qvf" `
    --engine "wss://localhost:4848/app/engineData" --out unbuild
```

(Desktop may require extra auth headers; see the corectl docs.) The rest of the
pipeline is identical — only how the engine is hosted changes.

### Diagnostics

```bash
python tools/probe_qvf.py "App.qvf"   # header / compression / marker counts
python tools/carve_qvf.py "App.qvf"   # try to inflate object payloads to JSON
python tools/diag_qvf.py  "App.qvf"   # test every decode strategy, dump samples
```

## What actually works

- ✅ **Engine pipeline**: Docker engine + `corectl unbuild` → JSON, consumed by
  `corectl_reader.py`. Sheet child charts are flattened out of their `qChildren`
  trees; visual field refs resolve whether they point at master items
  (`qLibraryId`) or are defined inline.
- ✅ Rebuilds the **data model** (tables, fields, source types, connections,
  associations) from the recovered load script.
- ✅ Converts measure expressions to DAX (~30 unit-tested mappings); set
  analysis / `$()` / `Aggr` / `TOTAL` / unknown functions → honest TODO fallbacks.
- ✅ Generates a TMDL semantic model, 10 PBIR visual types, M for 5 connectors,
  and assembles a validated `.pbip`.
- ✅ **Multiple apps** and **layer merge** (`--merge`) for E/T/UI sets.

## Verification status

`pytest tests/` is green (**96 tests**). Both extraction paths are tested against
mock fixtures built in their *real* formats — the binary mock
(`tools/create_mock_qvf.py`) emits the actual `.qvf` container layout, and the
corectl mock (`tools/create_mock_unbuild.py`) emits the actual unbuild folder
layout — so the readers, script parser, and mapper run against true shapes.

Still requires your environment to confirm:
1. **The engine step** (`docker compose up` + `corectl unbuild`) — needs Docker
   and the `qlikcore/engine` image (or Qlik Sense Desktop) on your machine.
2. **Field-name variation** across real apps — run `corectl unbuild` then compare
   its JSON to what `migrate.py` extracts; gaps are small mapping additions in
   `extractor.py`. Unknown visual types degrade to a labelled placeholder.
3. **Desktop render check** — structure is schema-validated; "opens and renders
   with correct data" must be confirmed in Power BI Desktop. Connection details
   come from the app's `E_` layer; real credentials make the model refresh.

## Layout

```
migrate.py                 entry point (folder or .qvf input; --merge)
docker/                    docker-compose.yml (Qlik engine) + apps/ mount
extraction/                corectl_reader (primary), qvf_reader (binary fallback),
                           qlik_script_parser, extractor, schemas
generation/                dax_*, semantic_model, visual_*, m_query,
                           pbip_assembler, validator
tools/                     qlik_unbuild (corectl driver), probe/carve/diag,
                           create_mock_qvf, create_mock_unbuild
reference/                 documented format + harvested-data provenance
tests/                     pytest suite (96 tests)
```
