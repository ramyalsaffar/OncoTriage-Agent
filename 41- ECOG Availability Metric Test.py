# ECOG Availability Metric Test
###############################

"""
ECOG Availability Metric Test

Covers `ecog_unavailable_rate` in `20- Drift Detection.py`.

The three ECOG columns record the value and the selection path, but until this
metric nothing read them, and storing a failure is not the same as noticing it.
The failure they exist to expose: a corpus regenerated with a
DATA_SNAPSHOT_DATE older than its own observations sends every patient down the
`all_after_reference_date` path. Every ECOG criterion becomes not_evaluable,
eligible-match counts fall across the board, and that is visible only to
someone who thinks to write the query.

Why a threshold and not a z-score, which this file asserts structurally: a
z-score against the baseline window reads ~0 if the baseline window was itself
captured after the bad regeneration. The metric would go silent in exactly the
case it was built for. A proportion is alarming at 1.0 whatever the baseline
was.

Covers:
    1. The headline case — every row `all_after_reference_date` gives a rate of
       exactly 1.0 and an alert.
    2. Denominator excludes rows whose `ecog_selection` is NULL. Those predate
       the columns; counting them as "fine" dilutes the rate toward zero
       exactly when the corpus is oldest.
    3. Numerator excludes `none_recorded`. A corpus where nobody ever had an
       ECOG scores 0.0, not 1.0 — no observation is a property of the source
       data, not a reference-date mismatch.
    4. ECOG 0 counts as available. It is falsy and it is the most eligible
       score there is; an implementation testing truthiness would report a
       fully-active cohort as unavailable.
    5. A zero denominator returns insufficient data, never a rate and never an
       alert — the state every existing row is in today.
    6. Result-dict shape matches the baseline-comparison metrics, so
       `log_drift_metrics` and `print_drift_details` need no special case, and a
       row lands in `drift_metrics` with the threshold and alert set.
    7. The alert carries the reference-date diagnosis in `notes`, which reaches
       the database.
    8. STRUCTURAL — the metric reads no baseline, computes no z-score, and its
       threshold comes from the config module.

No network, no LLM, no pipeline run. Frames are built in memory; the one
database test uses a throwaway SQLite file, so the real inferences.db is never
opened.

Run from terminal (or F5 in Spyder):
    python "41- ECOG Availability Metric Test.py"

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# Item 20a: this file sits in the code directory, so __file__ locates it with
# no hardcoded path. __file__ is bound when the file is run as a script (every
# documented entry point for it) and when Spyder runfile()s it. In a bare
# interactive paste it is not bound, and the working directory is the only
# remaining candidate -- taken, but announced, never silently.
import os as _os_boot
if "__file__" in globals():
    _code_dir = _os_boot.path.dirname(_os_boot.path.abspath(__file__)) + _os_boot.sep
else:
    _code_dir = _os_boot.getcwd() + _os_boot.sep
    print(f"[Bootstrap] __file__ unbound; using the working directory as the code directory: {_code_dir}")
del _os_boot

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

# 14 is deliberately NOT chained here: it connects at load time, and the one
# database test below repoints inferences_path at a temporary file first.
exec_chain(
    ["03- Config.py", "20- Drift Detection.py"],
    caller_file=_code_dir + "41- ECOG Availability Metric Test.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 03 → 20",
)


#------------------------------------------------------------------------------


import ast
import shutil
import tempfile
import textwrap


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(
            f"{label}\n          expected: {expected}\n          actual:   {actual}"
        )
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


# ===========================================================================
# FIXTURES
# ===========================================================================

def rows(*specs) -> pd.DataFrame:
    """Build an inference frame from (selection, value, count) triples.

    A count of N repeats that row N times. `None` for selection is SQL NULL —
    a row written before the ecog_* columns existed.
    """
    records = []
    for selection, value, n in specs:
        records.extend([{"ecog_selection": selection, "ecog_value": value}] * n)
    return pd.DataFrame(records, columns=["ecog_selection", "ecog_value"])


# Enough rows to clear MIN_SAMPLES_COMPARISON in every fixture that should
# produce a rate.
_N = max(MIN_SAMPLES_COMPARISON, 10)

SCORED       = ("most_recent_on_or_before_reference_date", 1, _N)
SCORED_ZERO  = ("most_recent_on_or_before_reference_date", 0, _N)
ALL_AFTER    = ("all_after_reference_date", None, _N)
UNDATED_AMB  = ("undated_ambiguous", None, _N)
NO_OBS       = ("none_recorded", None, _N)
PRE_MIGRATION = (None, None, _N)

REQUIRED_KEYS = {"metric_value", "threshold", "alert", "notes"}


print("\n" + "=" * 70)
print("ECOG AVAILABILITY METRIC TEST")
print("=" * 70)


# ===========================================================================
# 1. THE HEADLINE CASE -- every row unusable
# ===========================================================================

print("\n" + "=" * 70)
print("1. Every row all_after_reference_date -> rate 1.0 and an alert")
print("=" * 70)

_all_after = ecog_unavailable_rate(rows(ALL_AFTER))
check("rate is exactly 1.0", _all_after["metric_value"], 1.0)
check("it alerts", _all_after["alert"], 1)
check("numerator is every row", _all_after["numerator"], _N)
check("denominator is every row", _all_after["denominator"], _N)
check("threshold comes from the config module",
      _all_after["threshold"], ECOG_UNAVAILABLE_RATE_THRESHOLD)

# undated_ambiguous is the other unusable path and must count the same way.
_undated = ecog_unavailable_rate(rows(UNDATED_AMB))
check("undated_ambiguous also counts as unavailable",
      _undated["metric_value"], 1.0)
check("and alerts", _undated["alert"], 1)

# A healthy corpus.
_healthy = ecog_unavailable_rate(rows(SCORED))
check("a fully scored corpus rates 0.0", _healthy["metric_value"], 0.0)
check("and does not alert", _healthy["alert"], 0)
check("and carries no notes", _healthy["notes"], None)

# Mixed, on both sides of the threshold.
_half = ecog_unavailable_rate(rows(("all_after_reference_date", None, 5),
                                   ("most_recent_on_or_before_reference_date", 2, 5)))
check("a 50/50 split rates 0.5", _half["metric_value"], 0.5)
check("and alerts at the default threshold", _half["alert"], 1)

_one_in_ten = ecog_unavailable_rate(rows(("all_after_reference_date", None, 1),
                                         ("most_recent_on_or_before_reference_date", 3, 9)))
check("1 unusable in 10 rates 0.1", _one_in_ten["metric_value"], 0.1)
check("and stays below the threshold", _one_in_ten["alert"], 0)


# ===========================================================================
# 2. PRE-MIGRATION ROWS LEAVE THE DENOMINATOR
# ===========================================================================

print("\n" + "=" * 70)
print("2. ecog_selection NULL is excluded from the denominator")
print("=" * 70)

# The dilution this prevents: 10 unusable rows beside 990 pre-migration ones.
# Counting the NULLs as "fine" would report 0.01 and stay silent.
_diluted = ecog_unavailable_rate(rows(ALL_AFTER, (None, None, 990)))
check("rate is computed on reporting rows only", _diluted["metric_value"], 1.0)
check("so the alert still fires", _diluted["alert"], 1)
check("denominator counts only reporting rows", _diluted["denominator"], _N)
check("excluded rows are reported, not dropped silently",
      _diluted["rows_pre_migration"], 990)

_mixed_eras = ecog_unavailable_rate(rows(ALL_AFTER, SCORED, PRE_MIGRATION))
check("mixed eras: denominator is the reporting rows",
      _mixed_eras["denominator"], 2 * _N)
check("mixed eras: rate is 0.5", _mixed_eras["metric_value"], 0.5)


# ===========================================================================
# 3. none_recorded LEAVES THE NUMERATOR, NOT THE DENOMINATOR
# ===========================================================================

print("\n" + "=" * 70)
print("3. Patients with no observation are not a reference-date mismatch")
print("=" * 70)

_no_obs = ecog_unavailable_rate(rows(NO_OBS))
check("a corpus where nobody has an ECOG rates 0.0",
      _no_obs["metric_value"], 0.0)
check("and does not alert", _no_obs["alert"], 0)
check("but those rows DO count in the denominator", _no_obs["denominator"], _N)
check("and are reported separately", _no_obs["rows_no_observation"], _N)

# The distinction that matters: same number of NULL ecog_value in both frames,
# opposite verdicts.
_unusable_only = ecog_unavailable_rate(rows(ALL_AFTER))
check("none_recorded and all_after_reference_date both have NULL ecog_value",
      (_no_obs["denominator"], _unusable_only["denominator"]), (_N, _N))
check("but only one of them alerts",
      (_no_obs["alert"], _unusable_only["alert"]), (0, 1))

_half_no_obs = ecog_unavailable_rate(rows(NO_OBS, ALL_AFTER))
check("half no-observation, half unusable rates 0.5",
      _half_no_obs["metric_value"], 0.5)


# ===========================================================================
# 4. ECOG 0 IS AVAILABLE
# ===========================================================================

print("\n" + "=" * 70)
print("4. ECOG 0 counts as available, not as missing")
print("=" * 70)

_zeros = ecog_unavailable_rate(rows(SCORED_ZERO))
check("a cohort scored entirely 0 rates 0.0", _zeros["metric_value"], 0.0)
check("and does not alert", _zeros["alert"], 0)
check("numerator is empty", _zeros["numerator"], 0)

_zeros_and_unusable = ecog_unavailable_rate(rows(SCORED_ZERO, ALL_AFTER))
check("ECOG 0 rows are not counted as unavailable",
      _zeros_and_unusable["metric_value"], 0.5)


# ===========================================================================
# 5. INSUFFICIENT DATA IS NOT A ZERO RATE
# ===========================================================================

print("\n" + "=" * 70)
print("5. Zero denominator returns insufficient data, not 0.0")
print("=" * 70)

# The state every row in inferences is in today.
_all_pre = ecog_unavailable_rate(rows(PRE_MIGRATION))
check("metric_value is None, not 0.0", _all_pre["metric_value"], None)
check("it does not alert", _all_pre["alert"], 0)
check("denominator is 0", _all_pre["denominator"], 0)
check("notes explain why", "predate the ecog_* columns" in (_all_pre["notes"] or ""), True)
check("insufficient data is distinguishable from a clean 0.0 rate",
      _all_pre["metric_value"] == _healthy["metric_value"], False)

_empty = ecog_unavailable_rate(rows())
check("an empty frame is also insufficient", _empty["metric_value"], None)
check("and does not alert", _empty["alert"], 0)

# A denominator of 1 that happens to be unusable is a rate of 1.0 on one
# patient: noise wearing the costume of the alarm.
_one_row = ecog_unavailable_rate(rows(("all_after_reference_date", None, 1)))
check("a single reporting row is below the sample floor",
      _one_row["metric_value"], None)
check("and does not alert on n=1", _one_row["alert"], 0)
check("the floor is the file's existing comparison minimum",
      f">= {MIN_SAMPLES_COMPARISON}" in (_one_row["notes"] or ""), True)

# A database that never ran the migration has no such columns at all.
_no_columns = ecog_unavailable_rate(pd.DataFrame({"patient_id": ["a", "b"]}))
check("missing columns return insufficient data rather than raising",
      _no_columns["metric_value"], None)
check("and do not alert", _no_columns["alert"], 0)
check("and say which columns were absent",
      "ecog_selection" in (_no_columns["notes"] or ""), True)


# ===========================================================================
# 6. RESULT SHAPE MATCHES THE EXISTING METRICS
# ===========================================================================

print("\n" + "=" * 70)
print("6. Result dict slots into the existing reporting")
print("=" * 70)

_reference = z_score_drift(np.array([1.0, 2.0, 3.0]), np.array([2.0]))
_shared = REQUIRED_KEYS

for _label, _res in (("alerting", _all_after), ("clean", _healthy),
                     ("insufficient", _all_pre)):
    check(f"{_label} result carries every shared key",
          sorted(_shared - set(_res)), [])
check("the shared keys are the ones z_score_drift also returns",
      sorted(_shared - set(_reference)), [])

check("alert is an int, as log_drift_metrics expects",
      all(isinstance(r["alert"], int) for r in (_all_after, _healthy, _all_pre)), True)
check("metric_value is a float when present",
      isinstance(_all_after["metric_value"], float), True)

# No baseline keys: log_drift_metrics copies metric_value into the z_score
# column only when baseline_mean AND baseline_std are both present. A threshold
# alert must not be recorded as a z-score.
check("no baseline_mean", "baseline_mean" in _all_after, False)
check("no baseline_std", "baseline_std" in _all_after, False)
check("no p_value", "p_value" in _all_after, False)

_bundle = detect_data_availability(rows(ALL_AFTER))
check("detect_data_availability returns the metric under its name",
      list(_bundle), ["ecog_unavailable_rate"])
check("and it alerts", _bundle["ecog_unavailable_rate"]["alert"], 1)


# ===========================================================================
# 7. THE ALERT CARRIES ITS DIAGNOSIS, AND IT REACHES THE DATABASE
# ===========================================================================

print("\n" + "=" * 70)
print("7. The stored row names the reference-date mismatch")
print("=" * 70)

_notes = _all_after["notes"] or ""
check("notes name DATA_SNAPSHOT_DATE", "DATA_SNAPSHOT_DATE" in _notes, True)
check("notes say the corpus and the snapshot disagree",
      "disagree" in _notes, True)
check("notes name the resulting selection path",
      "all_after_reference_date" in _notes, True)
check("notes carry the counts", f"{_N}/{_N}" in _notes, True)
check("a clean result carries no diagnosis", _healthy["notes"], None)

# Round-trip through log_drift_metrics into a throwaway database.
#
# ITEM 20c, PASS 3b: THIS NO LONGER REBINDS A GLOBAL.
#
# It used to do exactly this:
#
#     inferences_path = os.path.join(_TMP_DIR, "drift_test.db")
#
# and rely on File 20's log_drift_metrics reading that name out of the shared
# exec namespace at call time. That worked only because every project file was
# exec'd into one dict. File 20's definitions live in
# oncotriage/monitoring/drift.py now, and a module function resolves its globals
# in its OWN module -- so the rebinding above would have reached nothing, and
# this test would have written drift rows into the REAL inferences.db while
# printing the name of the temporary file it thought it was using. Silent in
# both directions.
#
# CLAUDE.md named this file as "the one file that still rebinds inferences_path
# without passing a path", left that way until File 20 converted. It has now,
# and this is the change: log_drift_metrics takes db_path, and this test passes
# it. No writer anywhere in the repository still depends on rebinding a shared
# global.
_TMP_DIR = tempfile.mkdtemp(prefix="oncotriage_ecog_availability_")
_SCRATCH_DB = os.path.join(_TMP_DIR, "drift_test.db")
_DECOY_DB = os.path.join(_TMP_DIR, "decoy.db")

_DRIFT_SCHEMA = '''
    CREATE TABLE drift_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL, metric_category TEXT NOT NULL,
        metric_name TEXT NOT NULL, metric_value REAL, baseline_mean REAL,
        baseline_std REAL, p_value REAL, z_score REAL, threshold REAL,
        alert INTEGER, baseline_window_days INTEGER,
        comparison_window_days INTEGER, notes TEXT
    )
'''

for _db in (_SCRATCH_DB, _DECOY_DB):
    _conn = sqlite3.connect(_db)
    _conn.execute(_DRIFT_SCHEMA)
    _conn.commit()
    _conn.close()


# DISCRIMINATING FIRST. Asserting "it wrote where I told it to" proves nothing
# if the default happens to be the same place. So: establish that the default is
# the PRODUCTION database and is NOT this test's scratch file, which is what
# makes passing db_path load-bearing rather than decorative. Same shape as the
# check Files 36, 37, 38, 40 and 45 each make against resolve_inference_db_path.
#
# resolve_drift_db_path RESOLVES AND RETURNS; it opens nothing, so asking this
# question is safe on a machine holding a database this test must not touch.
_PRODUCTION_DB = resolve_drift_db_path(None)
check("omitting db_path resolves to the production database",
      _PRODUCTION_DB.endswith("inferences.db"), True)
check("...which is NOT this test's scratch file, so passing db_path is doing "
      "real work", _PRODUCTION_DB == _SCRATCH_DB, False)

# The production row count BEFORE. Read only. Compared again at the end: a run
# that leaked a write into the real database fails here rather than being
# discovered months later as an unexplained drift row.
def _production_drift_rows():
    """Rows in the production drift_metrics table, or None if unreadable."""
    try:
        _c = sqlite3.connect(_PRODUCTION_DB)
        try:
            return _c.execute("SELECT COUNT(*) FROM drift_metrics").fetchone()[0]
        finally:
            _c.close()
    except sqlite3.Error:
        return None


_PRODUCTION_ROWS_BEFORE = _production_drift_rows()

_written = log_drift_metrics(
    {"data_availability": detect_data_availability(rows(ALL_AFTER))},
    BASELINE_WINDOW_DAYS, COMPARISON_WINDOW_DAYS,
    db_path=_SCRATCH_DB)

check("log_drift_metrics reports the database it actually wrote to",
      _written, _SCRATCH_DB)

# NEGATIVE CONTROL for that assertion, and it uses only scratch files -- the
# honest demonstration ("call it with db_path omitted and watch it hit
# production") is exactly the thing this check exists to prevent, so it is
# aimed at a SECOND throwaway database instead. The assertion above must be
# capable of coming out False, or it is not an assertion.
_written_decoy = log_drift_metrics(
    {"data_availability": detect_data_availability(rows(ALL_AFTER))},
    BASELINE_WINDOW_DAYS, COMPARISON_WINDOW_DAYS,
    db_path=_DECOY_DB)
check("the same assertion FAILS when the write goes elsewhere (negative control)",
      _written_decoy == _SCRATCH_DB, False)
check("...and the decoy write landed in the decoy, so the control is not "
      "passing because the call did nothing",
      _written_decoy, _DECOY_DB)

_conn = sqlite3.connect(_SCRATCH_DB)
_conn.row_factory = sqlite3.Row
_row = _conn.execute(
    "SELECT * FROM drift_metrics WHERE metric_name = 'ecog_unavailable_rate'"
).fetchone()
_conn.close()

check("a row was written", _row is not None, True)
if _row is not None:
    check("stored under the availability category",
          _row["metric_category"], "data_availability")
    check("stored metric_value", _row["metric_value"], 1.0)
    check("stored threshold", _row["threshold"], ECOG_UNAVAILABLE_RATE_THRESHOLD)
    check("stored alert", _row["alert"], 1)
    check("the diagnosis is stored, not just printed",
          "DATA_SNAPSHOT_DATE" in (_row["notes"] or ""), True)
    check("it is NOT recorded as a z-score", _row["z_score"], None)
    check("no baseline mean was invented", _row["baseline_mean"], None)

# NOTHING LEAKED. Two writes were made above and both were aimed at throwaway
# files; the production database must be exactly where it was. This is the check
# that would have caught the silent regression described at the top of this
# section, where the rebinding stopped working and every row went to the real
# database while the printed path said otherwise.
check("the production database was not written to",
      _production_drift_rows(), _PRODUCTION_ROWS_BEFORE)
check("...and that comparison is not vacuous -- the production table was "
      "readable, so the count is a real number rather than a None on both sides",
      _PRODUCTION_ROWS_BEFORE is not None, True)

shutil.rmtree(_TMP_DIR, ignore_errors=True)


# ===========================================================================
# 8. STRUCTURAL -- a threshold alert, not a baseline comparison
# ===========================================================================

print("\n" + "=" * 70)
print("8. STRUCTURAL -- no baseline, no z-score")
print("=" * 70)


# THE FOUR SOURCE READS BELOW WERE RETARGETED IN PASS 20c-3b.
#
# They pointed at "20- Drift Detection.py". Every definition they inspect now
# lives in oncotriage/monitoring/drift.py; File 20 is a re-export shim whose
# whole body is one `from ... import (...)`. Left as they were, all four would
# have failed loudly on the very next run -- _function_body raises
# AssertionError when the function is absent, and the two `next(...)` lookups
# raise StopIteration -- so nothing here would have gone SILENTLY green.
#
# THAT IS NOT TRUE OF EVERY CHECK THEY FEED, and the pass-20c-2c lesson is to
# ask which ones could. Answering it honestly:
#
#   * The three "does not call X" checks on `_src` are `in` tests asserted
#     False. They pass on an EMPTY string. _function_body raising covers a
#     MISSING function, but not a function whose body came back empty -- a
#     future edit to the extractor, or a definition that is all docstring, and
#     three checks agree with the code by construction. THESE ARE THE ONES THAT
#     COULD HAVE ROTTED, so `_src` is now asserted non-degenerate first.
#   * "reads the threshold from config" and "threshold is not a literal" are one
#     True and one False on the same string, so the pair cannot both pass
#     vacuously -- the True half fails on empty. Partially self-protecting, and
#     still covered by the non-degeneracy check below.
#   * The `_runner` and `_printer` checks are all asserted True, so an empty
#     body fails them. Never at risk; the non-degeneracy assertions are added
#     anyway so the three extractions are treated alike.
#   * The signature checks compare a list of argument names against an exact
#     expected list. An empty result is [] != ["df"], which fails. Never at
#     risk.
#
# The shim is checked separately at the end of this section: the names still
# have to reach the shared exec namespace, because that is how this file gets
# them.
_DRIFT_MODULE = "oncotriage/monitoring/drift.py"


def _function_body(filename: str, function_name: str) -> str:
    """Source of one top-level function's body, docstring excluded.

    The docstring is dropped because these checks grep for forbidden
    constructs, and this function's docstring explains at length why a z-score
    against baseline is wrong — which a naive grep reads as a z-score.
    """
    text = Path(_code_dir + filename).read_text(encoding="utf-8")
    for node in ast.parse(text).body:
        if not (isinstance(node, ast.FunctionDef) and node.name == function_name):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        return "\n".join(ast.get_source_segment(text, s) or "" for s in body)
    raise AssertionError(f"{function_name} not found in {filename}")


_src = _function_body(_DRIFT_MODULE, "ecog_unavailable_rate")

# NON-DEGENERACY, BEFORE THE THREE `in ... == False` CHECKS. Without this, an
# empty extraction passes all three by construction — which is the defect class
# this project has already shipped three times.
check("the extracted body is non-empty", len(_src) > 200, True)
check("...and is the body of the metric under test, not some other function",
      all(marker in _src for marker in
          ("denominator", "none_recorded", "ecog_selection")), True)

check("does not call z_score_drift", "z_score_drift" in _src, False)
check("does not call ks_test_drift", "ks_test_drift" in _src, False)
check("does not call calculate_psi", "calculate_psi" in _src, False)
check("reads the threshold from config",
      "ECOG_UNAVAILABLE_RATE_THRESHOLD" in _src, True)
check("threshold is not a literal in the function body", "0.20" in _src, False)

# Signature: one frame in, so there is no baseline to compare against even by
# accident.
_sig_text = Path(_code_dir + _DRIFT_MODULE).read_text(encoding="utf-8")
_sig_defs = [n for n in ast.parse(_sig_text).body if isinstance(n, ast.FunctionDef)]
check("the module parsed to a non-empty set of top-level functions",
      len(_sig_defs) >= 10, True)

_fn = next(n for n in _sig_defs if n.name == "ecog_unavailable_rate")
check("takes exactly one argument", [a.arg for a in _fn.args.args], ["df"])

_avail = next(n for n in _sig_defs if n.name == "detect_data_availability")
check("detect_data_availability takes only the current window",
      [a.arg for a in _avail.args.args], ["current_df"])

# PASS 20c-3b: every database entry point takes db_path. Asserted structurally
# as well as exercised above, because the exercise proves that ONE call honours
# it and this proves there is no second door.
for _name, _expected_tail in (("log_drift_metrics", "db_path"),
                              ("get_baseline_and_current_data", "db_path"),
                              ("run_drift_detection", "db_path")):
    _node = next(n for n in _sig_defs if n.name == _name)
    _args = [a.arg for a in _node.args.args]
    check(f"{_name} takes db_path", _args[-1], _expected_tail)

# ...and no function in the module reaches a bare `inferences_path` any more.
# That name is what File 41 used to rebind; a survivor would be a write this
# test cannot redirect.
#
# BY AST, NOT BY GREP, and the difference is not pedantry: the string
# "inferences_path" appears half a dozen times in that module's prose --
# including in resolve_drift_db_path's own docstring, which explains why it does
# NOT read the exec namespace -- and it appears in `paths.inferences_path`,
# which is an ATTRIBUTE on an imported module and is the correct way to reach it.
# What is forbidden is a BARE NAME LOAD, which is the only form a caller could
# have rebound. A substring check reads all three as the same thing and fails on
# the documentation of the fix.
_sig_tree = ast.parse(_sig_text)
_bare_inferences_path = [
    n.lineno for n in ast.walk(_sig_tree)
    if isinstance(n, ast.Name) and n.id == "inferences_path"
    and isinstance(n.ctx, ast.Load)
]
check("no bare `inferences_path` name load survives anywhere in the module",
      _bare_inferences_path, [])
# NON-DEGENERATE: the same walk must FIND the attribute form, or the detector is
# looking at nothing.
_attr_inferences_path = [
    n.lineno for n in ast.walk(_sig_tree)
    if isinstance(n, ast.Attribute) and n.attr == "inferences_path"
]
check("...and the detector does see the ATTRIBUTE form it is meant to allow, "
      "so the empty result above is a finding rather than a broken walk",
      len(_attr_inferences_path) >= 1, True)

# The config module is oncotriage/config.py as of item 20c; "03- Config.py" is
# a shim that re-exports it and carries no comment of its own, so a grep for
# the rationale has to look where the rationale is. Both greps target the same
# file so they cannot disagree about which file "the config module" means.
_CONFIG_TEXT = Path(_code_dir + "oncotriage/config.py").read_text(encoding="utf-8")
check("the threshold constant lives in the config module",
      "ECOG_UNAVAILABLE_RATE_THRESHOLD" in _CONFIG_TEXT, True)
check("and is marked as an uncalibrated holding value",
      "HOLDING VALUE, NOT CALIBRATED" in _CONFIG_TEXT, True)
check("and the shim re-exports it, so the exec chain still sees the name",
      "ECOG_UNAVAILABLE_RATE_THRESHOLD" in
      Path(_code_dir + "03- Config.py").read_text(encoding="utf-8"), True)

# The runner and the printer must both know about the new category, or the
# metric is computed and then never shown.
_runner = _function_body(_DRIFT_MODULE, "run_drift_detection")
check("the runner body is non-empty", len(_runner) > 200, True)
check("run_drift_detection computes it", "detect_data_availability" in _runner, True)
check("and counts its alerts into the total", "availability_alerts" in _runner, True)
_printer = _function_body(_DRIFT_MODULE, "print_drift_details")
check("the printer body is non-empty", len(_printer) > 200, True)
check("print_drift_details renders the category",
      '"data_availability"' in _printer, True)


# ===========================================================================
# 8b. THE SHIM STILL DELIVERS THE NAMES TO THE EXEC CHAIN
# ===========================================================================
#
# Everything above reads oncotriage/monitoring/drift.py, which is where the
# definitions are. But THIS FILE reaches them by exec-chaining
# "20- Drift Detection.py" and picking them out of the shared namespace, so a
# shim that stopped re-exporting one of them would break this test in a way none
# of the structural checks above could see -- they read the package and would
# keep passing.
#
# The two are asserted to be the same objects, not merely present, so a shim
# that shadowed a name with a definition of its own would fail here.

print("\n" + "=" * 70)
print("8b. the shim re-exports what this file reads out of the namespace")
print("=" * 70)

import oncotriage.monitoring.drift as _drift_pkg

for _exported in ("ecog_unavailable_rate", "detect_data_availability",
                  "log_drift_metrics", "print_drift_details",
                  "run_drift_detection", "z_score_drift", "ks_test_drift",
                  "calculate_psi", "resolve_drift_db_path"):
    check(f"{_exported} in the exec namespace is the package's own object",
          globals().get(_exported) is getattr(_drift_pkg, _exported), True)

check("SCIPY_AVAILABLE reached the namespace too",
      SCIPY_AVAILABLE is _drift_pkg.SCIPY_AVAILABLE, True)

# The scipy guard is a real ImportError guard now, not a NameError guard on
# somebody else's namespace. Asserted from the AST, because the flag being True
# on this machine says nothing about how it was arrived at -- and because the
# module's docstring QUOTES the old NameError guard verbatim in order to explain
# what it replaced, so a substring check reports the documentation of the fix as
# the defect. Same lesson as the inferences_path check above.
_scipy_guards = [
    n for n in ast.walk(_sig_tree)
    if isinstance(n, ast.Try)
    and any(isinstance(s, ast.ImportFrom) and (s.module or "").startswith("scipy")
            for s in n.body)
]
check("the scipy import is inside exactly one try", len(_scipy_guards), 1)
if _scipy_guards:
    _handlers = [
        h.type.id for h in _scipy_guards[0].handlers
        if isinstance(h.type, ast.Name)
    ]
    check("...and it catches ImportError", "ImportError" in _handlers, True)
    check("...and NOT NameError, which was a guard on somebody else's namespace",
          "NameError" in _handlers, False)
    check("...and it guards a real import of ks_2samp",
          any(isinstance(s, ast.ImportFrom)
              and any(a.name == "ks_2samp" for a in s.names)
              for s in _scipy_guards[0].body), True)


# ===========================================================================
# SUMMARY
# ===========================================================================

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(textwrap.indent(f"  - {_f}", ""))

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug  3 2026

@author: ramyalsaffar
"""
