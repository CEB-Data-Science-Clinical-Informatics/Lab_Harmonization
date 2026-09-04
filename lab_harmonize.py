"""lab_harmonize.py — lab harmonization in two steps: split RESULT_VAL into standardized columns,
then harmonize units into standardized _std columns.

Run this file directly; no command-line arguments. It reads settings.toml (paths), criteria.toml
(split rules), unit_synonyms.csv and conversions.csv (unit rules) from its own folder and writes
one run_<timestamp>/ folder per run.

README.md says how to run it. HOW_IT_WORKS.md explains every rule, every output file and how to
extend the rule files. CHANGELOG.md records what changed and when.
"""

from __future__ import annotations

import hashlib
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # Python 3.9 / 3.10
    import tomli as tomllib

import polars as pl
import pyarrow.parquet as pq  # only to read the compression codec of the source files

HERE = Path(__file__).resolve().parent
SETTINGS_FILE = HERE / "settings.toml"
CRITERIA_FILE = HERE / "criteria.toml"
SYNONYMS_FILE = HERE / "unit_synonyms.csv"      # raw unit spelling -> canonical spelling
CONVERSIONS_FILE = HERE / "conversions.csv"     # (code, unit_source) -> unit_target, registered by people

# Internal column names. `_result` / `_unit` are the cleaned copies of the source columns that
# every rule reads; the nine SPLIT_COLUMNS are what this script adds to the data.
RESULT = "_result"
UNIT = "_unit"
CODE = "_code"
NAME = "_name"      # SHORT_TEST, carried along for the reports (never used by a rule)
N_ROWS = "_n_rows"
SPLIT_COLUMNS = [
    "shape", "split_action", "operator", "number", "range_low", "range_high",
    "unit", "value", "qualifier",
]
# Standardised copies: converted where a registered conversion exists, otherwise copied as is.
STD_COLUMNS = ["number_std", "range_low_std", "range_high_std", "unit_std", "conversion_status"]
NUMERIC_SHAPES = ["plain_numeric", "operator_numeric", "numeric_range", "verbal_operator_numeric"]
CONVERSION_METHODS = {"0", "1", "4", "5", "9"}

log = logging.getLogger("lab_harmonize")


# ----------------------------------------------------------------------------------------------
# 1. Configuration
# ----------------------------------------------------------------------------------------------

