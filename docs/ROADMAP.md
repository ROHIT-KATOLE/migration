# Qlik → Power BI Migration — Roadmap & Status

**Goal:** Convert Harman's Qlik Sense `.qvf` dashboards into Power BI `.pbip`
projects with a Python utility, **without taking anything from Harman's Qlik
environment** wherever possible.

---

## 0. Headline (read this first)

> **We CAN already convert `.qvf` → JSON and extract the data, today, in pure
> Python — no Qlik server, no license.**

Proof, on the real **Global SIOP Dashboard.qvf** (88 MB), one command:

```
python migrate.py "Global SIOP Dashboard.qvf"
```

produces:
- `output/.../extracted/` → **JSON files of every measure, dimension, sheet,
  visual, table, and relationship** (the qvf→JSON artifact),
- a Power BI `.pbip` that **opens in Power BI Desktop with 21 pages**.

Numbers extracted from the real file: **10 tables, 21 sheets, 175 visuals,
238 measures, 4 relationships.**

So the "we can't get the data out of the qvf" concern is **not correct** — that
part works. What is genuinely hard (for *every* Qlik→Power BI tool) is
translating Qlik's **set-analysis formulas** into DAX. That is the real
remaining work, and it is the same regardless of which extraction method we use.

---

## 1. The two layers of this project (don't conflate them)

| Layer | What it means | Status |
|------|----------------|--------|
| **Extraction** | Get the app's objects out of the `.qvf` as JSON | ✅ **Solved** (pure Python). Engine route can make it 100% bulletproof. |
| **Conversion** | Turn Qlik measures/expressions into DAX + Power BI visuals | ⚙️ Structure + simple measures done; **set-analysis measures need a manual/assisted DAX pass** (inherent, not a tooling gap). |

Most of the "are we stuck?" anxiety is about Layer 1, which is done. The honest
hard problem is Layer 2.

---

## 2. Two paths for extraction

### Path A — Pure-Python binary reader (current, zero dependencies)
We read the `.qvf` binary directly (objects are gzip-compressed Qlik JSON) and
decode them in Python. **No Qlik, no Docker, no license, nothing from Harman.**
Already working on the real file. Small risk: a rare object type may need a code
tweak.

### Path B — Local Qlik **engine** + export to JSON (most reliable)
Run Qlik's **own engine locally** to export every object via its API, then feed
that JSON to the same Python generator. Highest fidelity because Qlik itself
reads the app.

**Key question you asked — do we need Harman's Qlik server?**
**No.** The Qlik engine can run **locally and separately**, two ways:

1. **Qlik Sense Desktop (recommended)** — a *free* Windows app that bundles the
   Qlik engine and runs it on `localhost:4848`. We drop the `.qvf` in its Apps
   folder, open it, and a Python script reads it via the engine API. **No
   connection to Harman's Qlik Cloud/Server.**
2. **Docker `qlikcore/engine`** — the engine in a container on `localhost:9076`.
   Also local, but the image is from *Qlik Core* (deprecated by Qlik) and may
   need a licence — this is the only spot that might require Harman/Qlik help.

Either way the engine runs **on our machine against the `.qvf` files we already
have** — we do not log into Harman's environment.

---

## 3. Path B — exactly how it works (engine route)

```
.qvf  ─►  [ local Qlik engine ]  ─►  export objects to JSON  ─►  migrate.py  ─►  .pbip
          (Qlik Sense Desktop          (Engine API / corectl)     (our Python
           or Docker, localhost)                                   generator)
```

