# Changelog

Dated entries, newest first. Add a line here whenever you change `criteria.toml`,
`unit_synonyms.csv`, `conversions.csv` or the program. Every run's log records the SHA-256 of each
rule file, so a run can be matched to an entry here.

## 2026-09-04

Program and rule set built and validated on 16 annual files (2011–2026).

**Program (`lab_harmonize.py`)**
- Two steps in one run: split `RESULT_VAL` into 9 columns, then unit synonyms and registered
  conversions into 5 `_std` columns.
- All rules run on the distinct `(result, unit, code, name)` values of a file, not on rows.
- Harmonized parquet keeps every source row in source order, under the source file name and
  compression codec; excluded (`MB`) rows carry `shape = microbiology` and nothing else.
  Row count checked after writing.
- Run folder: `lab_harmonize.log`, `distribution_comparison.csv`, `code_dictionary.csv`,
  `units_not_registered.csv`, `conversions_applied.csv`, `harmonized/`, `decisions/`, `review/`.
- Requirements: polars, pyarrow (codec of the source file), tomli on Python < 3.11.

**Split rules (`criteria.toml`)**
- `null_literal` (`None`, `N/A`, `NA`) → missing for every test, no per-code exceptions.
- `not_reported_sentinel` (`*0000`) → missing.
- Numeric trailing full stops and stray punctuation around a single word stripped before matching.
- `mixed_text_numeric` needs a standalone number; `JAK2`-style identifiers are `short_text`.
- `qualifier_detection`: `detectable` forms, one optional trailing `.`, optional modifier.
- `severity_grade`: `rare`, `adequate(d)`, `decrease(d)`, `increase(d)` added.
- Modifiers `dimly`, `brightly` and the ranges `dimly - moderately`, `moderately - brightly`,
  `dimly - brightly`.
- Misspellings seen in 2012–2025 listed as explicit alternatives (22 of `few`, 17 of `trace`, …).
  No fuzzy matching; near-miss report instead.

**Unit rules (`unit_synonyms.csv`, `conversions.csv`)**
- 41 synonym rows (`K/ul` = `x 10\S\3/cumm` → `10^3/uL`, `Copies` → `copies/mL`, `mg%` → `mg/dL`,
  `๐` → blank). Lookup ignores case and whitespace; any unit named in either file is a known
  spelling.
- 94 conversions seeded from an earlier hand-built table (with six corrections: triglyceride
  885.7, phosphate as phosphorus 30.97, lithium prefix 1000, transferrin prefix 1e-6, urine
  creatinine method 1, methanol `mg%` = `mg/dL`) plus albumin, uric acid `200163` and POCT glucose
  `H119` from an older project mapping. Pairs with no fixed factor (viral loads copies vs IU,
  kit-specific antibody units) are not registered and appear in `units_not_registered.csv`.
