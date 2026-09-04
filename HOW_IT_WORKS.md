# How `lab_harmonize.py` works

Lab harmonization in two steps, one program, one run:

1. **Split** — one free-text result string (`RESULT_VAL`) becomes standardized columns: a number
   with its operator and unit, a range, or a qualitative label with its modifier. Values that
   cannot be resolved are marked for review, never guessed.
2. **Harmonize units** — every unit is spelled one way, and for the `(code, unit)` pairs a person
   has registered, the numbers are converted into standardized `_std` columns. Everything else is
   copied unchanged and labelled so.

Every rule is data, not code: step 1 lives in `criteria.toml`, step 2 in `unit_synonyms.csv` and
`conversions.csv`. This document names the file and key at every rule so you know what to edit
when a new word, spelling or unit shows up.

| You want to… | Edit | Section |
|---|---|---|
| point the program at your files | `settings.toml` | §1 |
| add a qualitative word, grade, modifier, null-like string, misspelling | `criteria.toml` | §3.7 |
| unify a new unit spelling (`K/ul` = `10^3/uL`) | `unit_synonyms.csv` | §4.1 |
| convert a test from one unit to another | `conversions.csv` | §4.2, §4.3 |
| find the code for a test, or which codes use a unit | read `code_dictionary.csv` | §4.5 |
| see what still needs a human | read `review/*_review_top.csv`, `units_not_registered.csv` | §5 |

---

## 1. One run, in one picture

```
settings.toml ─┐
criteria.toml ─┤        ┌──────────────────────────┐
unit_synonyms ─┼──────▶ │ for each input file:      │
conversions   ─┘        │  rows ▶ distinct values   │  (result, unit, code, name) + row count
                        │  step 1: shape, split     │  §3
                        │  step 2: unit, convert    │  §4
                        │  join decisions ▶ rows    │  harmonized/<input file name>
                        └──────────────────────────┘
                                   │
                                   ▼
                        run_<timestamp>/  log, reports, decisions, harmonized files   §2
```

The key idea: **a value is judged once, not once per row.** Each input file is first collapsed
to its distinct `(result, unit, code, name)` strings with a row count — a 25-million-row file has a
few hundred thousand distinct strings. All rules run on that small table; the row-level output is
produced afterwards by joining the decisions back to the rows. A file takes seconds.

`settings.toml` has four groups. `[paths]` is the part to edit on your device: `lab_dir` and
`output_dir`. `[input]` picks the files: `files` (a list; empty = every file matching `file_glob`).
`[output]` says whether to write the harmonized parquet files and how. `[columns]` names the source
columns (`code`, `result`, `unit`, `name`) and the `exclude_code_prefixes` (default `MB`,
microbiology). As shipped, `files` names one small file and `write_harmonized_files` is off, so a
first run finishes in seconds and writes only reports. Excluded rows are not split or converted; they are counted in the
log and kept in the harmonized file, in place, with `shape = excluded_shape_label`
(default `microbiology`) and every other new column null. Source files are never modified.

---

## 2. What a run produces

Every run creates one folder `run_<YYYYMMDD_HHMMSS>/` inside `output_dir` (from `settings.toml`;
empty means the folder of the script; a missing folder is created). Nothing from an earlier run
is overwritten.

```
run_20260904_211519/
├── lab_harmonize.log             what ran: file digests, compiled patterns, per-file counts by
│                                 action and shape, parse-failure count, timings, cross-file table
├── distribution_comparison.csv   one row per input file: split / missing / review shares,
│                                 codes_in_review, parse_failures, rows_written and
│                                 row_count_match (harmonized rows = source rows), share of every shape
├── code_dictionary.csv           every code with all its names and unit spellings (§4.5)
├── units_not_registered.csv      (code, unit) pairs still to decide, with test names and the
│                                 files they occur in (§4.5)
├── conversions_applied.csv       which conversions ran, on how many rows, in which files (§4.5)
├── harmonized/  <input file name>           every source row + 9 split columns + 5 unit columns, under
│                                            the source file's own name and compression codec, so it
│                                            can replace it downstream (if write_harmonized_files)
├── decisions/   <stem>_decisions.csv        every distinct (result, unit, code, name) with its
│                                            row count and all 14 decision columns — the audit trail
└── review/      <stem>_review_top.csv       the review_top_n most frequent values still in review
                 <stem>_near_vocab.csv       single words close to a known word — typo hunting (§3.6)
```

