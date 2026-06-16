# Qlik Sense `.qvf` Format Reference

Documents the binary structure `extraction/qvf_reader.py` parses, established by
inspecting real production apps (string/byte analysis via `tools/probe_qvf.py`
and `tools/carve_qvf.py`). No public spec exists; this is what we observed and
rely on.

## It is not SQLite

The original task assumed "a `.qvf` is a SQLite3 database." That is **false** for
production Qlik Sense apps — they error with `file is not a database`. A `.qvf`
is Qlik's proprietary binary container.

## Container layout

A `.qvf` is a sequence of **entries**. Each entry is:

1. An object **id/key** (e.g. `MBCxFRQ`, `siop-app`).
2. A plaintext **JSON header**:
   ```json
   {"ContentHash":"…","Format":"gzjson","ParentId":"MBCxFRQ",
    "Type":"GenericMeasureProperties","SharedStatus":"Published",
    "SecurityMetaAsBase64":"<base64-encoded JSON>","IsTemporary":false}
   ```
   - `Type` — the entry kind (`NxAppProperties`, `GenericMeasureProperties`,
     `GenericDimensionProperties`, `GenericObjectProperties`, …).
   - `SecurityMetaAsBase64` — base64 of a JSON metadata blob carrying
     `title`, `description`, `_objecttype` (`measure`/`dimension`/`sheet`/the
     visualization type/`app`), `id`, `owner`.
3. The object **payload**, compressed per `Format`:
   - `"gzjson"` → **gzip/zlib-compressed JSON** following the Qlik Engine API
     object model.
   - `"binary"` → loaded data tables (not needed for migration).
   - `"json"` → plaintext JSON (rare).

The large remainder of the file is the compressed **data** (symbol tables) —
which is why a UI app with loaded data can be tens/hundreds of MB.

## Payload shapes (Qlik Engine API)

After inflation, payloads follow the public Engine schema:

```jsonc
// master measure
{"qInfo":{"qId":"…","qType":"measure"},
 "qMeasure":{"qLabel":"Total Sales","qDef":"=Sum(Amount)",
             "qNumFormat":{"qType":"F","qFmt":"#,##0.00"}},
 "qMetaDef":{"title":"Total Sales"}}

// master dimension
{"qInfo":{"qId":"…","qType":"dimension"},
 "qDim":{"qGrouping":"N","qFieldDefs":["Region"],"qFieldLabels":["Region"]}}

// visualization
{"qInfo":{"qId":"…","qType":"barchart"}, "visualization":"barchart",
 "qHyperCubeDef":{
    "qDimensions":[{"qLibraryId":"<master dim id>", "qDef":{"qFieldDefs":["Region"]}}],
    "qMeasures":[{"qLibraryId":"<master measure id>", "qDef":{"qDef":"=Sum(Amount)","qLabel":"…"}}]}}

// sheet
{"qInfo":{"qId":"…","qType":"sheet"}, "rank":0,
 "cells":[{"name":"<visual id>","col":0,"row":0,"colspan":12,"rowspan":8}]}
```

Visuals reference master items by `qLibraryId`, or define dimensions/measures
inline via `qDef`. The reader/extractor handle both.

The load script is recovered from a payload field (`qScript`) or an inflated
text blob containing `LOAD` / `SQL SELECT` / `CONNECT`.

## How the reader uses this

1. Scan for compressed streams (gzip `1f 8b 08`, zlib `78 9c/da/01`), inflate
   each (`zlib.decompressobj(47)` auto-detects gzip vs zlib), `json.loads`.
2. Regex the plaintext entry headers; base64-decode `SecurityMetaAsBase64`.
3. Correlate payload (`qInfo.qId`) with header metadata (`id`) → `QlikObject`.

The reader is deliberately tolerant: an undecodable blob or unexpected object is
skipped, never fatal, so it degrades to "recovered less" instead of crashing.

## Known limitations

- **Encrypted apps** (Qlik Cloud managed keys) can't be inflated — the tool
  reports this and the app must be re-exported unencrypted.
- **Field-level data model** depends on the load script being recoverable. If it
  isn't, the extractor synthesises a flat table from referenced fields and logs
  a warning to verify tables/relationships manually.
- The **binary data tables** are intentionally not parsed — Power BI reloads
  from source via the generated M queries.
