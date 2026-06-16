# DAX Mapping Reference

Documents the provenance and verification of every Qlik→DAX mapping in
[`generation/dax_mappings.py`](../generation/dax_mappings.py).

## Source

Function names were cross-checked against two sources:

1. **`cyphou/Qlik-To-PowerBI`** — `qlik_export/dax_converter.py` (the
   `_SIMPLE_FUNCTION_MAP` regex table). That repo carried ~175 unverified
   regex substitutions, many producing invalid DAX (e.g. `Round(` → `ROUND(`
   with no second argument, `Skew(` → an `UNSUPPORTED` comment). We harvested
   only the function *names*, not its regex machinery.
2. The official **DAX function reference**, to confirm each emitted function is
   real and that the single-/multi-argument form we generate is valid.

## Verification principle

> A harvested mapping you haven't tested is worth less than no mapping at all.

Every entry in `DAX_MAPPINGS` carries an embedded `test = (qlik, expected_dax)`
case. [`tests/test_dax_converter.py`](../tests/test_dax_converter.py)
parametrizes over the whole table and asserts the converter reproduces each
expected DAX string exactly. **A mapping with no passing test does not ship.**

## What was deliberately dropped

The original repo's table contained entries we rejected because no clean,
valid-DAX test could be written for them:

| Qlik function | Why dropped |
|---------------|-------------|
| `Skew`, `Hash128/160/256`, `Evaluate`, `BitCount` | No DAX equivalent; original emitted a comment + `0`. |
| `Correl`, `NetWorkDays`, `KeepChar`, `Capitalize` | Original emitted long approximations that are not faithful. |
| `RangeSum`/`RangeAvg`/… | Original emitted an unbalanced `SUM( /* RangeSum */` fragment. |
| `Match`, `MixMatch`, `ApplyMap` | Require structural rewriting, not a name swap. |

These now fall through to the **fallback** path (a TODO placeholder), which is
the honest outcome — see below.

## Mapping table (all unit-tested)

| Qlik | DAX | Category |
|------|-----|----------|
| Sum | SUM | aggregation |
| Avg | AVERAGE | aggregation |
| Count | COUNTA | aggregation |
| CountDistinct | DISTINCTCOUNT | aggregation |
| Min / Max | MIN / MAX | aggregation |
| Median | MEDIAN | aggregation |
| Stdev | STDEV.S | aggregation |
| Sqrt / Abs / Exp / Sign / Int | SQRT / ABS / EXP / SIGN / INT | math |
| Round / Div / Pow | ROUND / DIVIDE / POWER | math (multi-arg) |
| Year / Month / Day / Hour / Minute / Second | YEAR / MONTH / DAY / HOUR / MINUTE / SECOND | date |
| Week / WeekDay | WEEKNUM / WEEKDAY | date |
| Upper / Lower / Len / Trim | UPPER / LOWER / LEN / TRIM | string |
| If | IF | logic |
| IsNull / IsNum / IsText | ISBLANK / ISNUMBER / ISTEXT | logic |

Count maps to `COUNTA` (not the original's `COUNTROWS`) so the counted column is
preserved and non-blank semantics match Qlik's `Count(field)`.

## Fallback behaviour (not a mapping, but the safety net)

The converter never guesses at expressions it cannot prove. It emits a fallback
placeholder for:

- **Set analysis** — `Sum({<Year={2023}>} Amount)`
- **Dollar expansion** — `$(vVariable)`
- **`Aggr(...)`** and the **`TOTAL`** qualifier
- **Unknown functions** not in the mapping table
- **Empty expressions**

Each becomes:

```dax
-- TODO: Manual conversion required
-- Original Qlik: <original expression>
0  // placeholder
```

and is counted in `fallback_count`, surfaced in the migration report.