`<stem>` is the input file name up to the first dot. The harmonized file has exactly the rows of the
source, in the same order; the `decisions/` and `review/` files and the shares in
`distribution_comparison.csv` cover the processed rows only.

### The 14 columns added to every row

| Step | Column | Content |
|---|---|---|
| 1 | `shape` | what the string looks like (17 shapes, §3.2), or `microbiology` for excluded rows |
| 1 | `split_action` | `split` · `missing` · `review` (§3.3) |
| 1 | `operator` | `<`, `<=`, `>`, `>=`, `=`, `+` when present |
| 1 | `number` | the numeric value (float) |
| 1 | `range_low`, `range_high` | the two bounds of a range |
| 1 | `unit` | the unit as reported, spelled the agreed way (§4.1) |
| 1 | `value` | qualitative label: `Positive`, `Negative`, `Non-Reactive`, `Reactive`, `Equivocal` |
| 1 | `qualifier` | modifier or grade: `Weakly`, `Dimly-Moderately`, `Trace`, `Few`, `Adequate`, `Over` … |
| 2 | `number_std`, `range_low_std`, `range_high_std` | converted when a conversion is registered, otherwise a copy |
| 2 | `unit_std` | the target unit when converted, otherwise `unit` |
| 2 | `conversion_status` | `convert` · `not_registered` · null when there is no number |

`number` + `unit` are what the lab reported. `number_std` + `unit_std` are what a project should
use. Nothing is overwritten.

### Worked example — twelve source rows and what comes out

Blank cells are null. `200106` is LDL cholesterol with a registered `mmol/L → mg/dL` conversion;
`250657` is a CBC count whose `K/ul` is a synonym of `10^3/uL`; `150511` is a urine microscopy grade.

| `CODE_TEST` | `RESULT_VAL` | `UNIT` | `shape` | `split_action` | `operator` | `number` | `range_low` | `range_high` | `unit` | `value` | `qualifier` | `number_std` | `unit_std` | `conversion_status` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 200106 | `3.5` | mmol/L | plain_numeric | split | | 3.5 | | | mmol/L | | | 135.3275 | mg/dL | convert |
| 200106 | `135` | mg/dL | plain_numeric | split | | 135 | | | mg/dL | | | 135 | mg/dL | not_registered |
| 200106 | `0.5-1.5` | mmol/L | numeric_range | split | | | 0.5 | 1.5 | mmol/L | | | | mg/dL | convert |
| 200058 | `< 5` | umol/L | operator_numeric | split | < | 5 | | | umol/L | | | 0.05656 | mg/dL | convert |
| 250657 | `7.2` | K/ul | plain_numeric | split | | 7.2 | | | 10^3/uL | | | 7.2 | 10^3/uL | not_registered |
| 100063 | `Weakly Positive` | | qualifier_numeric | split | | | | | | Positive | Weakly | | | |
| 400091 | `Not Detected.` | | qualifier_detection | split | | | | | | Negative | | | | |
| 150511 | `Trace` | /HPF | severity_grade | split | | | | | /HPF | | Trace | | /HPF | |
| 250537 | `Adequate` | | severity_grade | split | | | | | | | Adequate | | | |
| 150501 | `Centrifuged 10 ml. urine` | | mixed_text_numeric | review | | | | | | | | | | |
| L | `None` | | null_literal | missing | | | | | | | | | | |
| 250510 | `*0000` | fL | not_reported_sentinel | missing | | | | | | | | | | |

For `0.5-1.5` the range bounds convert too: `range_low_std` 19.33, `range_high_std` 58.00. A
microbiology row (code starting `MB`) would show `shape` = `microbiology` and nothing else.

---

## 3. Step 1 — split into standardized columns

### 3.1 Cleaning the raw string (`[general]`)

Before any rule is tried, `RESULT_VAL` becomes `_result`:

1. Cast to text and trim surrounding whitespace.
2. If the value contains **no letters** and ends in a digit, `+` or `%` followed by one or more
   full stops, the full stops are removed — `3.8.` → `3.8`, `> 40000.` → `> 40000`, `1+.` → `1+`.
   Key: `numeric_trailing_dot`.
3. If the value is a single word wrapped in stray punctuation (`!few`, `few.`, `TRACE'`,
   `Adequate,`, `.FEW`), the punctuation is removed and the word kept. Keys:
   `word_leading_punct`, `word_trailing_punct`. Anything with a digit or a second word is left
   alone.