### Option B1 — Qlik Sense Desktop + Python Engine API (100% Python, no Harman)
1. Install **Qlik Sense Desktop** (free) and sign in once (free Qlik account).
2. Put the `.qvf` in `…\Documents\Qlik\Sense\Apps\`.
3. Open the app in Desktop → its engine is now live at `wss://localhost:4848`.
4. A **pure-Python** script (`websocket` + Qlik's JSON-RPC Engine API) calls
   `OpenDoc → GetScript`, `GetMeasure`, `GetDimension`, `GetObject`,
   `GetTablesAndKeys` → writes JSON for every object.
5. `python migrate.py <json-folder>` → builds the `.pbip`.
   *(I can build this Python engine-extractor next — it reuses everything we
   already have.)*

### Option B2 — Docker engine + corectl (already scaffolded in the repo)
```powershell
copy "Global SIOP Dashboard.qvf" docker\apps\
cd docker; $env:ACCEPT_EULA="yes"; docker compose up -d; cd ..   # local engine :9076
python tools\qlik_unbuild.py "Global SIOP Dashboard.qvf" --out unbuild  # corectl -> JSON
python migrate.py "unbuild\Global SIOP Dashboard" --output-dir output    # -> .pbip
```

Both options produce the **same JSON shape** our generator already consumes, so
the Power BI side is unchanged.

---

## 4. How confident am I? (calibrated, honest)

| Stage | Confidence | Notes |
|-------|-----------|-------|
| `.qvf` → JSON extraction (Path A) | **High (~90%)** | Proven on the real file; rare object types may need a tweak. |
| `.qvf` → JSON extraction (Path B / engine) | **Very high (~99%)** | Qlik's own engine; nothing to reverse-engineer. |
| Build Power BI model + pages + visual layout | **High** | `.pbip` opens with 21 pages today. |
| Auto-convert **simple** measures (Sum/Count/Avg/dates/strings) to DAX | **High** | Already working + unit-tested. |
| Auto-convert **set-analysis** measures to DAX (`{<…>}`, `$()`, `Aggr`, `GetSelectedCount`) | **Low–Medium** | **No tool fully automates this**, including commercial ones. We emit safe TODO placeholders; an analyst finishes them. ~80% of this app's measures are this type. |

**So:** I'm highly confident we deliver a **structurally complete Power BI report
(pages, visuals, model, simple measures)** automatically. I'm honestly *not*
claiming the complex set-analysis DAX converts automatically — that needs a
human pass, and that's true of any Qlik→Power BI migration.

---

## 5. Is it possible with Python only?

**Yes.** Path A is 100% Python today. Path B option B1 is also 100% Python (the
engine API is just websocket calls). Only B2 uses `corectl` (a helper binary)
which we *call from* Python. No language switch needed.

---

## 6. What we still need from Harman (and what we don't)

**Needed for a *working* dashboard (data, not structure):**
- The **full set of in-scope `.qvf` files** (we have a few test apps).
- **Data source connection details** — Snowflake account/warehouse/db/schema +
  read credentials, and any Excel/file sources. *Unavoidable:* Power BI reloads
  data from the source; we deliberately do **not** copy data out of the `.qvf`.
- For the complex measures: **a short Qlik-side sign-off** on business logic so
  our DAX matches intent (a few hours of a Qlik developer's time).

**Not needed (this is the good news):**
- ❌ Access to Harman's Qlik Cloud / Qlik server (we run the engine locally).
- ❌ Any Qlik license from them (Qlik Sense Desktop is free; pure-Python path
  needs nothing).

---

## 7. Roadmap / phases

| Phase | Work | Output | Est. |
|-------|------|--------|------|
| **1 — Extraction (DONE)** | Pure-Python `.qvf` → JSON | JSON of all objects; tool runs end-to-end | ✅ |
| **2 — Power BI generation (DONE)** | JSON → TMDL model + PBIR pages/visuals | `.pbip` opens in Desktop, 21 pages | ✅ (final Desktop polish in progress) |
| **3 — Harden extraction (optional)** | Stand up local Qlik engine (Desktop/Docker) + Python engine-extractor | 100% reliable extraction, all apps | ~2–3 days |
| **4 — DAX conversion** | Expand auto-conversion (operators, `num`, common patterns); list set-analysis measures for manual rework | Higher auto-convert %, prioritized TODO list | ongoing |
| **5 — Data connectivity** | Wire Power BI to Snowflake/source using Harman connection details | Dashboards show live data | needs Harman creds |
| **6 — Validation** | Compare Qlik vs Power BI numbers on key measures | Sign-off | with Harman |

---

## 8. Recommendation for today's meeting

1. **Show the proof:** run `migrate.py` on the test app → the `extracted/*.json`
   files + the 21-page `.pbip`. This rebuts "we can't extract the data."
2. **Frame it correctly:** extraction is solved; the remaining effort is
   Qlik-formula → DAX, which is a known hard problem for everyone.
3. **Decisions to ask for:**
   - Approve using **Qlik Sense Desktop locally** (free) for the reliable engine
     route — no Harman server access required.
   - Get **Snowflake/source connection details** from Harman (required for data).
   - Agree a **priority list of measures** for the manual DAX pass.
4. **Commitment we can make:** automatic, repeatable migration of the dashboard
   **structure, model, and simple measures**; complex measures delivered as
   documented TODOs for a focused manual pass.
