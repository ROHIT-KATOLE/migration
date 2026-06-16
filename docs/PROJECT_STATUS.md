# Qlik → Power BI Migration — Status & Plan

**Constraint:** pure-Python only. No Qlik engine, server, Qlik Sense Desktop, or
Docker. We read the `.qvf` binary directly and generate the Power BI `.pbip`.

Last updated against the real file **`Global SIOP Dashboard.qvf`** (88 MB).

---

## 1. What we're building

```
.qvf (binary)  ──►  Python extraction  ──►  Power BI .pbip project
```

A single command, no external services:

```bash
python migrate.py "Global SIOP Dashboard.qvf" --output-dir output
```

"Extract the data" here means the **app definition** — measures, dimensions,
the data model, sheets, and visuals. Actual table *rows* are **not** pulled from
the `.qvf` (they're in Qlik's proprietary compressed store, and Power BI doesn't
need them: the generated Power Query M reconnects to the original source —
Snowflake/CSV — using the load-script connection).

---

## 2. The pipeline — stage by stage

| # | Stage | Python module | Status |
|---|-------|---------------|--------|
| 1 | Read `.qvf` binary → recover app objects | `extraction/qvf_reader.py` | ⚠️ **Partial** — measures/dimensions yes, sheets/visuals no |
| 2 | Parse load script → tables/fields/sources/relationships | `extraction/qlik_script_parser.py` | ✅ Working |
| 3 | Map objects → validated typed contract | `extraction/extractor.py` | ✅ Working |
| 4 | Convert Qlik expressions → DAX | `generation/dax_converter.py` | ✅ Working (high fallback rate, see §4) |
| 5 | Generate TMDL semantic model | `generation/semantic_model.py` | ✅ Working |
| 6 | Build PBIR visuals | `generation/visual_builder.py` | ✅ Working (blocked only by stage 1 visuals) |
| 7 | Assemble `.pbip` | `generation/pbip_assembler.py` | ✅ Working (report.json schema fixed) |
| 8 | Post-generation validation | `generation/validator.py` | ✅ Working |

How `.qvf` reading works (no engine): the file is a container of entries; each
object (measure, dimension, sheet, visual) is stored as **gzip-compressed Qlik
Engine JSON** (`"Format":"gzjson"`). We scan for the compressed streams, inflate
them with Python's `zlib`, and parse the JSON. This is proven — it already
recovers the master measures and dimensions from the real file.

---

## 3. Where we are now (real-file results)

Running on `Global SIOP Dashboard.qvf` today:

| Recovered | Count | Notes |
|-----------|-------|-------|
| Tables (data model) | 10 | from the load script |
| Relationships | 4 | inferred from shared fields |
| Measures | 238 | masters + inline visual measures |
| Dimensions | 35 | fields recovered |
| **Sheets** | **21** | ✅ recovered (qRoot fix) |
| **Visuals/charts** | **175** | ✅ recovered (qRoot fix) |
| Report pages | 21 | ✅ generated |
| DAX converted | 34 | rest are set-analysis TODOs (Challenge B) |

The generated `.pbip` **opens in Power BI Desktop** with **21 pages and 175
visuals**, the full model, and 238 measures.

✅ **Working end-to-end:** binary read → model → measures → sheets → visuals →
TMDL → valid 21-page `.pbip`. The remaining items are quality refinements
(slicers, a few chart types, set-analysis DAX), not blockers.

---

## 4. Challenges & how we resolve them (all in Python)

### Challenge A — Sheets & visuals not recovered  *(DIAGNOSED + FIX APPLIED)*

**Root cause (found via `dump_objects.py`):** the sheets and charts ARE in the
file (31 objects with `visualization`, 23 with sheet `cells`, 25 with
`qHyperCubeDef`) — but they are wrapped as `{"qMetaData": …, "qRoot": <tree>}`,
which the reader didn't unwrap, so they landed as "unknown".