def load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def resolve_path(text: str, base: Path) -> Path:
    """Expand "~", accept forward slashes on Windows, resolve relative to `base`."""
    path = Path(text).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def make_run_folder(output_dir: Path) -> dict:
    """Create output_dir/run_<timestamp>/ with the three sub-folders every run writes into."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = output_dir / f"run_{stamp}"
    folders = {
        "root": root,
        "harmonized": root / "harmonized",  # same file name as the input, drop-in replacement
        "decisions": root / "decisions",  # <stem>_decisions.csv
        "review": root / "review",        # <stem>_review_top.csv, <stem>_near_vocab.csv
    }
    for name, folder in folders.items():
        if name != "harmonized":  # created only when a harmonized file is actually written
            folder.mkdir(parents=True, exist_ok=True)
    return folders


def start_logging(run_root: Path) -> Path:
    """Log to lab_harmonize.log in the run folder and to the console at the same time."""
    log_path = run_root / "lab_harmonize.log"
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    for handler in (logging.FileHandler(log_path, encoding="utf-8"),
                    logging.StreamHandler(sys.stdout)):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    return log_path


# ----------------------------------------------------------------------------------------------
# 2. Patterns — assemble the regular expressions from criteria.toml
# ----------------------------------------------------------------------------------------------

def alternation(items) -> str:
    """Join regex fragments into a non-capturing group: a|b|c -> (?:a|b|c)."""
    return "(?:" + "|".join(items) + ")"


def compile_patterns(cr: dict) -> dict:
    """Turn the criteria tables into the full-string patterns used by the classifier.

    Every key returned here is a plain string; the names match HOW_IT_WORKS.md.
    """
    g, num, q, det = cr["general"], cr["numeric"], cr["qualifier"], cr["detection"]
    simple = num["simple"]
    op_token = alternation(cr["operators"]["tokens"])
    verbal_word = alternation(cr["verbal_operators"].keys())
    modifier = alternation(q["modifiers"].keys())
    modifier_phrase = alternation(q["modifier_ranges"].keys()) + "|" + modifier
    modifier_phrase = "(?:" + modifier_phrase + ")"
    qualifier_word = alternation(q["values"].keys())
    annotation = q["annotation"]
    detection_tail = annotation + det["trailing"] + r"\s*$"
    detection_phrase = alternation(det["values"].keys())

    p = {
        # general
        "break": g["break_pattern"],
        "narrative_length": int(g["narrative_length"]),
        "numeric_trailing_dot": g["numeric_trailing_dot"],
        "word_leading_punct": g["word_leading_punct"],
        "word_trailing_punct": g["word_trailing_punct"],
        "standalone_number": g["standalone_number"],
        "percent_suffix": g["percent_suffix"],
        "percent_unit": g["percent_unit"],
        # missing
        "null_literal": cr["missing"]["null_literal"],
        "not_reported_sentinel": cr["missing"]["not_reported_sentinel"],
        # numeric family
        "plain_numeric": (rf"^{num['core']}\s*%?$"
                          rf"|^{num['leading_dot']}\s*%?$"),
        "operator_numeric": (rf"^{op_token}\s*{num['core']}\s*%?$"
                             rf"|^{op_token}\s*{num['leading_dot']}\s*%?$"),
        "operator_prefix": rf"^{op_token}\s*",
        "operator_capture": rf"^({op_token})",
        "operator_normalize": cr["operators"]["normalize"],
        "numeric_range": rf"^{simple}\s*{num['range_separator']}\s*{simple}\s*%?$",
        "range_low_capture": rf"^\s*({simple})",
        "range_high_capture": rf"{num['range_separator']}\s*({simple})",
        "ordinal_plus": r"^\s*\d+\+\s*$",
        "ordinal_capture": r"(\d+)\+",
        # verbal operators
        "verbal_operator_numeric": rf"(?i)^\s*{verbal_word}\s+{simple}\s*%?\s*$",
        "verbal_operator_prefix": rf"(?i)^\s*{verbal_word}\s*",
        "verbal_operators": cr["verbal_operators"],
        # qualifier family
        "qualifier_numeric": (rf"(?i)^\s*(?:{simple}\s*%?\s*)?"
                              rf"(?:{modifier_phrase}\s+)?"
                              rf"{qualifier_word}{annotation}"
                              rf"(?:\s*{simple}\s*%?)?\s*$"),
        "qualifier_ordinal": rf"(?i)^\s*{qualifier_word}\s+\d+\+\s*$",
        "qualifier_comparison": (rf"(?i)^\s*{qualifier_word}\s*(?:<=|>=|<|>|=)\s*{simple}\s*%?\s*$"
                                 rf"|^\s*(?:<=|>=|<|>|=)\s*{simple}\s*%?\s*{qualifier_word}\s*$"),
        "comparison_operator_capture": r"(<=|>=|<|>|=)",
        "simple_number_capture": rf"({simple})",
        "qualifier_values": q["values"],
        "qualifier_modifiers": q["modifiers"],
        "qualifier_modifier_ranges": q["modifier_ranges"],
        # detection family
        "qualifier_detection": rf"(?i)^\s*(?:{modifier}\s+)?{detection_phrase}{detection_tail}",
        "detection_values": {rf"^\s*(?:{modifier}\s+)?{pat}{detection_tail}": label
                             for pat, label in det["values"].items()},
        # severity family
        "severity_grade": rf"(?i)^\s*{alternation(cr['severity'].keys())}\s*$",
        "severity_values": cr["severity"],
    }
    return p


def validate_patterns(p: dict) -> None:
    """Fail early, with the offending key named, if any regex does not compile in Polars."""
    probe = pl.DataFrame({"x": ["probe"]})
    for key, pattern in p.items():
        candidates = []
        if isinstance(pattern, str) and key not in ("percent_unit",):
            candidates = [pattern]
        elif isinstance(pattern, dict):
            candidates = [k for k in pattern.keys() if isinstance(k, str)]
        for regex in candidates:
            try:
                probe.select(pl.col("x").str.contains(regex))
            except Exception as error:  # noqa: BLE001 - report and stop
                raise ValueError(f"criteria.toml: pattern under '{key}' does not compile: "
                                 f"{regex!r} -> {error}") from error


# ----------------------------------------------------------------------------------------------
# 2b. Unit tables — unit_synonyms.csv and conversions.csv
# ----------------------------------------------------------------------------------------------

def unit_key(text: str) -> str:
    """Matching key for a unit spelling: case and all whitespace ignored ("ug / 24hr" == "UG/24HR")."""
    return "".join(text.split()).lower()


def load_unit_synonyms(path: Path) -> dict:
    """{unit_key(spelling as recorded): spelling to use}. An empty target means "treat as blank".

    Only real synonyms are listed; a spelling with no row is kept as it is.

    Two rows that collapse to the same key must agree on the canonical, otherwise the file is refused.
    """
    table = pl.read_csv(path, infer_schema_length=0).fill_null("")
    synonyms, clashes = {}, []
    for row in table.iter_rows(named=True):
        raw, canonical = row["unit_as_recorded"].strip(), row["unit_to_use"].strip()
        if not raw:
            continue
        key = unit_key(raw)
        if key in synonyms and synonyms[key] != canonical:
            clashes.append(f"{raw!r} -> {canonical!r} conflicts with an earlier row -> {synonyms[key]!r}")
        synonyms[key] = canonical
    if clashes:
        raise ValueError("unit_synonyms.csv:\n  " + "\n  ".join(clashes))
    return synonyms


def add_known_spellings(synonyms: dict, conversions: pl.DataFrame) -> dict:
    """Every unit named in either file becomes a known spelling, so `mg/dl` or `MG / DL` snaps to
    `mg/dL` without a synonym row. Explicit synonym rows always win."""
    known = dict(synonyms)
    names = [u for u in synonyms.values() if u]
    names += conversions["unit_source"].to_list() + conversions["unit_target"].to_list()
    for name in names:
        known.setdefault(unit_key(name), name)
    return known


def canonical_unit(unit: pl.Expr, synonyms: dict) -> pl.Expr:
    """Map a recorded unit to its canonical spelling; unknown spellings pass through unchanged."""
    key = unit.str.replace_all(r"\s+", "").str.to_lowercase()
    mapped = key.replace_strict(synonyms, default=unit, return_dtype=pl.String)
    return pl.when(unit.is_null() | (mapped == "")).then(None).otherwise(mapped)


def load_conversions(path: Path, synonyms: dict) -> pl.DataFrame:
    """Read conversions.csv and refuse to run on a row that cannot be applied safely."""
    t = pl.read_csv(path, infer_schema_length=0)
    for col in ("code", "unit_source", "unit_target", "method"):
        t = t.with_columns(pl.col(col).fill_null("").str.strip_chars())
    t = t.with_columns(
        pl.col("factor").cast(pl.Float64, strict=False),
        pl.col("molar_mass").cast(pl.Float64, strict=False),
    )
    problems = []
    dup = t.group_by("code", "unit_source").len().filter(pl.col("len") > 1)
    for r in dup.iter_rows(named=True):
        problems.append(f"duplicate key code={r['code']} unit_source={r['unit_source']!r}")
    for r in t.iter_rows(named=True):
        where = f"code={r['code']} {r['unit_source']!r}->{r['unit_target']!r}"
        for u in (r["unit_source"], r["unit_target"]):
            if u and synonyms.get(unit_key(u), u) != u:
                problems.append(f"{where}: unit {u!r} is not the canonical spelling "
                                f"({synonyms.get(unit_key(u))!r})")
        m = r["method"]
        if m not in CONVERSION_METHODS:
            problems.append(f"{where}: method {m!r} unknown"); continue
        if m in ("1", "4", "5", "9") and r["factor"] is None:
            problems.append(f"{where}: method {m} needs a factor")
        if m in ("4", "5") and r["molar_mass"] is None:
            problems.append(f"{where}: method {m} needs a molar_mass")
    if problems:
        raise ValueError("conversions.csv:\n  " + "\n  ".join(problems))
    return t


# ----------------------------------------------------------------------------------------------
# 3. Classification — applied to the table of distinct values, not to the raw rows
# ----------------------------------------------------------------------------------------------

def normalize_result(raw: pl.Expr, p: dict) -> pl.Expr:
    """Source result -> _result: text, trimmed, numeric trailing dots and stray punctuation
    around a single word removed ("3.8." -> "3.8", "!few" -> "few", "TRACE'" -> "TRACE")."""
    return (raw.cast(pl.String).str.strip_chars()
            .str.replace(p["numeric_trailing_dot"], "${1}")
            .str.replace(p["word_leading_punct"], "${1}")
            .str.replace(p["word_trailing_punct"], "${1}"))


def normalize_unit(raw: pl.Expr) -> pl.Expr:
    """Source unit -> _unit: text, trimmed, empty string becomes null."""
    text = raw.cast(pl.String).str.strip_chars()
    return pl.when(text.is_null() | (text == "")).then(None).otherwise(text)


def label_chain(text_lc: pl.Expr, mapping: dict, wrap: str = r"\b{}\b") -> pl.Expr:
    """First pattern in `mapping` found in `text_lc` decides the label (None if no match)."""
    expr = pl.when(pl.lit(False)).then(pl.lit(None, dtype=pl.String))
    for pattern, label in mapping.items():
        expr = expr.when(text_lc.str.contains(wrap.format(pattern))).then(pl.lit(label))
    return expr.otherwise(pl.lit(None, dtype=pl.String))


def shape_expression(p: dict) -> pl.Expr:
    """Assign the shape of each distinct value. First match wins — order is the specification."""
    v = pl.col(RESULT)
    return (
        pl.when(v.is_null() | (v == "")).then(pl.lit("missing"))
        .when(v.str.contains(p["null_literal"])).then(pl.lit("null_literal"))
        .when(v.str.contains(p["not_reported_sentinel"])).then(pl.lit("not_reported_sentinel"))
        .when(v.str.contains(p["break"]) | (v.str.len_chars() > p["narrative_length"]))
        .then(pl.lit("narrative_text"))
        .when(v.str.contains(p["plain_numeric"])).then(pl.lit("plain_numeric"))
        .when(v.str.contains(p["operator_numeric"])).then(pl.lit("operator_numeric"))
        .when(v.str.contains(p["numeric_range"])).then(pl.lit("numeric_range"))
        .when(v.str.contains(p["ordinal_plus"])).then(pl.lit("ordinal_plus"))
        .when(v.str.contains(p["verbal_operator_numeric"])).then(pl.lit("verbal_operator_numeric"))
        .when(v.str.contains(p["qualifier_numeric"])).then(pl.lit("qualifier_numeric"))
        .when(v.str.contains(p["qualifier_ordinal"])).then(pl.lit("qualifier_ordinal"))
        .when(v.str.contains(p["qualifier_comparison"])).then(pl.lit("qualifier_comparison"))
        .when(v.str.contains(p["standalone_number"])).then(pl.lit("mixed_text_numeric"))
        .when(v.str.contains(p["qualifier_detection"])).then(pl.lit("qualifier_detection"))
        .when(v.str.contains(p["severity_grade"])).then(pl.lit("severity_grade"))
        .when(v.str.contains(r"\p{L}")).then(pl.lit("short_text"))
        .otherwise(pl.lit("punctuation_or_junk"))
    )


SPLIT_SHAPES = {  # shapes that resolve mechanically; everything else is missing or review
    "plain_numeric", "operator_numeric", "numeric_range", "ordinal_plus",
    "verbal_operator_numeric", "qualifier_numeric", "qualifier_ordinal",
    "qualifier_comparison", "qualifier_detection", "severity_grade",
}
MISSING_SHAPES = {"missing", "null_literal", "not_reported_sentinel", "punctuation_or_junk"}


def classify(distinct: pl.DataFrame, p: dict, synonyms: dict | None = None) -> pl.DataFrame:
    """Add shape, split_action and the seven typed columns to the distinct-values table.

    `synonyms` (from unit_synonyms.csv) makes the `unit` column the canonical spelling;
    the recorded spelling stays in `_unit`.
    """
    v = pl.col(RESULT)
    unit = canonical_unit(pl.col(UNIT), synonyms) if synonyms else pl.col(UNIT)
    shape = pl.col("shape")
    lc = v.str.to_lowercase()

    # -- which family the value belongs to (derived from shape, so the chain order holds)
    is_numeric = shape.is_in(["plain_numeric", "operator_numeric"])
    is_verbal = shape == "verbal_operator_numeric"
    is_range = shape == "numeric_range"
    is_ordinal = shape.is_in(["ordinal_plus", "qualifier_ordinal"])
    is_qualifier = shape == "qualifier_numeric"
    is_comparison = shape == "qualifier_comparison"
    is_detection = shape == "qualifier_detection"
    is_severity = shape == "severity_grade"
    is_qualitative = shape.is_in(["qualifier_numeric", "qualifier_ordinal",
                                  "qualifier_comparison", "qualifier_detection"])

    # -- a trailing % on the value must not contradict a different recorded unit
    has_percent = v.str.contains(p["percent_suffix"]).fill_null(False)
    percent_ok = ~has_percent | unit.is_null() | (unit == p["percent_unit"])
    can_split = shape.is_in(list(SPLIT_SHAPES)) & percent_ok

    # -- the numeric token of a plain / operator / verbal value
    numeric_token = (v.str.replace(p["operator_prefix"], "")
                      .str.replace(p["verbal_operator_prefix"], "")
                      .str.replace_all(",", "")
                      .str.replace(r"\s*%$", ""))
    first_number = v.str.extract(p["simple_number_capture"], 1).cast(pl.Float64, strict=False)

    # -- operators
    raw_operator = v.str.extract(p["operator_capture"], 1)
    normalized_operator = raw_operator
    for token, canonical in p["operator_normalize"].items():
        normalized_operator = (pl.when(raw_operator.str.contains(token))
                               .then(pl.lit(canonical)).otherwise(normalized_operator))
    verbal_operator = label_chain(lc, {w: spec["operator"] for w, spec in p["verbal_operators"].items()})
    verbal_label = label_chain(lc, {w: spec["label"] for w, spec in p["verbal_operators"].items()})

    # -- labels
    qualifier_value = label_chain(lc, p["qualifier_values"], wrap="{}")
    modifier_label = label_chain(lc, {**p["qualifier_modifier_ranges"], **p["qualifier_modifiers"]})
    detection_value = label_chain(lc, p["detection_values"], wrap="{}")
    severity_label = label_chain(lc, p["severity_values"])

    return distinct.with_columns(
        shape_expression(p).alias("shape"),
    ).with_columns(
        can_split.alias("can_split"),
        pl.when(shape.is_in(list(MISSING_SHAPES))).then(pl.lit("missing"))
          .when(can_split).then(pl.lit("split"))
          .otherwise(pl.lit("review")).alias("split_action"),
    ).with_columns(
        # operator
        pl.when(~pl.col("can_split")).then(None)
          .when(is_verbal).then(verbal_operator)
          .when(is_ordinal).then(pl.lit("+"))
          .when(is_comparison).then(v.str.extract(p["comparison_operator_capture"], 1))
          .when(is_numeric).then(normalized_operator)
          .otherwise(None).alias("operator"),
        # number
        pl.when(~pl.col("can_split")).then(None)
          .when(is_numeric | is_verbal).then(numeric_token.cast(pl.Float64, strict=False))
          .when(is_ordinal).then(v.str.extract(p["ordinal_capture"], 1).cast(pl.Float64, strict=False))
          .when(is_qualifier | is_comparison).then(first_number)
          .otherwise(None).alias("number"),
        # range bounds
        pl.when(pl.col("can_split") & is_range)
          .then(v.str.extract(p["range_low_capture"], 1).cast(pl.Float64, strict=False))
          .otherwise(None).alias("range_low"),
        pl.when(pl.col("can_split") & is_range)
          .then(v.str.extract(p["range_high_capture"], 1).cast(pl.Float64, strict=False))
          .otherwise(None).alias("range_high"),
        # unit: qualitative results and ordinals ("2+") have none; a grade ("Few /HPF") keeps it;
        # a trailing % overrides the recorded unit
        pl.when(~pl.col("can_split") | is_qualitative | is_ordinal).then(None)
          .when(has_percent).then(pl.lit(p["percent_unit"]))
          .otherwise(unit).alias("unit"),
        # value (qualitative label)
        pl.when(~pl.col("can_split")).then(None)
          .when(is_detection).then(detection_value)
          .when(is_qualitative).then(qualifier_value)
          .otherwise(None).alias("value"),
        # qualifier (modifier / grade / verbal word)
        pl.when(~pl.col("can_split")).then(None)
          .when(is_severity).then(severity_label)
          .when(is_qualifier | is_detection).then(modifier_label)
          .when(is_verbal).then(verbal_label)
          .otherwise(None).alias("qualifier"),
    ).drop("can_split")


# ----------------------------------------------------------------------------------------------
# 3a. Standardised columns — apply registered conversions, copy everything else
# ----------------------------------------------------------------------------------------------

def standardise(decisions: pl.DataFrame, conversions: pl.DataFrame) -> pl.DataFrame:
    """Add number_std / range_*_std / unit_std / conversion_status.

    A row is converted only when (code, unit) has a row in conversions.csv. Every other numeric row
    gets its values copied unchanged with `conversion_status = not_registered`; rows that carry no
    number get null.
    """
    reg = conversions.select(
        pl.col("code").alias(CODE), pl.col("unit_source").alias("unit"),
        "unit_target", "method", "factor", "molar_mass", pl.lit("convert").alias("_reg_status"),
    )
    d = decisions.join(reg, on=[CODE, "unit"], how="left")
    numeric = pl.col("shape").is_in(NUMERIC_SHAPES) & (pl.col("split_action") == "split")
    convert = numeric & pl.col("_reg_status").is_not_null()
    m, f, mm = pl.col("method"), pl.col("factor"), pl.col("molar_mass")
    multiplier = (pl.when(m == "0").then(pl.lit(1.0))
                    .when(m == "1").then(f)
                    .when(m == "4").then(f * mm)
                    .when(m == "5").then(f / mm)
                    .when(m == "9").then(f)
                    .otherwise(pl.lit(1.0)))

    def std(col: str) -> pl.Expr:
        return (pl.when(convert).then(pl.col(col) * multiplier)
                  .otherwise(pl.col(col)).alias(f"{col}_std"))

    return d.with_columns(
        std("number"), std("range_low"), std("range_high"),
        pl.when(convert).then(pl.col("unit_target")).otherwise(pl.col("unit")).alias("unit_std"),
        pl.when(~numeric).then(None)
          .when(pl.col("_reg_status").is_not_null()).then(pl.col("_reg_status"))
          .otherwise(pl.lit("not_registered")).alias("conversion_status"),
    ).drop("unit_target", "method", "factor", "molar_mass", "_reg_status")


def unit_reports(decisions: pl.DataFrame, conversions: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Two tables: (code, unit) pairs on multi-unit codes with no registry row; conversions applied."""
    numeric = decisions.filter(pl.col("shape").is_in(NUMERIC_SHAPES) & (pl.col("split_action") == "split"))
    names = pl.col(NAME).drop_nulls().unique().sort().str.join(" | ").alias("test_names")
    per_unit = numeric.group_by(CODE, "unit").agg(pl.col(N_ROWS).sum(), names,
                                                   pl.col("conversion_status").first())
    # a blank unit is a missing unit, not a second unit
    multi = (per_unit.filter(pl.col("unit").is_not_null()).group_by(CODE)
             .agg(pl.col("unit").n_unique().alias("n_units")).filter(pl.col("n_units") > 1))
    per_unit = per_unit.with_columns(pl.col("unit").fill_null("<blank>"))
    # the unit a registered conversion converts *to* is the code's chosen unit, not an open question
    targets = conversions.select(pl.col("code").alias(CODE), pl.col("unit_target").alias("unit")).unique()
    queue = (per_unit.join(multi, on=CODE).filter(pl.col("conversion_status") == "not_registered")
             .join(targets, on=[CODE, "unit"], how="anti")
             .rename({CODE: "code", N_ROWS: "n_rows"}).sort(["code", "n_rows"], descending=[False, True]))
    used = (numeric.filter(pl.col("conversion_status") == "convert")
            .group_by(CODE, "unit", "unit_std").agg(pl.col(N_ROWS).sum().alias("n_rows"), names)
            .rename({CODE: "code", "unit": "unit_source", "unit_std": "unit_target"})
            .sort("n_rows", descending=True))
    return queue, used