`UNIT` becomes `_unit`: text, trimmed, and an empty string becomes null.

### 3.2 Shape — what does the string look like?

The shape is decided by a **first-match-wins chain**. The order below *is* the specification:
a value gets the first shape whose test passes and is never tested against later shapes.

| #  | Shape                       | Test (criteria key)                                                                                                             | Examples                                                                                                                         |
| -- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| 1  | `missing`                 | null or empty string                                                                                                            | `""`                                                                                                                           |
| 2  | `null_literal`            | `[missing] null_literal` — export artifacts written as text                                                                  | `None`, `N/A`, `NA`                                                                                                        |
| 3  | `not_reported_sentinel`   | `[missing] not_reported_sentinel` — analyzer "not computed" marker                                                           | `*0000`, `*000`                                                                                                              |
| 4  | `narrative_text`          | contains `[general] break_pattern`, or longer than `narrative_length` characters                                             | multi-line comments                                                                                                              |
| 5  | `plain_numeric`           | whole string is a number (`[numeric] core` or `leading_dot`), optional trailing `%`                                       | `5`, `2.64`, `1,234`, `18%`, `.5`                                                                                      |
| 6  | `operator_numeric`        | one `[operators] tokens` entry, then a number                                                                                  | `<5`, `>= 100`, `</=10`                                                                                                    |
| 7  | `numeric_range`           | number, `range_separator`, number                                                                                              | `0-1`, `2.5 – 3.5`                                                                                                          |
| 8  | `ordinal_plus`            | digits followed by `+`                                                                                                         | `1+`, `3+`                                                                                                                   |
| 9  | `verbal_operator_numeric` | a `[verbal_operators]` word, space, number                                                                                     | `Over 20`, `less than 5`                                                                                                     |
| 10 | `qualifier_numeric`       | optional number, optional modifier (or modifier range), a `[qualifier.values]` word, optional gloss `(...)`, optional number | `Positive`, `2.64 Positive`, `Dimly Positive`, `dimly - moderately positive`, `Negative(ตรวจไม่พบเชื้อ)` |
| 11 | `qualifier_ordinal`       | qualifier word, space, `N+`                                                                                                    | `Positive 3+`                                                                                                                  |
| 12 | `qualifier_comparison`    | qualifier word and `op number`, either order                                                                                   | `Negative < 2`, `< 10.0 Negative`                                                                                            |
| 13 | `mixed_text_numeric`      | contains a **standalone** number (`[general] standalone_number`: digits not glued to a letter) and matched nothing above | `Centrifuged 10 ml. urine`, `Class0 (<0.35 kU/l)`, `1:80`                                                                  |
| 14 | `qualifier_detection`     | optional modifier, a `[detection.values]` phrase, optional gloss, optional one trailing `.`                                  | `Detected`, `Not Found`, `Undetectable`, `Not Detected.`, `Strongly detected`                                          |
| 15 | `severity_grade`          | a bare `[severity]` word, inflections included                                                                                 | `Rare`, `Trace`, `Few`, `Many`, `Adequate`, `Decrease`, `Decreased`, `Increase`                                  |
| 16 | `short_text`              | has at least one letter, no standalone number, matched nothing above                                                            | `Adequate`, `Clear`, `Negative for JAK2 V617F Mutation`, `A2A`                                                           |
| 17 | `punctuation_or_junk`     | no letter, no digit                                                                                                             | `-`, `**`, `----`                                                                                                          |

Why step 13 sits before 14–15: any value with a free-standing number is a compound result and
must be reviewed by a person, even if it also contains a detection or grading word.

Why `JAK2` is `short_text` and not `mixed_text_numeric`: the digit is part of an identifier, not a
measurement. `standalone_number` requires a non-letter (or the string edge) on both sides of the
digits.

### 3.3 Split action — can it be resolved mechanically?

| `split_action` | Shapes                                                                            | Meaning                                 |
| ---------------- | --------------------------------------------------------------------------------- | --------------------------------------- |
| `missing`      | `missing`, `null_literal`, `not_reported_sentinel`, `punctuation_or_junk` | no usable value; nothing to extract     |
| `split`        | 5–12, 14, 15                                                                     | typed columns are filled (next section) |
| `review`       | `narrative_text`, `mixed_text_numeric`, `short_text`                        | a person must look at it                |

