# Changelog

Dated entries, newest first. Add a line here whenever you change `criteria.toml`,
`unit_synonyms.csv`, `conversions.csv` or the program. Every run's log records the SHA-256 of each
rule file, so a run can be matched to an entry here.

## 2026-10-30

Two rule changes in the program (`lab_harmonize.py`) and `settings.toml`. The split and unit rule
files did not change.

**Unit report over the whole run**
- `Decision`: a code is "multi-unit" when its numeric rows carry two or more non-blank units in
  all files of the run together. Before, the runner decided this inside each file. A code that
  changed unit at a year boundary (one unit per file) was never listed.
- Each file now returns all its numeric `(code, unit)` pairs. The run-level step aggregates them
  and applies the multi-unit filter once.
- `units_not_registered.csv` keeps its columns. `n_rows` counts rows of the unit in all files.
- The per-file log line lists the pairs of the file. The run-level line gives the result once.

**Microbiology excluded through `OBS_ID`**
- `Decision`: a row that shares its `OBS_ID` with an `MB` row is microbiology too. Before, culture
  results with an empty `CODE_TEST` (`No growth`, `Culture POSITIVE`) went into split and review.
- New `settings.toml` keys in `[columns]`: `link = "OBS_ID"` and `exclude_linked = true`.
- A pre-pass reads `link` and `code` from all selected files and collects the `link` values of
  `MB` rows. The runner then excludes every row with one of these values.
- Linked rows stay in the harmonized file with `shape = microbiology`, like `MB` rows.
- The log gives excluded rows by prefix and by link per file. `distribution_comparison.csv` has a
  new column `excluded_linked_rows`. `excluded_rows` still counts all excluded rows.

**Documentation**
- `README.md`: rewritten in Simplified Technical English, with tables. The results now come from
  the two runs of 2026-10-30.
- `HOW_IT_WORKS.md` §2 and §4.4: `conversion_status` is null for every row that is not a numeric
  shape, also when the row has a `number` (`0.01 Negative`, `3+`).
- `HOW_IT_WORKS.md` §4.5: the table of the three unit reports is complete again.

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