# ----------------------------------------------------------------------------------------------
# 3b. Near-miss report — audit aid, never a decision
# ----------------------------------------------------------------------------------------------

def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance; small inputs only (single words), so plain Python is fine."""
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def near_miss_report(decisions: pl.DataFrame, cr_near: dict) -> pl.DataFrame:
    """Single-word `review` values within `max_distance` edits of a vocabulary word.

    Output columns: value, nearest, distance, n_rows, codes. A person reads this to spot typos
    (`ferw`, `teace`) worth adding to criteria.toml. Nothing is changed automatically — the same
    distance also pairs `DONE` with `none` and `Male` with `many`.
    """
    vocab = [w.lower() for w in cr_near["vocabulary"]]
    max_d, min_len = int(cr_near["max_distance"]), int(cr_near["min_length"])
    words = (decisions.filter((pl.col("split_action") == "review")
                              & ~pl.col(RESULT).str.contains(r"\s")
                              & (pl.col(RESULT).str.len_chars() >= min_len)
                              & (pl.col(RESULT).str.len_chars() <= 20))
             .group_by(RESULT).agg(pl.col(N_ROWS).sum(), pl.col(CODE).n_unique().alias("codes")))
    rows = []
    for value, n_rows, codes in words.iter_rows():
        low = value.lower()
        if low in vocab:
            continue
        best = min(((edit_distance(low, w), w) for w in vocab if abs(len(w) - len(low)) <= max_d),
                   default=(max_d + 1, None))
        if best[0] <= max_d:
            rows.append((value, best[1], best[0], n_rows, codes))
    return pl.DataFrame(rows, schema=["value", "nearest", "distance", "n_rows", "codes"],
                        orient="row").sort("n_rows", descending=True)


# ----------------------------------------------------------------------------------------------
# 4. One input file
# ----------------------------------------------------------------------------------------------

# Parquet codec names as pyarrow reports them -> as polars' sink_parquet accepts them.
CODEC_NAMES = {"UNCOMPRESSED": "uncompressed", "SNAPPY": "snappy", "GZIP": "gzip", "LZO": "lzo",
               "BROTLI": "brotli", "LZ4": "lz4", "LZ4_RAW": "lz4", "ZSTD": "zstd"}


def output_compression(path: Path, setting: str) -> str:
    """`compression` from settings.toml wins; otherwise the codec the source file itself uses."""
    if setting:
        return setting
    metadata = pq.ParquetFile(path).metadata
    if metadata.num_row_groups == 0:
        return "zstd"
    return CODEC_NAMES.get(metadata.row_group(0).column(0).compression.upper(), "zstd")


def scan_file(path: Path, cols: dict, p: dict) -> tuple[pl.LazyFrame, pl.LazyFrame, pl.LazyFrame]:
    """Lazy frames of in-scope rows, of excluded rows, and of all rows (with _result/_unit/_code)."""
    lf = pl.scan_parquet(path)
    code = pl.col(cols["code"]).cast(pl.String)
    excluded = pl.lit(False)
    for prefix in cols["exclude_code_prefixes"]:
        excluded = excluded | code.str.starts_with(prefix)
    excluded = excluded.fill_null(False)  # a null code is in scope, not silently dropped
    name_col = cols.get("name", "")
    name = (pl.col(name_col).cast(pl.String).str.strip_chars() if name_col
            else pl.lit(None, dtype=pl.String))
    lf = lf.with_columns(
        normalize_result(pl.col(cols["result"]), p).alias(RESULT),
        normalize_unit(pl.col(cols["unit"])).alias(UNIT),
        code.alias(CODE),
        name.alias(NAME),
        excluded.alias("_excluded"),
    )
    return lf.filter(~pl.col("_excluded")), lf.filter(pl.col("_excluded")), lf


def distinct_values(lf: pl.LazyFrame) -> pl.DataFrame:
    """Collapse the rows to distinct (result, unit, code, name) with a row count. One pass."""
    return (lf.group_by(RESULT, UNIT, CODE, NAME).agg(pl.len().alias(N_ROWS))
              .collect(engine="streaming"))


def summarise(decisions: pl.DataFrame, n_excluded: int) -> dict:
    """Row-weighted counts for the log and the distribution comparison."""
    total_in_scope = int(decisions[N_ROWS].sum())
    by_action = (decisions.group_by("split_action").agg(pl.col(N_ROWS).sum())
                 .to_dict(as_series=False))
    by_shape = (decisions.group_by("shape").agg(pl.col(N_ROWS).sum())
                .sort(N_ROWS, descending=True).to_dict(as_series=False))
    parse_failures = int(decisions.filter(
        (pl.col("split_action") == "split")
        & pl.col("number").is_null() & pl.col("value").is_null()
        & pl.col("range_low").is_null() & pl.col("qualifier").is_null()
    )[N_ROWS].sum())
    return {
        "total_rows": total_in_scope + n_excluded,
        "excluded_rows": n_excluded,
        "in_scope_rows": total_in_scope,
        "actions": dict(zip(by_action["split_action"], by_action[N_ROWS])),
        "shapes": dict(zip(by_shape["shape"], by_shape[N_ROWS])),
        "distinct_values": decisions.height,
        "codes_in_review": decisions.filter(pl.col("split_action") == "review")[CODE].n_unique(),
        "parse_failures": parse_failures,
    }


def process_file(path: Path, settings: dict, p: dict, folders: dict, near: dict,
                 synonyms: dict, conversions: pl.DataFrame) -> dict:
    cols, out = settings["columns"], settings["output"]
    started = time.perf_counter()
    stem = path.name.split(".")[0]
    log.info("=" * 78)
    log.info("FILE %s", path.name)

    in_scope, excluded, all_rows = scan_file(path, cols, p)
    n_excluded = excluded.select(pl.len()).collect(engine="streaming").item()

    # 1. distinct values -> decisions (all regex work happens here, once per distinct value)
    distinct = distinct_values(in_scope)
    decisions = standardise(classify(distinct, p, synonyms), conversions)
    log.info("rows: total %s | excluded (%s) %s | in scope %s | distinct (result, unit, code, name) %s",
             f"{int(distinct[N_ROWS].sum()) + n_excluded:,}", ",".join(cols["exclude_code_prefixes"]),
             f"{n_excluded:,}", f"{int(distinct[N_ROWS].sum()):,}", f"{distinct.height:,}")

    # 2. statistics
    stats = summarise(decisions, n_excluded)
    for action in ("split", "missing", "review"):
        n = stats["actions"].get(action, 0)
        log.info("  %-8s %14s  %6.2f%% of total", action, f"{n:,}", 100 * n / stats["total_rows"])
    for shape, n in stats["shapes"].items():
        log.info("    shape %-24s %14s", shape, f"{n:,}")
    log.info("  review spans %s distinct codes; split rows with nothing extracted: %s",
             f"{stats['codes_in_review']:,}", f"{stats['parse_failures']:,}")
    by_status = (decisions.filter(pl.col("conversion_status").is_not_null())
                 .group_by("conversion_status").agg(pl.col(N_ROWS).sum()).sort("conversion_status"))
    for r in by_status.iter_rows(named=True):
        log.info("    units %-15s %14s rows", r["conversion_status"], f"{r[N_ROWS]:,}")

    # 3. audit files: every distinct decision, and the top still-to-review values
    decisions_out = (decisions.sort(N_ROWS, descending=True)
                     .rename({RESULT: "result", UNIT: "unit_recorded", CODE: "code", NAME: "name",
                              N_ROWS: "n_rows"}))
    decisions_out.write_csv(folders["decisions"] / f"{stem}_decisions.csv")
    queue, used = unit_reports(decisions, conversions)
    log.info("  units: %s (code, unit) pairs on multi-unit codes still unregistered (%s rows); "
             "%s conversions applied (collected into the run-level unit reports)",
             f"{queue.height:,}", f"{int(queue['n_rows'].sum()) if queue.height else 0:,}",
             f"{used.height:,}")
    (decisions_out.filter(pl.col("split_action") == "review")
     .head(int(out["review_top_n"]))
     .write_csv(folders["review"] / f"{stem}_review_top.csv"))
    near_misses = near_miss_report(decisions, near)
    near_misses.write_csv(folders["review"] / f"{stem}_near_vocab.csv")
    log.info("  near-miss report: %s single-word review values within %s edits of the vocabulary "
             "(%s rows) -> review/%s_near_vocab.csv", f"{near_misses.height:,}", near["max_distance"],
             f"{int(near_misses['n_rows'].sum()) if near_misses.height else 0:,}", stem)

    # 4. optional: join the decisions back to ALL rows (excluded rows stay, untouched, in their
    #    original position with shape = excluded_shape_label) and write a new parquet file
    if out["write_harmonized_files"]:
        keep = out["keep_columns"] or [c for c in all_rows.collect_schema().names()
                                       if not c.startswith("_")]
        key = [RESULT, UNIT, CODE]
        lookup = (decisions.select(key + SPLIT_COLUMNS + STD_COLUMNS).unique(subset=key)
                  .with_columns(pl.col(RESULT).fill_null(""), pl.col(UNIT).fill_null(""),
                                pl.col(CODE).fill_null("")))
        folders["harmonized"].mkdir(parents=True, exist_ok=True)
        target = folders["harmonized"] / path.name  # original name: the file can replace the source
        codec = output_compression(path, out.get("compression", ""))
        label = cols.get("excluded_shape_label", "excluded")
        (all_rows.with_columns(pl.col(RESULT).fill_null(""), pl.col(UNIT).fill_null(""),
                               pl.col(CODE).fill_null(""))
                 .join(lookup.lazy(), on=key, how="left", maintain_order="left")  # keep source row order
                 .with_columns(pl.when(pl.col("_excluded")).then(pl.lit(label))
                                 .otherwise(pl.col("shape")).alias("shape"))
                 .select(keep + SPLIT_COLUMNS + STD_COLUMNS)
                 .sink_parquet(target, compression=codec))
        rows_written = pl.scan_parquet(target).select(pl.len()).collect().item()
        stats["rows_written"] = rows_written
        stats["row_count_match"] = rows_written == stats["total_rows"]
        log.info("  wrote harmonized/%s (%s; %s excluded rows kept as shape=%s)",
                 target.name, codec, f"{n_excluded:,}", label)
        if stats["row_count_match"]:
            log.info("  row count check: source %s = harmonized %s", f"{stats['total_rows']:,}", f"{rows_written:,}")
        else:
            log.error("  ROW COUNT MISMATCH: source %s, harmonized %s", f"{stats['total_rows']:,}", f"{rows_written:,}")

    stats["file"] = path.name
    stats["unit_queue"] = queue.with_columns(pl.lit(path.name).alias("file"))
    stats["code_dict"] = code_dictionary(decisions).with_columns(pl.lit(path.name).alias("file"))
    stats["conversions_used"] = used.with_columns(pl.lit(path.name).alias("file"))
    stats["seconds"] = round(time.perf_counter() - started, 1)
    log.info("  done in %.1f s", stats["seconds"])
    return stats


# ----------------------------------------------------------------------------------------------
# 5. Distribution comparison across files
# ----------------------------------------------------------------------------------------------

def comparison_table(all_stats: list[dict]) -> pl.DataFrame:
    shapes = sorted({s for st in all_stats for s in st["shapes"]})
    rows = []
    for st in all_stats:
        total = st["total_rows"]
        row = {  # frames in st (unit_queue, conversions_used) are handled by unit_reports_across_files

            "file": st["file"],
            "total_rows": total,
            "excluded_rows": st["excluded_rows"],
            "split_pct": round(100 * st["actions"].get("split", 0) / total, 2),
            "missing_pct": round(100 * st["actions"].get("missing", 0) / total, 2),
            "review_pct": round(100 * st["actions"].get("review", 0) / total, 2),
            "codes_in_review": st["codes_in_review"],
            "parse_failures": st["parse_failures"],
            "rows_written": st.get("rows_written"),
            "row_count_match": st.get("row_count_match"),
        }
        for shape in shapes:
            row[f"shape_{shape}_pct"] = round(100 * st["shapes"].get(shape, 0) / total, 3)
        rows.append(row)
    return pl.DataFrame(rows)


def code_dictionary(decisions: pl.DataFrame) -> pl.DataFrame:
    """One row per code: every test name, every recorded unit spelling, every unit after synonyms."""
    joined = lambda col: pl.col(col).drop_nulls().unique().sort().str.join(" | ")  # noqa: E731
    return (decisions.group_by(CODE)
            .agg(joined(NAME).alias("test_names"), joined(UNIT).alias("units_recorded"),
                 joined("unit").alias("units_used"), pl.col(N_ROWS).sum().alias("n_rows"))
            .rename({CODE: "code"}))


def code_dictionary_across_files(all_stats: list[dict]) -> pl.DataFrame:
    """Merge the per-file dictionaries: names and units are the union over all files."""
    union = lambda col: (pl.col(col).str.split(" | ").list.explode().drop_nulls().unique().sort()  # noqa: E731
                         .str.join(" | ").alias(col))
    d = pl.concat([st["code_dict"] for st in all_stats])
    return (d.group_by("code")
            .agg(union("test_names"), union("units_recorded"), union("units_used"),
                 pl.col("n_rows").sum(), pl.col("file").n_unique().alias("n_files"))
            .sort("code"))


def unit_reports_across_files(all_stats: list[dict]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """One row per (code, unit) over the whole run, with the files it was seen in.

    Sorted by code so the several units of one code sit next to each other.
    """
    queue = pl.concat([st["unit_queue"] for st in all_stats])
    used = pl.concat([st["conversions_used"] for st in all_stats])
    all_names = (pl.col("test_names").str.split(" | ").list.explode().drop_nulls().unique().sort()
                 .str.join(" | ").alias("test_names"))
    queue_all = (queue.group_by("code", "unit")
                 .agg(all_names, pl.col("n_rows").sum(), pl.col("file").n_unique().alias("n_files"),
                      pl.col("file").sort().str.join("; ").alias("files"))
                 .sort(["code", "n_rows"], descending=[False, True])) if queue.height else queue.drop("file")
    used_all = (used.group_by("code", "unit_source", "unit_target")
                .agg(all_names, pl.col("n_rows").sum(), pl.col("file").n_unique().alias("n_files"),
                     pl.col("file").sort().str.join("; ").alias("files"))
                .sort(["code", "n_rows"], descending=[False, True])) if used.height else used.drop("file")
    return queue_all, used_all


def log_comparison(table: pl.DataFrame) -> None:
    log.info("=" * 78)
    log.info("DISTRIBUTION COMPARISON (percent of each file's total rows)")
    log.info("%-40s %14s %8s %8s %8s", "file", "rows", "split", "missing", "review")
    for r in table.iter_rows(named=True):
        log.info("%-40s %14s %7.2f%% %7.2f%% %7.2f%%", r["file"], f"{r['total_rows']:,}",
                 r["split_pct"], r["missing_pct"], r["review_pct"])
    spread = table["review_pct"].max() - table["review_pct"].min()
    log.info("review share ranges %.2f%% to %.2f%% (spread %.2f points)",
             table["review_pct"].min(), table["review_pct"].max(), spread)


# ----------------------------------------------------------------------------------------------
# 6. Run
# ----------------------------------------------------------------------------------------------

def select_files(settings: dict) -> list[Path]:
    lab_dir = resolve_path(settings["paths"]["lab_dir"], HERE)
    if not lab_dir.is_dir():
        raise FileNotFoundError(f"lab_dir does not exist: {lab_dir}")
    names = settings["input"]["files"]
    if names:
        files = [lab_dir / name for name in names]
        missing = [f.name for f in files if not f.is_file()]
        if missing:
            raise FileNotFoundError(f"listed in settings.toml but not found in {lab_dir}: {missing}")
        return files
    files = sorted(f for f in lab_dir.glob(settings["input"]["file_glob"]) if f.is_file())
    if not files:
        raise FileNotFoundError(f"no file matches {settings['input']['file_glob']!r} in {lab_dir}")
    return files


def run() -> None:
    settings = load_toml(SETTINGS_FILE)
    criteria = load_toml(CRITERIA_FILE)
    output_dir = resolve_path(settings["paths"]["output_dir"] or ".", HERE)
    folders = make_run_folder(output_dir)
    log_path = start_logging(folders["root"])

    log.info("lab_harmonize started; python %s, polars %s", sys.version.split()[0], pl.__version__)
    for f in (SETTINGS_FILE, CRITERIA_FILE, SYNONYMS_FILE, CONVERSIONS_FILE):
        log.info("%-18s sha256 %s", f.name, file_digest(f))
    synonyms = load_unit_synonyms(SYNONYMS_FILE)
    conversions = load_conversions(CONVERSIONS_FILE, synonyms)
    synonyms = add_known_spellings(synonyms, conversions)
    log.info("unit synonyms: %d spellings mapped; conversions registered: %d (code, unit) pairs",
             len(synonyms), conversions.height)

    patterns = compile_patterns(criteria)
    validate_patterns(patterns)
    for key in ("plain_numeric", "operator_numeric", "numeric_range", "verbal_operator_numeric",
                "qualifier_numeric", "qualifier_ordinal", "qualifier_comparison",
                "qualifier_detection", "severity_grade", "null_literal", "not_reported_sentinel",
                "standalone_number", "numeric_trailing_dot"):
        log.info("pattern %-24s %s", key, patterns[key])

    files = select_files(settings)
    log.info("%d file(s) to process from %s", len(files), files[0].parent)

    all_stats = []
    for path in files:
        try:
            all_stats.append(process_file(path, settings, patterns, folders, criteria["near_miss"],
                                          synonyms, conversions))
        except Exception:  # noqa: BLE001 - keep going, the log records the failure
            log.exception("FAILED %s", path.name)

    if all_stats:
        table = comparison_table(all_stats)
        table.write_csv(folders["root"] / "distribution_comparison.csv")
        log_comparison(table)
        queue_all, used_all = unit_reports_across_files(all_stats)
        queue_all.write_csv(folders["root"] / "units_not_registered.csv")
        used_all.write_csv(folders["root"] / "conversions_applied.csv")
        codes = code_dictionary_across_files(all_stats)
        codes.write_csv(folders["root"] / "code_dictionary.csv")
        log.info("CODES: %s codes seen across the run -> code_dictionary.csv (names and units per code)",
                 f"{codes.height:,}")
        log.info("UNITS: %s (code, unit) pairs unregistered across the run -> units_not_registered.csv; "
                 "%s conversions applied -> conversions_applied.csv",
                 f"{queue_all.height:,}", f"{used_all.height:,}")
    log.info("outputs in %s", folders["root"])


if __name__ == "__main__":
    run()