One extra guard: a value ending in `%` whose recorded unit is something **other** than `%` (or
null) is demoted from `split` to `review`, because the value and the unit disagree.

### 3.4 Typed columns — what is extracted for a `split` value

| Column                        | Filled for                                                           | How                                                                                                                                       |
| ----------------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `operator`                  | `operator_numeric`                                                 | the token, normalised via `[operators.normalize]` (`</=` → `<=`)                                                                    |
|                               | `verbal_operator_numeric`                                          | from the word: `over/above/greater than` → `>`, `under/below/less than` → `<`                                                    |
|                               | `ordinal_plus`, `qualifier_ordinal`                              | literally `+`                                                                                                                            |
|                               | `qualifier_comparison`                                             | the comparison token inside the string                                                                                                    |
| `number`                    | `plain_numeric`, `operator_numeric`, `verbal_operator_numeric` | the numeric token, thousands separators and `%` removed                                                                                  |
|                               | `ordinal_plus`, `qualifier_ordinal`                              | the digits before `+` (`3+` → 3)                                                                                                      |
|                               | `qualifier_numeric`, `qualifier_comparison`                      | the first number in the string, if any                                                                                                    |
| `range_low`, `range_high` | `numeric_range`                                                    | the two bounds                                                                                                                            |
| `unit`                      | numeric shapes, `severity_grade`                                    | the recorded unit (`Few` with `/HPF` keeps `/HPF`), or `%` when the value itself ends in `%`                                    |
|                               | qualitative shapes, `ordinal_plus`                                  | always null (a word or a `2+` grade has no unit)                                                                                         |
| `value`                     | `qualifier_*` shapes                                               | the label from `[qualifier.values]` — first pattern found wins, so `non-reactive` is listed before `reactive`                       |
|                               | `qualifier_detection`                                              | the label from `[detection.values]` — negated phrases are listed before bare ones                                                       |
| `qualifier`                 | `qualifier_numeric`, `qualifier_detection`                       | the modifier label from `[qualifier.modifier_ranges]` then `[qualifier.modifiers]` (`Dimly`, `Dimly-Moderately`, `Strongly`, …) |
|                               | `severity_grade`                                                   | the grade label (`Trace`, `Rare`, `Adequate`, `Decreased`, …) — a grade never becomes a `value`                               |
|                               | `verbal_operator_numeric`                                          | the word as written (`Over`, `Less Than`) so the original phrasing is kept                                                            |

Columns not listed for a shape are null. For `missing` and `review` rows all seven typed columns
are null.

### 3.5 What is deliberately **not** done

- **No value is mapped from `RESULT_VAL` alone when the meaning depends on the test.** `Yes`/`No`
  are workflow flags in this data (`On time: Yes`), `Normal` is a real result for some tests only,
  `non` is a reported hemolysis index — all stay `review`.
- **Admin phrases stay `review`** (`แพทย์ไม่แจ้ง`, `ไม่สามารถคำนวณค่าได้`, `See Comment`, `DONE`).
  That corpus grows every year; each phrase needs a human decision.
- **Compound sentences are not parsed.** `No Paraprotein Detected`, `Target not detected`,
  `Positive fine speckled titer 1:80`, `Negative for JAK2 V617F Mutation` stay `review`.
  Detection words are honoured only as a single word or as modifier + word.
- **Numbers are not interpreted.** `3+` becomes operator `+`, number 3; nothing decides whether
  that is "moderate".
- **No test-specific rules.** Some tests report `None` as a real result — the serum indices
  (Lipemic / Hemolysis / Icteric, codes `L`/`H`/`I`) use the scale None < Trace < 1+ < 2+ < 3+ < 4+,
  and `None` is 13.7 million rows in 2015–2023. From 2024 the source stores a blank for the same
  result. Decision: `None` is `missing` everywhere, matching the blank, and the missing share of
  ~8 % per year from 2016 on is accepted. Per-code exceptions were considered and rejected as an
  open-ended maintenance burden.

### 3.6 Spelling mistakes and why there is no fuzzy matching

Every rule is an exact regular expression. `fewww`, `teace` or `Nagative` do not match anything
and end in `short_text` → `review`. Measured on 2012–2025 that is about 400 rows in 335 million.

