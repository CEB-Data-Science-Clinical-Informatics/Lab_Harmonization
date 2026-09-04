# lab_harmonize

Turns raw laboratory results into columns a project can use, in two steps and one run:

1. **Split** — the free-text result (`RESULT_VAL`) becomes standardized columns: a number with its
   operator and unit, a range, or a qualitative label with its modifier. What cannot be resolved is
   marked `review`, never guessed.
2. **Harmonize units** — every unit is spelled one way, and for the `(code, unit)` pairs somebody
   has registered in `conversions.csv`, the numbers are converted into standardized `_std` columns.
   Everything else is copied unchanged and labelled `not_registered`.

Source files are never modified. Rules are data files a person edits; `HOW_IT_WORKS.md` explains
every rule and how to extend it.

## Quick start

1. Python 3.9 or newer, then `pip install -r requirements.txt` (polars, pyarrow; tomli below 3.11).
2. Open `settings.toml`. The block marked **EDIT THESE ON YOUR DEVICE** holds the two paths that
   are the author's: `lab_dir` (where the parquet files are) and `output_dir` (where runs go).
   Change them to yours. Everything else can stay.
3. Run `lab_harmonize.py` (double-click, "Run file" in your editor, or `python lab_harmonize.py`).
   No command-line arguments. As shipped it processes **one small file and writes no harmonized
   parquet**, so the first run takes seconds. Open `output/run_<timestamp>/lab_harmonize.log` and
   the `review/` folder to see what it did.
4. For a full run set `files = []` and `write_harmonized_files = true` in `settings.toml`. Expect a
   few minutes and an output roughly the size of the input.

## What you get

One folder per run, `run_<timestamp>/` inside `output_dir`:

```
run_<timestamp>/
├── lab_harmonize.log             what ran, with counts per file
├── distribution_comparison.csv   split / missing / review share per file
├── code_dictionary.csv           every code with its names and units — where to look up a test
├── units_not_registered.csv      unit pairs still to decide before a project uses the test
├── conversions_applied.csv       which conversions ran, where
├── harmonized/                   one parquet per input file, same name, rows and compression as the source, + 14 new columns
├── decisions/                    every distinct value and what was decided for it (audit trail)
└── review/                       most frequent values still in review; near-miss (typo) report
```

The 14 new columns: `shape`, `split_action`, `operator`, `number`, `range_low`, `range_high`,
`unit`, `value`, `qualifier` (step 1) and `number_std`, `range_low_std`, `range_high_std`,
`unit_std`, `conversion_status` (step 2). `number` + `unit` are what the lab reported;
`number_std` + `unit_std` are what to use.

## Files you edit

| File                | Controls                                                                                    |
| ------------------- | ------------------------------------------------------------------------------------------- |
| `settings.toml`     | where to read, what to write, column names — the `[paths]` block is the part to edit         |
| `criteria.toml`     | step 1: the shape rules (regular expressions and label maps) — add words, misspellings here |
| `unit_synonyms.csv` | step 2: spellings that mean the same unit and the one to keep (`K/ul` = `x 10\S\3/cumm` → `10^3/uL`) |
| `conversions.csv`   | step 2: registered unit conversions per `(code, unit)`; append a row when your project needs a test converted |
| `CHANGELOG.md`      | add a dated line whenever you change one of the files above                                  |

## Typical tasks

- **A frequent value is still `review`** → find it in `review/<stem>_review_top.csv`, add the word to
  `criteria.toml` (HOW_IT_WORKS §3.7), run again, compare `distribution_comparison.csv`.
- **A test has two units and you need one** → find the code in `code_dictionary.csv`, check
  `units_not_registered.csv`, append a row to `conversions.csv` with the method from
  HOW_IT_WORKS §4.3, run again, confirm the pair in `conversions_applied.csv`.
- **A test has one unit** → nothing to do; its values are copied into the `_std` columns as is.
- **A new unit spelling appears** → one row in `unit_synonyms.csv`. Case and spacing differences
  need no row.

## Results so far

Applied to 16 files (2011–2026, up to 26 million rows each): 3.0–4.9 % of rows per file still need
manual review; the rest is mechanically split (86–96 %) or confirmed missing. 94 unit conversions
are registered; the 85 `(code, unit)` pairs still open are all pairs without a fixed factor (viral
loads in copies vs IU, kit-specific antibody units). A file takes seconds, because the rules run on
distinct values, not on rows.

## Notes

- Windows, macOS, Linux: paths in `settings.toml` may use forward slashes and `~`.
- If a source file already has a column with one of the 14 new names, list the columns you need in
  `keep_columns`.
- The log records the SHA-256 of every rule file, so any set of numbers can be traced to the rule
  set that produced it.