**Fix applied** in `qvf_reader.py`: the tree walker now unwraps `qRoot` /
`qProperty` and flattens `qChildren` / `qChildList`, so each sheet and its child
charts surface as objects. The app name is also now read from the layout
object's `qTitle`. **Pending verification on the real file** (re-run `migrate.py`
and confirm non-zero sheets/visuals).

----- original analysis kept for reference -----

**Symptom:** stage 1 recovers 217 objects (144 measures + 35 dimensions + 38
others) but classifies **0 sheets and 0 visuals**.

**Likely causes (one of):**
1. Sheet/visual objects *are* inflated but tagged with a type our classifier
   doesn't yet recognise → just a classification fix.
2. They are nested inside another object (`qChildren`) we don't fully walk →
   flattening fix.
3. Their compressed blobs aren't being inflated (larger/different framing) →
   inflation fix.

**Resolution steps (Python):**
1. **Diagnose** — run `python tools/dump_objects.py "Global SIOP Dashboard.qvf"`.
   It dumps every recovered object's type + keys, and crucially reports how many
   inflated blobs contain `qHyperCubeDef` (a visual), `cells` (a sheet layout),
   or `qChildList` (a container). This tells us *which* of the three causes we
   have.
2. **Fix based on the dump:**
   - If objects with `qHyperCubeDef`/`cells` exist but are mis-typed → extend
     the type map in `qvf_reader.py` (small change).
   - If they're nested → extend the `_explode()` child-flattening (already added
     for `qChildren`/`qChildList`; the dump confirms the actual nesting key).
   - If they never inflate → adjust the stream scan in `qvf_reader.py`
     (e.g. widen signatures, handle raw-DEFLATE) — `tools/diag_qvf.py` already
     tests these strategies.
3. **Verify** — re-run `migrate.py`; sheets/visuals counts should be non-zero
   and pages render in Desktop.

This is the single open item for a complete migration. It is a Python change in
`qvf_reader.py`, driven by the dump output.

### Challenge B — High DAX fallback rate (118 of 143)

**Symptom:** most measures fall back to a `-- TODO` placeholder. Two reasons:
- **Set analysis** (`Sum({<Year={2023}>} …)`) — genuinely needs manual rework;
  there is no automatic 1:1 DAX. These stay as documented TODOs (correct
  behaviour — never a wrong silent conversion).
- **Simple unmapped functions** — `num()`, `and`, `or` account for ~30 of the
  fallbacks and *are* convertible.

**Resolution (Python):** extend `generation/dax_mappings.py` /
`dax_converter.py` to handle `num()` (unwrap to inner expression), and Qlik
`and`/`or` keywords → DAX `&&`/`||`. Each with a unit test. This cuts the
fallback count materially; the remaining set-analysis measures are listed for an
analyst to finish.

### Challenge C — Actual data rows

Not a blocker, by design: we do **not** extract row data from the `.qvf`
(proprietary compressed store). The generated Power Query M reconnects Power BI
to the original Snowflake/CSV source, which is the correct migration approach.
Row data is reloaded by Power BI on refresh.

---

## 5. Definition of done

- [x] `.qvf` read in pure Python (no engine)
- [x] Data model + measures + dimensions → TMDL
- [x] Valid `.pbip` that opens in Power BI Desktop
- [x] **Sheets & visuals recovered** (21 sheets, 175 visuals)
- [x] `and`/`or`/`not` + bracketed `[Field Name]` DAX handled
- [ ] Slicers (filterpane/listbox) — partially fixed; verify
- [ ] Remaining set-analysis / `num` / `GetSelectedCount` measures (manual or future mapping)
- [ ] Desktop render confirmed across all 21 pages

---

## 6. Immediate next action

```bash
python tools/dump_objects.py "Global SIOP Dashboard.qvf"
```

Share the printed summary (and the `*_objects/` dump). It pinpoints exactly why
sheets/visuals are missing and tells us which one-file Python fix in
`qvf_reader.py` closes Challenge A.