Fuzzy matching is not used because the same edit distance that finds those typos also pairs
`DONE` with `none` (one edit, 153,000 admin rows that would become `missing`), `Male` with `many`,
`Abnormal` with `normal`, `mass` with `many`. False positives outnumber true typos roughly 400 to
1 in rows. A regex is safer: it only ever does what a person wrote.

Instead each run writes `review/<stem>_near_vocab.csv`: every single-word `review` value within
`[near_miss] max_distance` edits of a word in `[near_miss] vocabulary`, with the nearest word, the
distance, rows and codes. Reading that file after a run is how a typo worth a rule gets found. If
one is, add it explicitly to `criteria.toml` as its own alternative (for example
`'fe+w+' = 'Few'` under `[severity]`), so the decision is visible and reviewable.
`[near_miss]` never changes a decision. It only writes the report.

The 2012–2025 sweep was read once and its clear cases were added as explicit alternatives
(2026-09-04): 22 spellings of `few` (`ferw`, `feqw`, `fewq`, `feww`, …), 17 of `trace` (`teace`,
`tarce`, `trece`, …), `moderat`/`moderte`/`modurate`, `adequete`, `decreasd`/`decrased`/`decraese`/
`decrase`, `positve`/`postive`, `negativ`/`nagative`/`neagtive`/`negartiv`/`megative`, `equivoca`,
`borderli`, `undetect`, `markedly`. About 450 rows in total. Left out on purpose: `Male`, `mass`,
`MAHA` (real words near `many`), `Inadequate` (opposite meaning), anything with a digit glued on
(`few1`, `1+few`, `trace3`), and words carrying a stray Thai vowel mark.

### 3.7 Editing `criteria.toml`

- New qualitative word (say `indeterminate`): add `'indeterminate' = 'Indeterminate'` under
  `[qualifier.values]`. Put it **before** any word it could be confused with.
- New intensity word (say `faintly`): add under `[qualifier.modifiers]`; if it appears in ranges
  (`faintly - dimly`), add the range under `[qualifier.modifier_ranges]`.
- New grading or quantity word (say `occasional`): add under `[severity]`. Write the regex so it
  accepts the inflections you have seen (`decreased?`).
- New misspelling: add it as another alternative on the word's own line (`'few|feww|…'`).
- New null-like string (say `<Blank>`): extend the `null_literal` regex — but confirm first that
  the string is never a real result for any test.
- After any edit, run again and compare `distribution_comparison.csv` with the previous run: the
  `split` share should go up and `review` down; `missing` should not move unless you changed
  `[missing]`. The log prints every compiled pattern and the SHA-256 of `criteria.toml`, so a later
  reader can tell which rule set produced which numbers.

Patterns are Rust regular expressions (what Polars uses): `(?i)` for case-insensitive, `\p{L}` for
any letter, `\b` for word boundary; look-behind is **not** available. TOML literal strings
(`'...'`) need no backslash escaping. Where a table maps pattern → label, order matters: the first
pattern that matches wins.

---

## 4. Step 2 — harmonize units

Two files next to the script, both edited by people, both read on every run.

### 4.1 `unit_synonyms.csv` — one spelling per unit

`unit_as_recorded,unit_to_use,note`. A list of spellings that mean the same unit and the one
spelling to keep. Applied to the recorded unit of every value after the split: the `unit` column
holds the spelling to use, the recorded spelling stays in `unit_recorded` of the decisions file.

- Lookup ignores case and all whitespace, so `mg/dl`, `MG / DL`, `ug/24hr` and `ug / 24hr` need no
  rows of their own.
- Every unit named anywhere in this file or in `conversions.csv` is a known spelling: `mg/dl` and
  `MG / DL` snap to `mg/dL` because `mg/dL` appears there. Only real synonyms need a row.
- A spelling that matches nothing is kept as it is.
- An empty `unit_to_use` means "this is not a unit" (`๐`, a Thai digit typed into the unit field);
  the unit becomes blank.

Seeded from the 2012–2025 files: `x 10\S\3/cumm`, `x 10\S\3/ul`, `K/ul` → `10^3/uL`; three
spellings of `mL/min/1.73m2`; `'c`/`Celsius` → `C`; `Copies`/`Copies/mL`/`cp/mL` → `copies/mL`;
`mg/dL1`/`mg%`/`mg.` → `mg/dL`; `mmol / 24hr` → `mmol/day`; and so on. A spelling the run meets
that is not in the file appears in `units_not_registered.csv` when the code has more than one
unit — that is where a new synonym is noticed.

### 4.2 `conversions.csv` — the registry of conversions

One row per `(code, unit_source)`, and **only pairs that are converted**. Nothing is converted
unless a row says so; a test with one unit, or a pair nobody has decided on, needs no row.

| Column                                              | Meaning                                                                                                                                                                    |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `code`, `unit_source`                           | the key — a `CODE_TEST` and a unit it has been seen with, spelled as `unit_synonyms.csv` spells it                                                                     |
| `unit_target`                                     | the unit the row converts to (the code's chosen unit)                                                                                                                      |
| `method`                                          | `1` prefix only · `4` molar → mass · `5` mass → molar · `9` plain multiplier · `0` no arithmetic (§4.3)                                                    |
| `factor`                                          | the SI prefix factor (methods 1/4/5) or the multiplier (9). Biology never hides in here: `mmol/L → mg/dL` is always `0.1`; the analyte's molar mass does the rest      |
| `molar_mass`                                      | g/mol, methods 4 and 5 only                                                                                                                                                |
| `test_name`, `note`, `added_by`, `added_on` | provenance                                                                                                                                                                 |

Rules the loader enforces before any file is processed: unique `(code, unit_source)`; units spelled
the way `unit_synonyms.csv` would spell them; a method the code knows; a factor for methods 1/4/5/9;
a molar mass for 4/5. A failing file stops the run with the offending rows listed. Whether a factor
is *right* is the responsibility of the person adding the row: work it out with §4.3 and check one
converted value by hand before trusting the row.

Same test under several codes (glucose has fifteen) means several rows, one per code. That is
intended: a decision is made per `CODE_TEST`, and the file shows every code someone has looked at.

**To add a conversion**: find the code in `code_dictionary.csv` (§4.5), pick the method and
factor from §4.3, append one line, run again, and look for the pair in `conversions_applied.csv`.

### 4.3 Choosing a method

Let $x$ be the reported number, $f$ the `factor`, $M$ the `molar_mass` in g/mol. The standardised
number is $x_{std}$.

| Method | Use when                                                                                                                                       | Formula                            | `factor` is                                                                                                                           | `molar_mass` |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | -------------- |
| `1`  | same quantity, different SI prefix or volume: `ug/mL → g/L`, `ng/mL → ng/L`, `mg/dL → g/dL`, `pg/mL → ng/dL`                        | $x_{std} = x \cdot f$            | the pure scaling: $10^{-3}$ for `ug/mL → g/L`, $10^{3}$ for `ng/mL → ng/L`, $0.1$ for `pg/mL → ng/dL`                     | leave empty    |
| `4`  | **molar → mass**: `mmol/L → mg/dL`, `umol/L → mg/dL`, `mmol/day → g/day`                                                       | $x_{std} = x \cdot f \cdot M$    | the prefix/volume part only: $0.1$ for `mmol/L → mg/dL`, $10^{-4}$ for `umol/L → mg/dL`, $10^{-3}$ for `mmol/day → g/day` | required       |
| `5`  | **mass → molar**: `mg/dL → mmol/L`, `ng/mL → nmol/L`                                                                              | $x_{std} = x \cdot \dfrac{f}{M}$ | the prefix/volume part only: $10$ for `mg/dL → mmol/L`, $10^{3}$ for `ng/mL → nmol/L`                                          | required       |
| `9`  | one plain multiplier that is neither a prefix nor a molar mass (assay constants, `pmol/L → pg/mL` for a peptide given as a published factor) | $x_{std} = x \cdot f$            | the whole factor                                                                                                                        | leave empty    |
| `0`  | the two spellings are the same unit and nothing should change (prefer `unit_synonyms.csv` for this; `0` exists for completeness)            | $x_{std} = x$                    | ignored                                                                                                                                 | leave empty    |

Why $f$ and $M$ are separate: the prefix factor is the same for every analyte, the molar mass is
the analyte. Keeping them apart means the prefix column is copied from the short table below and
the only number a person looks up is the molar mass.

Worked examples, each one row in the file:

- Cholesterol `200047`, `mmol/L → mg/dL`, method `4`, $f = 0.1$, $M = 386.65$:
  $x_{std} = x \cdot 0.1 \cdot 386.65 = 38.67\,x$. A 5.2 mmol/L reading becomes 201 mg/dL.
- Glucose `200076`, `mmol/L → mg/dL`, method `4`, $f = 0.1$, $M = 180.16$: $18.0\,x$.
- Creatinine `200058`, `umol/L → mg/dL`, method `4`, $f = 10^{-4}$, $M = 113.12$: $0.01131\,x$.
- Lactate `200098`, `mg/dL → mmol/L`, method `5`, $f = 10$, $M = 90.08$:
  $x_{std} = x \cdot 10 / 90.08 = 0.111\,x$.
- Triglyceride `200157`, `mmol/L → mg/dL`, method `4`, $f = 0.1$, $M = 885.7$: $88.57\,x$. Note
  the molar mass is the average of the mixture, not a single molecule.
- Phosphate `200090`, `mmol/L → mg/dL`, method `4`, $f = 0.1$, $M = 30.97$: $3.097\,x$. Reported
  as phosphorus, so the atomic mass of P, not the mass of the PO$_4$ ion (94.97 would give 9.5).
- Troponin T `200159`, `ng/mL → ng/L`, method `1`, $f = 1000$: $1000\,x$.
- Urine creatinine `150660`, `mg/dL → g/dL`, method `1`, $f = 10^{-3}$: $0.001\,x$. No molar mass:
  both units are mass.
- Albumin `200008`, `g/L → g/dL`, method `1`, $f = 0.1$: $0.1\,x$.
- NT-proBNP `200127`, `pmol/L → pg/mL`, method `9`, $f = 8.457$: the published conversion constant.

Prefix factors for the pairs that occur here (source → target, then $f$):

| Source → target      |    $f$ |  | Source → target       |               $f$ |
| --------------------- | -------: | - | ---------------------- | ------------------: |
| `mmol/L → mg/dL`   |      0.1 |  | `mg/dL → mmol/L`    |                  10 |
| `umol/L → mg/dL`   |   0.0001 |  | `umol/L → ug/dL`    |                 0.1 |
| `mmol/day → g/day` |    0.001 |  | `mmol/day → mg/day` |                   1 |
| `g/L → mg/dL`      |      100 |  | `g/L → g/dL`        |                 0.1 |
| `mg/dL → g/dL`     |    0.001 |  | `mg/dL → mg/L`      |                  10 |
| `ug/mL → g/L`      |    0.001 |  | `mg/mL → g/L`       |                   1 |
| `ng/mL → g/L`      | 0.000001 |  | `ng/mL → nmol/L`    |                1000 |
| `ng/mL → ng/L`     |     1000 |  | `pg/mL → ng/dL`     |                 0.1 |
| `ug/L → mg/L`      |    0.001 |  | `ug/mL → mg/L`      |                   1 |
| `mg/mL → mg/L`     |     1000 |  | `mg/L → ug/mL`      |                   1 |
| `ug/mL → ng/mL`    |     1000 |  | `ug/L → ng/mL`      |                   1 |
| `mmol/L → ug/L`    | 1000 (then × $M$) |  |                     |                     |

A pair not in this table: work it out from the prefixes ($\text{m} = 10^{-3}$, $\text{u} = 10^{-6}$,
$\text{n} = 10^{-9}$, $\text{p} = 10^{-12}$; $\text{dL} = 0.1\,\text{L}$, $\text{mL} = 10^{-3}\,\text{L}$)
and check one converted value by hand.

The conversion **direction** is a per-code choice, recorded in `unit_target`. The seed follows the
unit the lab reports today: lactate moved to `mmol/L` in 2015 so old `mg/dL` rows move forward,
glucose stayed `mg/dL` so its `mmol/L` rows move back. A house rule (all metabolites in `mg/dL`,
say) is one edit per code. A code cannot have both directions.

### 4.4 The five unit columns

| Column                                                | Content                                                                                                                                 |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `number_std`, `range_low_std`, `range_high_std` | converted when `(code, unit)` has a row in `conversions.csv`, otherwise **a copy** of the split value                                     |
| `unit_std`                                          | `unit_target` when converted, otherwise the unit as spelled after `unit_synonyms.csv`                                               |
| `conversion_status`                                 | `convert`, `not_registered` (no row for this pair — the normal case for single-unit tests), or null when the row carries no number |

`not_registered` is not a warning; it means untouched, which is right for every single-unit test.
Converted numbers are not rounded (a value of 1.5 ng/mL becomes 0.0000015 g/L, not 0).

### 4.5 The three run-level unit reports

All three are one file per run, at the run root, aggregated over every input file, sorted by
`code` so the several units of one code sit next to each other. `files` lists the input files a
row was seen in, `n_files` counts them.

`code_dictionary.csv` — **finding a code.** One row per `code`: `test_names` (every `SHORT_TEST`
spelling seen, joined with ` | `), `units_recorded` (every raw unit spelling), `units_used` (the
same after `unit_synonyms.csv`), `n_rows`, `n_files`. When you need "the code for HDL" or "which
codes report in `KUA/L`", search this file. It is also where a code carrying two different tests
shows up (`402053` lists both an influenza A and an influenza B name).

`units_not_registered.csv` — **the work queue.** Every `(code, unit)` pair, with the test names,
on a code that carries more than one non-blank unit and has no registry row for that pair — except
the unit that registered rows of that code convert *to*, which is the code's chosen unit. That is
the list to read before using a test in a project; each line becomes a row in `conversions.csv`,
or stays because no fixed factor exists (viral loads in copies vs IU, kit-specific antibody units).
Over sixteen years of this data it is about 85 lines.

`conversions_applied.csv` — **what this run changed.** Every conversion actually applied:
`code`, `unit_source`, `unit_target`, test names, rows touched, files. It records; it does not judge
the factor.

### 4.6 Where the registry came from

The seed is an earlier hand-built conversion table (181 codes; the method scheme and all molar
masses were kept) plus three rows from an older project mapping (`laboratory.xlsx`); the
`added_by` column of `conversions.csv` names the source of every row. Six entries of the
earlier table were corrected on the way, each marked in `note`: triglyceride molar mass 176.12 →
885.7 (factor 17.6 → 88.57), phosphate 94.97 → 30.97 (reported as phosphorus, 9.5 → 3.097),
lithium `mmol/L → ug/L` prefix 0.001 → 1000, transferrin `ng/mL → g/L` prefix 1e6 → 1e-6, urine
creatinine `mg/dL → g/dL` method 4 → 1, methanol `mg%` = `mg/dL` (× 10 → same unit). Pairs the
earlier table left without a method (viral load copies vs IU/mL, `KUA/L` vs `PAU/L`, mercury
`ug/day` vs `ug/L`, …) are **not** in the file: no constant exists, so they stay unconverted and
show in `units_not_registered.csv` after every run, with their test names, for whoever meets them
next.

---

## 5. What to read after a run

1. `lab_harmonize.log`: every file processed, no `FAILED`, and for each file a
   `row count check: source N = harmonized N` line (a mismatch is logged as `ROW COUNT MISMATCH`).
2. `distribution_comparison.csv`: `row_count_match` must be `true` and `parse_failures` must be
   **0** for every file — the latter counts `split`
   rows where nothing was extracted, so anything else means a pattern accepted a string the
   extractor cannot read. Compare the `split` / `review` shares with the previous run after a rule
   change.
3. `review/<stem>_review_top.csv`: the biggest things still unresolved. A frequent value here is
   either a rule to add (§3.7) or a deliberate exclusion (§3.5).
4. `review/<stem>_near_vocab.csv`: misspellings worth an explicit alternative (§3.6).
5. `units_not_registered.csv`: unit pairs to decide on before a project uses the test (§4.5).
6. `conversions_applied.csv`: confirm a row you just added actually fired.

---

## 6. Glossary

| Term | Meaning |
|---|---|
| shape | what a result string looks like; one of 17 labels (§3.2), or the `excluded_shape_label` (`microbiology`) for rows that were not processed |
| `split` / `missing` / `review` | the three outcomes of step 1 (§3.3) |
| typed columns | `operator`, `number`, `range_low`, `range_high`, `unit`, `value`, `qualifier` |
| `value` vs `qualifier` | `value` is the qualitative result (`Positive`); `qualifier` modifies or grades it (`Weakly`, `Trace`) and never stands in for a result |
| `_std` columns | step 2 output: converted where registered, otherwise a copy |
| `not_registered` | no row in `conversions.csv` for this `(code, unit)`; the value is untouched |
| distinct table | the `(result, unit, code, name)` rows with counts that all rules run on; written as `decisions/<stem>_decisions.csv` |
| `<stem>` | the input file name up to the first dot |
| method 1 / 4 / 5 / 9 / 0 | how a conversion is computed (§4.3) |
| `factor`, `molar_mass` | the SI scaling and the analyte's molar mass, kept separate (§4.3) |
