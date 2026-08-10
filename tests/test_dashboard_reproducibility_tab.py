# Dashboard Reproducibility Tab Render Test
###########################################

"""
``render_reproducibility_tab`` is the largest render function in the dashboard.
Pass 20f-4 split it -- four literal tables hoisted to module scope and nineteen
pure helpers extracted -- and proved the split correct ONCE, with a harness that
rendered the pre-split module out of ``git show`` beside the shipped one and
compared every element. That harness was never committed. This file is that
comparison made permanent, and it is not a file move: three things had to change
for it to be a standing test rather than a one-off.

WHAT CHANGED, AND WHY EACH CHANGE WAS FORCED
--------------------------------------------
1.  THERE IS NO "BEFORE" ANY MORE. The old harness compared against a commit,
    and a commit recedes: ``e7c9742^`` is reachable today and is not a thing a
    test may depend on for the life of the repository -- a shallow clone, a
    squash or an export drops it, and the test then fails for a reason that is
    not a defect. The reference is a GOLDEN SNAPSHOT committed beside this file
    (``snapshots/dashboard_reproducibility_tab.json``), plain JSON, readable in
    a diff, regenerated only on purpose:

        python tests/test_dashboard_reproducibility_tab.py --update-snapshot

    The snapshot was established against the pre-split source ONCE, in the
    session that wrote this file: the four literal tables were lifted from
    ``git show e7c9742^:oncotriage/dashboard/tabs/reproducibility.py`` by AST,
    evaluated with ``ast.literal_eval`` and compared to the module constants by
    VALUE and by KEY ORDER. All four were identical on both. Nothing here reads
    git; that comparison is what makes the committed snapshot the reference
    rather than a photograph of whatever the module happened to do.

    WHICH STREAMLIT THE SNAPSHOT WAS RECORDED ON, AND WHY IT MOVED ONCE.
    Recorded on **streamlit 1.46.0**, which is what ``pyproject.toml`` pins.
    It was originally recorded on **1.45.1** -- the version that happened to be
    installed on the development machine, which the pin had already moved past
    -- so the committed snapshot disagreed with the project's own declared
    dependency and 8 checks failed for anyone who installed from
    ``pyproject.toml``: CI, the Docker image, a fresh ``pip install -e .``.

    IT WAS REGENERATED ONLY AFTER THE FULL DIFF WAS SHOWN TO BE A RENAME.
    Every difference between the 1.45.1 and 1.46.0 snapshots was enumerated --
    46 of them, all in ``element_order``, and all one substitution:

        2:horizontal -> 2:flex_container   (36x)
        1:vertical   -> 1:flex_container   ( 7x)
        3:horizontal -> 3:flex_container   ( 3x)

    streamlit 1.46.0 collapsed its ``vertical`` and ``horizontal`` layout
    containers into a single ``flex_container`` element type. ZERO differences
    in metrics, markdown, captions, subheaders, dataframes, selectboxes, plotly
    specs, the pooled templates or the hoisted literals -- so nothing this tab
    computes or displays changed, only what streamlit calls the boxes it draws
    them in.

    THE RULE THAT REGENERATION HAS TO PASS, because a golden file refreshed to
    accommodate a change makes whatever the code does correct by definition:
    regenerate only when every difference is a streamlit-internal structural
    rename, never when a label, a value, a count or a figure spec moves. Two
    later versions were tested against that rule and BOTH FAILED it, which is
    why the pin stays at 1.46.0:

        1.51.0   96 differences -- including 43 True -> False and four element
                 heights collapsing to 0
        1.61.1   element counts change (165->164, 7->6), types reorder
                 (4:plotly_chart -> 2:markdown), and st.info loses its emoji
                 prefix in the rendered text

    Note for whoever bumps streamlit next: 1.51.0 is also the first release
    that lifts streamlit's ``pillow<12`` cap, so the pillow advisories and this
    snapshot are the same decision. See pyproject.toml's pillow note.

2.  IT MUST NOT TOUCH THE PRODUCTION DATABASE. The old harness rendered from the
    real ``inferences.db``. This one seeds a scratch SQLite file in a temporary
    directory -- BOTH tables, because the tab is rendered from the join of
    ``inferences`` and ``trial_matches`` -- using the project's own
    ``initialize_database()`` so the schema is the real one by construction
    rather than a retyped copy that can drift.

    ``oncotriage/dashboard/data.py`` reads ``paths.inferences_path``, which does
    NOT honour ``ONCOTRIAGE_INFERENCES_DB`` (that variable reaches the two
    WRITERS, ``resolve_inference_db_path`` and ``resolve_drift_db_path``). So
    the scratch path is installed into ``paths._RESOLVED``, the documented cache
    -- the same seam ``tests/test_ablation_db_isolation.py`` uses to install a
    decoy -- and restored afterwards.

    Isolation is asserted BEHAVIOURALLY, not by inspecting a variable:
    ``sqlite3.connect`` is recorded for the duration of every render, and the
    set of paths opened must be exactly the scratch path. Section 1c is the
    control -- the same assertion run against a DECOY database shows it FAILING,
    so a passing render is evidence rather than a tautology. The decoy is used
    for the reason File 41 used one: a demonstration that proved the point by
    reading the production database would be the defect it is testing for.

3.  IT DRIVES THE TAB, NOT THE APP. ``AppTest.from_string`` runs a four-line
    driver that imports one module and calls one function with a frame this file
    supplies. Rendering through ``21- Streamlit Dashboard.py`` would pull in the
    sidebar, the three cached loaders and all nine tabs, and a failure would not
    say which tab produced it.

WHAT IS COMPARED
----------------
Element for element, in document order, per scenario: the type of every element
and block (so a layout change is caught, not only a content change), metrics
with label / value / delta / direction / help, markdown, captions, subheaders,
text, success and info bodies, expander labels, dataframes as complete CSV text
plus their column config and height, selectboxes with their KEY, label, option
list and value, and plotly figures as their complete JSON spec.

THE PLOTLY TEMPLATE POOL, AND WHAT IT IS AND IS NOT BLIND TO
------------------------------------------------------------
``layout.template`` is ~7 KB of identical boilerplate on every figure, so it is
replaced in each spec by a 16-hex-character reference and the templates are
stored once in a pool at the top of the snapshot. It is the difference between
a snapshot a person can read a diff of and one they cannot.

That is the one part of the file with custom machinery between capture and
comparison, and the first version of it was the one part with no planted defect
behind it -- 5a moves a marker colour and 5h a figure height, and BOTH live
outside the template, so "a changed template is still compared" rested on
reading ``_pack_plotly_spec`` rather than on breaking it. 5m and 5n are that
plant: one integer changed ~7 KB deep inside ONE of the six figures' templates,
and one figure switched to a different named template.

Two facts about the pool, stated from the code that builds it:

    A figure whose template changes gets a NEW pool entry. The ref is the
    digest of the template, so a changed template is a changed ref, the spec
    that carries it differs, and section 2b reports it. Nothing is silently
    reused, and 5m measures exactly that: one of six refs moves, five do not,
    and the original entry stays in the pool beside the new one.

    Two DIFFERENT templates cannot share an entry -- now. ``_template_blob``
    serializes with ``sort_keys=FALSE``, deliberately. The first version sorted,
    which makes the ref blind to key order: two templates differing only in the
    order of their keys hashed to one ref, the second silently overwrote the
    first, and no ``__template_ref__`` and no pool digest moved. That was a
    distinct JSON document colliding onto one entry by construction. Keying on
    the exact bytes the snapshot stores removes the case, and what remains -- a
    real 64-bit sha256 collision -- is RECORDED by the guard in
    ``_pack_plotly_spec`` and read by section 2c rather than absorbed.

Changing the key changed every ref, so the snapshot was regenerated. A golden
file regenerated to accommodate a fix makes whatever the code does correct by
definition, so before the new one was committed both files were decoded back to
the thing they are a compressed record OF -- the full element capture, every
pooled template spliced back inline -- and compared directly. 20 figures inlined
on each side, 973 items compared across all seven scenarios, IDENTICAL, with a
control (one integer changed inside an inlined template) shown to make that
comparison fail. The new snapshot is byte-identical across three regenerations.

SIX SCENARIOS, FIVE OF THEM UNREACHABLE FROM ORDINARY DATA
-----------------------------------------------------------
``full`` renders everything: two collections (so the corpus filter renders),
three flip types, a score-drift table, both deep dives, the 'Other' failure-mode
expander and the patient-record expander. The other five exist because the
ordinary path cannot reach them:

    no_collection_column  the pre-flight return, before any database read
    no_repeats            the two-metric branch -- seeded as one patient with two
                          inferences on DIFFERENT collections, so
                          `patients_with_multi` is 1 rather than 0 and the branch
                          is reached with a non-degenerate number in it
    no_overlap            two inferences whose trial sets are disjoint
    perfectly_stable      no flips at all -> the first st.success
    flips_no_drift        flips but identical eligible scores -> the second
                          st.success, which is nested INSIDE `if flip_count > 0`
                          and therefore cannot be reached by the scenario above.
                          One render cannot satisfy both, which is why there are
                          six scenarios for five named branches.
    empty_hash_column     every patient_data_hash is '' -> `_build_patient_groups`
                          falls back to the (patient, collection) key, and the
                          whole deep dive still renders

THE LITERAL CHECK IS SEPARATE FROM THE RENDER COMPARISON AND IS NOT OPTIONAL
----------------------------------------------------------------------------
Pass 20f-4 hand-transcribed ``_FLIP_TYPE_COLORS`` and typed ``#2ecc71`` where
the original had ``#2ca02c``. The element-for-element comparison PASSED, because
that flip type never occurs in the data.

That is not bad luck, and this file measured why: ``_with_flip_types`` is applied
only to ``flipped_comps``, which by construction holds two or more distinct
classifications, so ``'Rejected'`` is always in ``tiers_seen`` and
``_classify_flip_type`` can never return ``'Full Match <-> Partial Match'`` or
``'Other'``. Two of the five entries in that table are UNREACHABLE BY ANY DATA.
A render comparison is exactly as complete as the data that exercises it. So
section 4 compares every hoisted literal against the snapshot by value AND key
order -- key order is load-bearing, the failure-mode bar chart and the
recommended-fix table both iterate ``_FAILURE_CATEGORIES.keys()`` -- and section
5b plants into one of the two unreachable entries and requires the render
comparison to see NOTHING while the literal check fires.

``_STATUS_DISPLAY_BASE_FLIP['violated']`` is unreachable for the same shape of
reason: a run that violates an exclusion criterion is rejected, and a rejected
run stores no criterion_details. It is covered by section 4 and by nothing else.

WHAT THIS FILE DOES NOT DO
--------------------------
It does not inventory decorators. ``tests/test_package_invariants.py`` section
2i already compares them as an exact dict keyed by ``path::qualified_name``, and
a second copy is a second thing to keep in step.

NO NETWORK, MEASURED RATHER THAN CLAIMED
----------------------------------------
"No network" used to be a sentence in this docstring with nothing behind it.
Every render now runs with ``socket.socket.connect``, ``socket.socket.connect_ex``,
``socket.create_connection`` and ``socket.getaddrinfo`` replaced by a recorder
that RAISES -- so a render that reached out fails at the call site and names the
frame it came from, rather than succeeding quietly against a live endpoint.
Section 5o reads what those recorders saw for all seven scenarios, and its
CONTROL arms the identical guard and makes a real outbound call, which must be
blocked and recorded: without it the seven readings are seven looks at a list
nothing could ever have appended to.

That covers the renders. The IMPORTS were measured separately and out of band,
by running this whole file under ``sandbox-exec`` with ``(deny network*)`` --
genuine OS-level unavailability, not a patch this process could undo. Result:
exit 0, every check passing, and no resolver or connection error anywhere in the
output. Reported rather than committed, because a test that requires a macOS
sandbox profile to run is a test that stops running everywhere else.

NO KEYS, NO MODEL, NO SPEND, NO ABSOLUTE PATH, and nothing in the repository is
written: every plant is applied to a COPY of the module source written into a
temporary directory, and section 6 hashes the shipped file before and after to
say so.

WHICH TABLE IS READ BY WHOM
---------------------------
The tab takes ONE argument -- the ``inferences`` frame -- and reads
``trial_matches`` ITSELF, through ``load_trial_matches_data()`` on its fourth
statement. That is the read the decoy control in 5l fires on, and it is why six
of the seven scenarios open the database exactly once while
``no_collection_column``, which returns above that line, opens nothing.

``oncotriage/dashboard/app.py`` does not hand it the raw frame: it calls
``enrich_match_tiers(filtered_df, trial_matches)`` first, so production adds
four columns this file's frame does not have. That is equivalent only for as
long as the tab reads none of them, so section 1c asserts it against the shipped
source in both forms a column can be read by -- ``df['x']`` is a string literal,
``df.x`` is an attribute -- each with its own non-degeneracy probe, because the
first version used three probes that were all string literals and would have
passed with the attribute half of the walk deleted.

WHY IT IS NOT IN tests/run_serial_tests.py, derived rather than assumed: it
writes only inside a temporary directory, it patches no file in the repository,
and the only repository file it READS is
``oncotriage/dashboard/tabs/reproducibility.py`` -- which is written by neither
of the suite's two writers (``oncotriage/registries/cancer_code_registry.py``
and ``oncotriage/config.py``). It imports the package, which is the ordinary
hazard every reader shares and is not a collision this matrix is for.

Run from terminal:
    python tests/test_dashboard_reproducibility_tab.py
    python tests/test_dashboard_reproducibility_tab.py --update-snapshot

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# The package bootstrap every file in tests/ carries; the candidate directory
# is the PARENT of this file's. `pip install -e .` makes it a no-op.
import os
import sys

try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
         if "__file__" in globals() else None, "__file__"),
        (os.getcwd(), "cwd"),
    ):
        if _candidate and os.path.isdir(os.path.join(_candidate, "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise
    del _candidate, _how

import ast
import hashlib
import importlib
import json
import pickle
import shutil
import socket
import sqlite3
import tempfile
import time
import traceback
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from oncotriage import paths as _paths
from oncotriage.dashboard import data as _dashboard_data
from oncotriage.dashboard.tabs import reproducibility as _repro
from oncotriage.storage.database_logger import initialize_database


#------------------------------------------------------------------------------


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
        _FAILURES.append(f"{label}\n          expected: {expected}\n          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest_file(path) -> str:
    """sha256 of a file, or the string 'absent'."""
    if not os.path.exists(path):
        return "absent"
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# The module under test, and its source. Read once; section 6 re-reads it.
_REPRO_FILE = os.path.abspath(_repro.__file__)
_REPRO_SOURCE = Path(_REPRO_FILE).read_text(encoding="utf-8")
_REPRO_DIGEST_BEFORE = _digest_file(_REPRO_FILE)

_SNAPSHOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "snapshots",
                              "dashboard_reproducibility_tab.json")

_UPDATE_SNAPSHOT = "--update-snapshot" in sys.argv

_TMP = tempfile.mkdtemp(prefix="oncotriage-repro-tab-")
_SCRATCH_DB = os.path.join(_TMP, "scratch_inferences.db")
_DECOY_DB = os.path.join(_TMP, "decoy_inferences.db")


# ===========================================================================
# THE SEEDED CORPUS
# ===========================================================================
#
# Every row is written here, deterministically, with explicit primary keys --
# the run-summary table renders `Inference ID`, so an autoincrement value would
# put a database detail into the snapshot.
#
# Scenarios are partitioned by patient-id prefix inside ONE database: the tab
# selects trial_matches by inference_id, so disjoint inference ids make the
# scenarios independent without six files and six cache fills.

_CRIT_MET = "Histologically confirmed invasive carcinoma"
_CRIT_ECOG = "ECOG performance status 0-1"
_CRIT_RENAL = "Adequate renal function"
_CRIT_RECIST = "Measurable disease per RECIST 1.1"
_CRIT_ANTHRA = "Prior anthracycline therapy"
_CRIT_PREG = "Pregnancy or lactation"


def _criteria(inclusion, exclusion):
    return json.dumps({"inclusion": inclusion, "exclusion": exclusion})


# Run A of the default-selected flip. Covers met / not_evaluable-with-a-value
# (-> Unverifiable) / not_evaluable-without-one (-> Missing Data) / not_violated
# / a patient value beginning "Not applicable" (-> Not Applicable).
_T1_RUN_A = _criteria(
    [
        {"criterion": _CRIT_MET, "status": "met",
         "patient_value": "Invasive ductal carcinoma of the breast"},
        {"criterion": _CRIT_ECOG, "status": "met", "patient_value": "ECOG 1"},
        {"criterion": _CRIT_RENAL, "status": "not_evaluable",
         "patient_value": "Not in patient record"},
        {"criterion": _CRIT_RECIST, "status": "not_evaluable",
         "patient_value": "Imaging narrative is unstructured"},
    ],
    [
        {"criterion": _CRIT_ANTHRA, "status": "not_violated",
         "patient_value": "No prior anthracycline exposure"},
        {"criterion": _CRIT_PREG, "status": "not_violated",
         "patient_value": "Not applicable - male patient"},
    ],
)

# Run C of the same flip. Three deliberate differences from run A:
#   - _CRIT_ECOG flips met -> not_met                     (the "Changed" mark)
#   - _CRIT_RECIST is absent                              (the "—" missing cell)
#   - _CRIT_MET is spelled in a DIFFERENT CASE, so that dropping .lower() from
#     _normalize_criterion splits one row into two. Pass 20f-4 planted exactly
#     that and measured it as a no-op, because the criteria in the flip it
#     selected already agreed in case. Section 5f is the same plant made real.
_T1_RUN_C = _criteria(
    [
        {"criterion": _CRIT_MET.upper(), "status": "met",
         "patient_value": "Invasive ductal carcinoma of the breast"},
        {"criterion": _CRIT_ECOG, "status": "not_met", "patient_value": "ECOG 2"},
        {"criterion": _CRIT_RENAL, "status": "not_evaluable",
         "patient_value": "Not in patient record"},
    ],
    [
        {"criterion": _CRIT_ANTHRA, "status": "not_violated",
         "patient_value": "No prior anthracycline exposure"},
        {"criterion": _CRIT_PREG, "status": "not_violated",
         "patient_value": "Not applicable - male patient"},
    ],
)

_DRIFT_RUN_1 = _criteria(
    [
        {"criterion": _CRIT_MET, "status": "met", "patient_value": "Adenocarcinoma"},
        {"criterion": _CRIT_ECOG, "status": "met", "patient_value": "ECOG 0"},
    ],
    [{"criterion": _CRIT_ANTHRA, "status": "not_violated", "patient_value": "None"}],
)
_DRIFT_RUN_2 = _criteria(
    [
        {"criterion": _CRIT_MET, "status": "met", "patient_value": "Adenocarcinoma"},
        {"criterion": _CRIT_ECOG, "status": "not_evaluable",
         "patient_value": "Not in patient record"},
    ],
    [{"criterion": _CRIT_ANTHRA, "status": "not_violated", "patient_value": "None"}],
)
_DRIFT_RUN_3 = _criteria(
    [
        {"criterion": _CRIT_MET, "status": "met", "patient_value": "Adenocarcinoma"},
        {"criterion": _CRIT_ECOG, "status": "met", "patient_value": "ECOG 1"},
    ],
    [{"criterion": _CRIT_ANTHRA, "status": "not_violated", "patient_value": "None"}],
)

_PROMPT = (
    "[SYSTEM]\nYou are an oncology trial matcher.\n"
    "PATIENT RECORD:\nAge 61, female. Primary: Malignant neoplasm of breast.\n"
    "ECOG 1. Medications: 4 active, 2 historical.\n"
    "CLINICAL TRIALS:\n1. NCT-T1 ...\n"
)

_COLL_A = "trial_criteria_20260101_000000"
_COLL_B = "trial_criteria_20260202_000000"


def _inf(inf_id, patient_id, collection, phash, condition="Malignant neoplasm of breast",
         age=61, sex="female", cond_count=7, med_count=4, prompt=_PROMPT):
    """One inferences row, as the column tuple the seeder inserts."""
    return (inf_id, patient_id, "2026-03-01 09:00:00", age, sex, condition,
            cond_count, med_count, collection, phash, prompt)


def _tm(inf_id, nct, eligible, score, explanation="", details="",
        phase="Phase 2", title=None):
    """One trial_matches row."""
    return (inf_id, nct, title or f"A study of therapy in {nct}", phase,
            score, eligible, explanation, details)


# --- explanations, chosen to hit several _FAILURE_CATEGORIES and one none ----
_EXP_TEMPORAL = ("Condition was resolved years ago; the protocol requires active "
                 "disease at enrollment.")
_EXP_MISSING = ("The record lacks evidence of HER2 status, so receptor eligibility "
                "is not confirmed.")
_EXP_LAB = "Serum creatinine exceeds the age limit threshold defined by the protocol."
_EXP_STAGE = "The trial requires metastatic disease and this patient is not metastatic."
_EXP_OTHER = "Enrollment at the coordinating site closed before this evaluation."


def _seed_rows():
    """(inferences rows, trial_matches rows) for every scenario, in one corpus."""
    inf_rows = []
    tm_rows = []

    # ---------------- full: two collections, every section renders ----------
    # FULL-P1 on collection A, three inferences, same hash.
    for i, inf_id in enumerate((101, 102, 103), start=1):
        inf_rows.append(_inf(inf_id, "FULL-P1", _COLL_A, "hash-full-p1"))
    # NCT-T1: Rejection <-> Full Match, and the flip the deep dive selects.
    tm_rows += [
        _tm(101, "NCT-T1", "eligible", 1.0, "All criteria confirmed.", _T1_RUN_A),
        _tm(102, "NCT-T1", "not_eligible", 0.0, _EXP_TEMPORAL, ""),
        _tm(103, "NCT-T1", "eligible", 1.0, "All criteria confirmed.", _T1_RUN_C),
    ]
    # NCT-T2: Rejection <-> Partial Match.
    tm_rows += [
        _tm(101, "NCT-T2", "eligible", 0.60, "Most criteria confirmed.", _DRIFT_RUN_1),
        _tm(102, "NCT-T2", "not_eligible", 0.0, _EXP_MISSING, ""),
        _tm(103, "NCT-T2", "eligible", 0.80, "Most criteria confirmed.", _DRIFT_RUN_3),
    ]
    # NCT-T3: Rejection <-> Zero Score (eligible with a 0.0 score).
    tm_rows += [
        _tm(101, "NCT-T3", "eligible", 0.0, "No criteria could be confirmed.", _DRIFT_RUN_2),
        _tm(102, "NCT-T3", "not_eligible", 0.0, _EXP_LAB, ""),
        _tm(103, "NCT-T3", "eligible", 0.0, "No criteria could be confirmed.", _DRIFT_RUN_2),
    ]
    # NCT-T4: eligible in all three, scores differ -> the score-drift table and
    # the drift deep dive.
    tm_rows += [
        _tm(101, "NCT-T4", "eligible", 0.50, "Confirmed.", _DRIFT_RUN_1, phase="Phase 3"),
        _tm(102, "NCT-T4", "eligible", 0.60, "Confirmed.", _DRIFT_RUN_2, phase="Phase 3"),
        _tm(103, "NCT-T4", "eligible", 0.70, "Confirmed.", _DRIFT_RUN_3, phase="Phase 3"),
    ]
    # NCT-T5: eligible in all, identical.  NCT-T6: not eligible in all.
    for inf_id in (101, 102, 103):
        tm_rows.append(_tm(inf_id, "NCT-T5", "eligible", 0.90, "Confirmed.", _DRIFT_RUN_1))
        tm_rows.append(_tm(inf_id, "NCT-T6", "not_eligible", 0.0, _EXP_STAGE, ""))
    # A trial evaluated in ONE inference only: must be skipped, not reported as
    # perfect agreement.
    tm_rows.append(_tm(101, "NCT-T7", "eligible", 0.40, "Confirmed.", _DRIFT_RUN_1))

    # FULL-P2 on collection A, two inferences.
    for inf_id in (104, 105):
        inf_rows.append(_inf(inf_id, "FULL-P2", _COLL_A, "hash-full-p2",
                             condition="Malignant neoplasm of colon", age=55, sex="male"))
    tm_rows += [
        _tm(104, "NCT-T1", "eligible", 0.75, "Confirmed.", _DRIFT_RUN_1),
        _tm(105, "NCT-T1", "not_eligible", 0.0, _EXP_STAGE, ""),
        _tm(104, "NCT-T8", "eligible", 0.55, "Confirmed.", _DRIFT_RUN_1, phase="Phase 1"),
        _tm(105, "NCT-T8", "eligible", 0.65, "Confirmed.", _DRIFT_RUN_3, phase="Phase 1"),
        _tm(104, "NCT-T9", "not_eligible", 0.0, _EXP_MISSING, ""),
        _tm(105, "NCT-T9", "not_eligible", 0.0, _EXP_MISSING, ""),
    ]

    # FULL-P3 on collection B: a second collection, so the corpus filter renders.
    for inf_id in (106, 107):
        inf_rows.append(_inf(inf_id, "FULL-P3", _COLL_B, "hash-full-p3",
                             condition="Malignant neoplasm of lung", age=70, sex="male"))
    tm_rows += [
        # An 'Other' failure mode: no keyword in any category matches.
        _tm(106, "NCT-T10", "eligible", 1.0, "Confirmed.", _DRIFT_RUN_1, phase=None),
        _tm(107, "NCT-T10", "not_eligible", 0.0, _EXP_OTHER, "", phase=None),
        _tm(106, "NCT-T11", "eligible", 0.30, "Confirmed.", _DRIFT_RUN_1),
        _tm(107, "NCT-T11", "eligible", 0.30, "Confirmed.", _DRIFT_RUN_1),
    ]

    # ---------------- no_repeats -------------------------------------------
    # One patient, two inferences, DIFFERENT collections. `grouped` is empty
    # (nothing has 2+ on ONE collection) but `patients_with_multi` is 1, so the
    # branch is exercised with a non-degenerate number rather than a pair of
    # zeroes that would also be produced by a broken query.
    inf_rows.append(_inf(201, "NR-P1", _COLL_A, "hash-nr-p1"))
    inf_rows.append(_inf(202, "NR-P1", _COLL_B, "hash-nr-p1"))
    inf_rows.append(_inf(203, "NR-P2", _COLL_A, "hash-nr-p2"))
    for inf_id in (201, 202, 203):
        tm_rows.append(_tm(inf_id, "NCT-T1", "eligible", 0.5, "Confirmed.", _DRIFT_RUN_1))

    # ---------------- no_overlap -------------------------------------------
    # Two inferences on one collection with disjoint trial sets.
    for inf_id in (301, 302):
        inf_rows.append(_inf(inf_id, "NOV-P1", _COLL_A, "hash-nov-p1"))
    tm_rows += [
        _tm(301, "NCT-X1", "eligible", 0.5, "Confirmed.", _DRIFT_RUN_1),
        _tm(302, "NCT-X2", "eligible", 0.5, "Confirmed.", _DRIFT_RUN_1),
    ]

    # ---------------- perfectly_stable -------------------------------------
    # No flips at all -> the first st.success. Scores identical too, so the
    # score-drift block (nested inside `if flip_count > 0`) is not reached.
    for inf_id in (401, 402):
        inf_rows.append(_inf(inf_id, "PS-P1", _COLL_A, "hash-ps-p1"))
    for inf_id in (401, 402):
        tm_rows.append(_tm(inf_id, "NCT-Y1", "eligible", 0.80, "Confirmed.", _DRIFT_RUN_1))
        tm_rows.append(_tm(inf_id, "NCT-Y2", "not_eligible", 0.0, _EXP_LAB, ""))

    # ---------------- flips_no_drift ---------------------------------------
    # A flip (so the deep dive runs) with every eligible-in-all trial scoring
    # identically -> the SECOND st.success, which perfectly_stable cannot reach.
    for inf_id in (501, 502):
        inf_rows.append(_inf(inf_id, "FND-P1", _COLL_A, "hash-fnd-p1"))
    tm_rows += [
        _tm(501, "NCT-Z1", "eligible", 1.0, "Confirmed.", _T1_RUN_A),
        _tm(502, "NCT-Z1", "not_eligible", 0.0, _EXP_TEMPORAL, ""),
        _tm(501, "NCT-Z2", "eligible", 0.70, "Confirmed.", _DRIFT_RUN_1),
        _tm(502, "NCT-Z2", "eligible", 0.70, "Confirmed.", _DRIFT_RUN_1),
    ]

    # ---------------- empty_hash_column ------------------------------------
    # Every hash is '', so both the render function and _build_patient_groups
    # fall back to the (patient, collection) key. Seeded with a flip AND a
    # score drift so the whole deep dive renders on the fallback key.
    for inf_id in (601, 602, 603):
        inf_rows.append(_inf(inf_id, "EHC-P1", _COLL_A, ""))
    tm_rows += [
        _tm(601, "NCT-W1", "eligible", 1.0, "Confirmed.", _T1_RUN_A),
        _tm(602, "NCT-W1", "not_eligible", 0.0, _EXP_TEMPORAL, ""),
        _tm(603, "NCT-W1", "eligible", 1.0, "Confirmed.", _T1_RUN_C),
        _tm(601, "NCT-W2", "eligible", 0.40, "Confirmed.", _DRIFT_RUN_1),
        _tm(602, "NCT-W2", "eligible", 0.50, "Confirmed.", _DRIFT_RUN_2),
        _tm(603, "NCT-W2", "eligible", 0.60, "Confirmed.", _DRIFT_RUN_3),
    ]

    return inf_rows, tm_rows


_INF_COLUMNS = ("id", "patient_id", "timestamp", "age", "sex", "primary_condition",
                "condition_count", "medication_count", "qdrant_collection",
                "patient_data_hash", "llm_classifier_prompt")
_TM_COLUMNS = ("inference_id", "nct_id", "trial_title", "trial_phase",
               "match_score", "eligible", "assessment", "criterion_details")


def _build_database(db_path, inf_rows, tm_rows):
    """Create the real schema at `db_path` and fill it. No production file."""
    initialize_database(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            f"INSERT INTO inferences ({','.join(_INF_COLUMNS)}) "
            f"VALUES ({','.join('?' * len(_INF_COLUMNS))})", inf_rows)
        conn.executemany(
            f"INSERT INTO trial_matches ({','.join(_TM_COLUMNS)}) "
            f"VALUES ({','.join('?' * len(_TM_COLUMNS))})", tm_rows)
        conn.commit()
    finally:
        conn.close()


# Which patient-id prefix belongs to which scenario, and what the scenario does
# to the frame before handing it over.
_SCENARIOS = (
    ("full", "FULL-", None),
    ("no_collection_column", "FULL-", "drop_collection"),
    ("no_repeats", "NR-", None),
    ("no_overlap", "NOV-", None),
    ("perfectly_stable", "PS-", None),
    ("flips_no_drift", "FND-", None),
    ("empty_hash_column", "EHC-", None),
)


def _frame_for(scenario_df_source, prefix, mutation):
    df = scenario_df_source[
        scenario_df_source["patient_id"].str.startswith(prefix)].copy()
    if mutation == "drop_collection":
        df = df.drop(columns=["qdrant_collection"])
    return df.reset_index(drop=True)


# ===========================================================================
# THE CAPTURE
# ===========================================================================
#
# One dict per scenario, everything in document order. The plotly template pool
# is filled as a side effect of packing specs and written once at the top of the
# snapshot, so a 3 KB block of identical boilerplate does not appear nine times
# in a file whose whole purpose is being read as a diff.

_TEMPLATE_POOL = {}
_TEMPLATE_POOL_BLOBS = {}
_TEMPLATE_POOL_COLLISIONS = []


def _template_blob(template):
    """The exact serialization the pool is keyed on.

    ``sort_keys=False``, DELIBERATELY, and it is the one thing about this pool
    that is not obvious. The first version keyed on the SORTED serialization,
    which makes the ref blind to key order -- so two templates differing only in
    the order of their keys hash to one ref, the second silently overwrites the
    first in the pool, the ``__template_ref__`` in every spec stays put, and
    NOTHING in this file reports it. That is a distinct JSON document colliding
    onto one entry by construction rather than by luck, inside the one part of
    the snapshot with custom machinery between capture and comparison.

    Keying on the exact bytes the snapshot stores removes the case: two
    templates share a ref only through a real 64-bit sha256 collision, and the
    guard in ``_pack_plotly_spec`` RECORDS that rather than absorbing it.
    """
    return json.dumps(template, sort_keys=False, separators=(",", ":"))


def _pack_plotly_spec(spec_text):
    """The complete figure spec, with layout.template hoisted into the pool."""
    spec = json.loads(spec_text)
    template = spec.get("layout", {}).pop("template", None)
    if template is not None:
        blob = _template_blob(template)
        ref = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
        if _TEMPLATE_POOL_BLOBS.get(ref, blob) != blob:
            # Two different templates under one ref. Not raised -- this runs
            # inside the capture, where a raise would be reported as a render
            # failure and name the wrong thing. Section 2c reads the list.
            _TEMPLATE_POOL_COLLISIONS.append(ref)
        _TEMPLATE_POOL_BLOBS[ref] = blob
        _TEMPLATE_POOL[ref] = template
        spec["layout"]["__template_ref__"] = ref
    return spec


def _children(node):
    kids = getattr(node, "children", None)
    if kids is None:
        return []
    if isinstance(kids, dict):
        return [kids[k] for k in sorted(kids)]
    return list(kids)


def _walk(node, depth, out):
    """Every element and block, in document order, as 'depth:type'."""
    node_type = getattr(node, "type", node.__class__.__name__)
    out.append(f"{depth}:{node_type}")
    for child in _children(node):
        _walk(child, depth + 1, out)


def _proto_field(proto, name, default=None):
    return getattr(proto, name, default)


def _capture_tree(at):
    """Everything this file compares, from one rendered AppTest."""
    order = []
    _walk(at.main, 0, order)

    metrics = []
    for m in at.metric:
        p = m.proto
        metrics.append({
            "label": p.label,
            "value": p.body,
            "delta": p.delta,
            "direction": int(p.direction),
            "color": int(_proto_field(p, "color", 0)),
            "help": p.help,
        })

    dataframes = []
    for d in at.dataframe:
        value = d.value
        dataframes.append({
            "csv": value.to_csv(index=True) if isinstance(value, pd.DataFrame) else str(value),
            "columns_config": _proto_field(d.proto, "columns", ""),
            "height": int(_proto_field(d.proto, "height", 0)),
            "use_container_width": bool(_proto_field(d.proto, "use_container_width", False)),
        })

    selectboxes = []
    for s in at.selectbox:
        selectboxes.append({
            "key": s.key,
            "label": s.label,
            "options": list(s.options),
            "value": s.value,
        })

    figures = []
    for f in at.get("plotly_chart"):
        figures.append({
            "spec": _pack_plotly_spec(f.proto.spec),
            "use_container_width": bool(_proto_field(f.proto, "use_container_width", False)),
            "theme": _proto_field(f.proto, "theme", ""),
        })

    return {
        "element_order": order,
        "metrics": metrics,
        "markdown": [m.value for m in at.markdown],
        "captions": [c.value for c in at.caption],
        "subheaders": [s.value for s in at.subheader],
        "headers": [h.value for h in at.header],
        "text": [t.value for t in at.text],
        "success": [s.value for s in at.success],
        "info": [i.value for i in at.info],
        "expanders": [{"label": e.proto.label, "expanded": bool(e.proto.expanded)}
                      for e in at.get("expander")],
        "dataframes": dataframes,
        "selectboxes": selectboxes,
        "plotly": figures,
        "exception": [e.value for e in at.exception],
    }


_DRIVER = """
import pickle, importlib, sys
sys.path.insert(0, {extra_path!r})
_mod = importlib.import_module({module!r})
with open({frame!r}, "rb") as _fh:
    _df = pickle.load(_fh)
_mod.render_reproducibility_tab(_df)
"""


_CONNECTED_PATHS = []
_REAL_CONNECT = sqlite3.connect


def _recording_connect(database, *args, **kwargs):
    _CONNECTED_PATHS.append(str(database))
    return _REAL_CONNECT(database, *args, **kwargs)


# --- the offline guard -------------------------------------------------------
#
# "No network" was a claim in this file's docstring that nothing measured. It is
# measured now, the way the database isolation is: by making the thing fail and
# recording every attempt, not by reading the import list. The three primitives
# below are every way this process can open an outbound connection --
# ``socket.create_connection`` builds a ``socket.socket`` and calls ``connect``
# on it, so patching the class method covers the helper as well, and both are
# patched anyway rather than reasoned about. ``getaddrinfo`` is included because
# a resolver call is an outbound packet even when no connection follows it.
#
# It RAISES as well as records: a render that reaches the network must fail
# audibly at the call site rather than continue against a stub. The raise lands
# inside the AppTest script run, so it also surfaces through `capture.exception`
# and section 2a's "no scenario raised" check.

_NETWORK_ATTEMPTS = []
_REAL_SOCKET_CONNECT = socket.socket.connect
_REAL_SOCKET_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_GETADDRINFO = socket.getaddrinfo


def _blocked(call_name, target):
    where = _network_caller()
    _NETWORK_ATTEMPTS.append({"call": call_name, "target": repr(target),
                              "caller": where})
    raise OSError(f"[offline guard] {call_name} to {target!r} blocked; "
                  f"attempted from {where}")


def _guard_connect(self, address, *a, **k):
    return _blocked("socket.connect", address)


def _guard_connect_ex(self, address, *a, **k):
    return _blocked("socket.connect_ex", address)


def _guard_create_connection(address, *a, **k):
    return _blocked("socket.create_connection", address)


def _guard_getaddrinfo(host, port, *a, **k):
    return _blocked("socket.getaddrinfo", (host, port))


# THE FOUR STAND-INS ARE NAMED FUNCTIONS, NOT LAMBDAS, and that is what makes
# `_network_caller` able to answer. The first version used lambdas and walked
# back a FIXED two frames, so the frame it named was its own lambda -- every
# report said "this file, the line the lambda is on", whatever had actually
# called out. Its control passed anyway, because the control's caller is also
# this file: the assertion was satisfied for the wrong reason. Measured, not
# noticed by reading. The guard's own frames are skipped BY NAME now, and the
# control below asserts the reported frame is the function that made the call.
_GUARD_FRAMES = {"_blocked", "_network_caller", "_guard_connect",
                 "_guard_connect_ex", "_guard_create_connection",
                 "_guard_getaddrinfo"}


def _network_caller():
    """The innermost frame outside this guard and the socket module."""
    for frame in reversed(traceback.extract_stack()):
        if os.path.basename(frame.filename) == "socket.py":
            continue
        if frame.name in _GUARD_FRAMES:
            continue
        return f"{os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}"
    return "unknown"


def _arm_offline_guard():
    socket.socket.connect = _guard_connect
    socket.socket.connect_ex = _guard_connect_ex
    socket.create_connection = _guard_create_connection
    socket.getaddrinfo = _guard_getaddrinfo


def _disarm_offline_guard():
    socket.socket.connect = _REAL_SOCKET_CONNECT
    socket.socket.connect_ex = _REAL_SOCKET_CONNECT_EX
    socket.create_connection = _REAL_CREATE_CONNECTION
    socket.getaddrinfo = _REAL_GETADDRINFO


def _render(module_name, df, extra_path=""):
    """Render one frame through one module.

    Returns (capture, connected sqlite paths, attempted network calls).

    The cache is cleared first so every render actually opens the database --
    without that only the first render of the run would, and the isolation
    assertion would be about one scenario rather than all of them.
    """
    frame_path = os.path.join(_TMP, "frame.pkl")
    with open(frame_path, "wb") as fh:
        pickle.dump(df, fh)

    script = _DRIVER.format(extra_path=extra_path or _TMP,
                            module=module_name, frame=frame_path)

    st.cache_data.clear()
    del _CONNECTED_PATHS[:]
    del _NETWORK_ATTEMPTS[:]
    sqlite3.connect = _recording_connect
    _dashboard_data.sqlite3.connect = _recording_connect
    _arm_offline_guard()
    try:
        at = AppTest.from_string(script, default_timeout=120)
        at.run()
    finally:
        _disarm_offline_guard()
        sqlite3.connect = _REAL_CONNECT
        _dashboard_data.sqlite3.connect = _REAL_CONNECT

    return _capture_tree(at), list(_CONNECTED_PATHS), list(_NETWORK_ATTEMPTS)


# ===========================================================================
# PLANTING: A COPY OF THE MODULE, NEVER THE SHIPPED FILE
# ===========================================================================

_PLANT_DIR = os.path.join(_TMP, "plants")
os.makedirs(_PLANT_DIR, exist_ok=True)
_PLANT_SEQ = [0]


def _plant_module(old, new, count=1):
    """Write a copy of the module with `old` replaced by `new`.

    Returns (module_name, replacements_made). Nothing under version control is
    touched: the copy lives in a temporary directory and is imported from there.
    """
    made = _REPRO_SOURCE.count(old)
    source = _REPRO_SOURCE.replace(old, new, count)
    _PLANT_SEQ[0] += 1
    name = f"repro_plant_{_PLANT_SEQ[0]}"
    Path(os.path.join(_PLANT_DIR, name + ".py")).write_text(source, encoding="utf-8")
    return name, made


def _literals_from_module(mod):
    """The four hoisted tables as {name: {"value":..., "key_order":[...]}}."""
    out = {}
    for name in _LITERAL_NAMES:
        table = getattr(mod, name)
        out[name] = {"value": table, "key_order": list(table.keys())}
    return out


def _literals_from_source(path):
    """The same, lifted from a source file by AST without importing it."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in _LITERAL_NAMES):
            value = ast.literal_eval(node.value)
            out[node.targets[0].id] = {"value": value, "key_order": list(value.keys())}
    return out


_LITERAL_NAMES = ("_STATUS_DISPLAY_BASE_FLIP", "_FLIP_TYPE_SEVERITY",
                  "_FLIP_TYPE_COLORS", "_FAILURE_CATEGORIES")


#------------------------------------------------------------------------------


_T_START = time.time()

print("=" * 70)
print("DASHBOARD REPRODUCIBILITY TAB — RENDER SNAPSHOT TEST")
print("=" * 70)
print(f"Module under test: {_REPRO_FILE}")
print(f"Snapshot:          {_SNAPSHOT_PATH}")
print(f"Scratch database:  {_SCRATCH_DB}")
print()


# ===========================================================================
# SECTION 1: THE SCRATCH DATABASE, AND IT IS NOT THE PRODUCTION ONE
# ===========================================================================

print("=" * 70)
print("Section 1: the database this test reads is a seeded scratch file")
print("=" * 70)

_PRODUCTION_DB = os.path.abspath(_paths.inferences_path)
_PRODUCTION_DIGEST_BEFORE = _digest_file(_PRODUCTION_DB)

check("1a  the package default resolves to the production database, and the "
      "scratch path is NOT it (without this every check below is vacuous)",
      os.path.abspath(_SCRATCH_DB) != _PRODUCTION_DB, True)
check("1a  ...and the production path is a real resolved path, not empty",
      _PRODUCTION_DB.endswith("inferences.db") and len(_PRODUCTION_DB) > 20, True)

_inf_rows, _tm_rows = _seed_rows()
_build_database(_SCRATCH_DB, _inf_rows, _tm_rows)

check("1b  the scratch database exists after seeding",
      os.path.isfile(_SCRATCH_DB), True)
_probe = sqlite3.connect(_SCRATCH_DB)
try:
    _n_inf = _probe.execute("SELECT COUNT(*) FROM inferences").fetchone()[0]
    _n_tm = _probe.execute("SELECT COUNT(*) FROM trial_matches").fetchone()[0]
    _schema_cols = {r[1] for r in _probe.execute("PRAGMA table_info(inferences)")}
finally:
    _probe.close()
check("1b  ...covering the inferences table", _n_inf, len(_inf_rows))
check("1b  ...and the trial_matches table", _n_tm, len(_tm_rows))
check("1b  ...built from the project's own schema, not a retyped copy "
      "(a column only initialize_database() adds is present)",
      "mesh_filter_skip_reason" in _schema_cols, True)

# THE SEAM. dashboard/data.py reads paths.inferences_path, so the scratch path
# goes into the resolver cache the same way test_ablation_db_isolation.py
# installs its decoy. Restored in section 6.
_SAVED_RESOLVED = _paths._RESOLVED.get("inferences_path")
_paths._RESOLVED["inferences_path"] = _SCRATCH_DB

st.cache_data.clear()
_ALL_INFERENCES = _dashboard_data.load_inferences_data()

check("1b  ...and the dashboard loader reads it back",
      len(_ALL_INFERENCES), len(_inf_rows))

# WHICH TABLE IS READ BY WHOM, and why the frame handed over is the raw one.
# The tab takes ONE argument, the `inferences` frame, and reads `trial_matches`
# ITSELF -- `load_trial_matches_data()` at the top of the render, which is what
# every scenario but `no_collection_column` is seen to open in 5l. This file
# hands it exactly that: `load_inferences_data()`, sliced by patient-id prefix.
#
# `oncotriage/dashboard/app.py` does NOT hand it that. It calls
# `enrich_match_tiers(filtered_df, trial_matches)` first, so in production the
# frame carries four extra columns. Handing over the un-enriched frame is
# equivalent only for as long as the tab reads none of them -- so that is
# asserted against the shipped source rather than assumed, in both reference
# forms a column can be read by (`df['x']` is a string constant, `df.x` is an
# attribute). If the tab ever starts reading one, this frame stops being
# representative of production and this check says so instead of the test
# quietly rendering a branch the dashboard never takes.
_ENRICHED_COLUMNS = {"full_match_count", "partial_match_count",
                     "unconfirmed_match_count", "match_tier"}
_REPRO_TREE = ast.parse(_REPRO_SOURCE)
_REPRO_STRING_LITERALS = set()
_REPRO_ATTRIBUTES = set()
for _node in ast.walk(_REPRO_TREE):
    if isinstance(_node, ast.Constant) and isinstance(_node.value, str):
        _REPRO_STRING_LITERALS.add(_node.value)
    elif isinstance(_node, ast.Attribute):
        _REPRO_ATTRIBUTES.add(_node.attr)
_REPRO_NAMES_USED = _REPRO_STRING_LITERALS | _REPRO_ATTRIBUTES

check("1c  the frame handed to the tab is the raw inferences frame, and the tab "
      "reads none of the four columns app.py's enrich_match_tiers adds",
      _ENRICHED_COLUMNS & _REPRO_NAMES_USED, set())
# THE TWO FORMS GET SEPARATE CONTROLS. The first version asserted one union
# against three column names that all appear as STRING LITERALS, so the
# attribute half of the walk could be deleted outright and the check still
# passed -- measured, not supposed. `columns` and `empty` appear in this module
# only as attributes and `patient_id` and friends only as literals, so each
# branch now has a probe no other branch can satisfy.
check("1c  ...and the STRING-LITERAL half of that scan can see a column name, "
      "so the empty result above is a finding, not a broken walk",
      {"patient_id", "qdrant_collection", "nct_id"} <= _REPRO_STRING_LITERALS, True)
check("1c  ...and the ATTRIBUTE half can too — a `df.match_tier` read must not "
      "escape it just because `df['match_tier']` would not",
      {"columns", "empty"} <= _REPRO_ATTRIBUTES, True)
check("1c  ...and the tab reads trial_matches itself rather than receiving it — "
      "the frame this file passes carries no trial-match column",
      _ENRICHED_COLUMNS & set(_ALL_INFERENCES.columns), set())


# ===========================================================================
# SECTION 2: EVERY SCENARIO, ELEMENT FOR ELEMENT, AGAINST THE SNAPSHOT
# ===========================================================================

print()
print("=" * 70)
print("Section 2: the rendered tab, element for element, vs the golden snapshot")
print("=" * 70)

_CAPTURES = {}
_CONNECTS = {}
_NETWORK = {}

for _name, _prefix, _mutation in _SCENARIOS:
    _frame = _frame_for(_ALL_INFERENCES, _prefix, _mutation)
    _cap, _conn, _net = _render("oncotriage.dashboard.tabs.reproducibility", _frame)
    _CAPTURES[_name] = _cap
    _CONNECTS[_name] = _conn
    _NETWORK[_name] = _net
    print(f"  rendered {_name:22s} {len(_cap['element_order']):4d} elements, "
          f"{len(_cap['metrics']):2d} metrics, {len(_cap['dataframes'])} dataframes, "
          f"{len(_cap['plotly'])} figures")

# THE POOL AS THE SCENARIOS LEFT IT, frozen by COPY. `_TEMPLATE_POOL` keeps
# filling for the rest of the run -- section 5's template plants each add an
# entry to it on purpose -- and the snapshot must carry the seven scenarios'
# templates and nothing else. Binding the live dict here (which is what the
# first version did) makes the file that is written depend on where in this
# file the write happens to sit.
_SCENARIO_TEMPLATE_POOL = dict(_TEMPLATE_POOL)

_LIVE = {
    "plotly_templates": _SCENARIO_TEMPLATE_POOL,
    "literals": _literals_from_module(_repro),
    "scenarios": _CAPTURES,
}

if _UPDATE_SNAPSHOT:
    os.makedirs(os.path.dirname(_SNAPSHOT_PATH), exist_ok=True)
    Path(_SNAPSHOT_PATH).write_text(
        json.dumps(_LIVE, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8")
    print()
    print(f"  [--update-snapshot] wrote {_SNAPSHOT_PATH}")
    print("  Re-run without the flag to compare against it.")

check("2a  the snapshot file exists (regenerate with --update-snapshot)",
      os.path.isfile(_SNAPSHOT_PATH), True)

_SNAP = json.loads(Path(_SNAPSHOT_PATH).read_text(encoding="utf-8")) \
    if os.path.isfile(_SNAPSHOT_PATH) else {"scenarios": {}, "literals": {},
                                            "plotly_templates": {}}

check("2a  ...and names exactly the scenarios this file drives",
      sorted(_SNAP.get("scenarios", {})), sorted(n for n, _, _ in _SCENARIOS))

# Non-degeneracy first: a snapshot of an empty render would make every
# comparison below pass while proving nothing.
check("2a  the 'full' scenario is non-degenerate — it renders every element "
      "class this file compares",
      all(len(_CAPTURES["full"][k]) > 0 for k in
          ("element_order", "metrics", "markdown", "captions", "subheaders",
           "dataframes", "selectboxes", "plotly", "expanders", "text")),
      True)
check("2a  ...and no scenario raised inside the render",
      {n: c["exception"] for n, c in _CAPTURES.items() if c["exception"]}, {})

_FIELDS = ("element_order", "metrics", "markdown", "captions", "subheaders",
           "headers", "text", "success", "info", "expanders", "dataframes",
           "selectboxes", "plotly")

for _name, _, _ in _SCENARIOS:
    _snap_scn = _SNAP.get("scenarios", {}).get(_name, {})
    for _field in _FIELDS:
        _live_v = _CAPTURES[_name][_field]
        _snap_v = _snap_scn.get(_field)
        if _live_v == _snap_v:
            check(f"2b  {_name}.{_field} ({len(_live_v)}) matches the snapshot",
                  True, True)
        else:
            # Report the first difference rather than two large blobs.
            _detail = "field missing from the snapshot" if _snap_v is None else None
            if _detail is None and isinstance(_live_v, list) and isinstance(_snap_v, list):
                if len(_live_v) != len(_snap_v):
                    _detail = f"length {len(_live_v)} vs snapshot {len(_snap_v)}"
                else:
                    for _i, (_a, _b) in enumerate(zip(_live_v, _snap_v)):
                        if _a != _b:
                            _detail = f"index {_i}: {_a!r} vs snapshot {_b!r}"
                            break
            check(f"2b  {_name}.{_field} matches the snapshot", _detail, "no difference")

_SNAP_POOL = _SNAP.get("plotly_templates", {})

# Non-degeneracy first. A pool that came back empty would satisfy every
# comparison below while proving that no template was ever captured.
_LIVE_REFS = {f["spec"].get("layout", {}).get("__template_ref__")
              for c in _CAPTURES.values() for f in c["plotly"]}
_SNAP_REFS = {f["spec"].get("layout", {}).get("__template_ref__")
              for c in _SNAP.get("scenarios", {}).values() for f in c.get("plotly", [])}

check("2c  the pool is non-degenerate — at least one template was captured and "
      "every figure carries a ref",
      bool(_SCENARIO_TEMPLATE_POOL) and _LIVE_REFS and None not in _LIVE_REFS, True)
check("2c  every ref a live figure carries is IN the pool (a hoisted template "
      "that never made it into the pool would be dropped, not compared)",
      _LIVE_REFS - set(_SCENARIO_TEMPLATE_POOL), set())
check("2c  ...and the same holds inside the snapshot file itself, so a pool "
      "written short is caught rather than silently believed",
      _SNAP_REFS - set(_SNAP_POOL), set())
check("2c  every plotly template referenced by a scenario is in the pool",
      sorted(_SCENARIO_TEMPLATE_POOL), sorted(_SNAP_POOL))
# EXACT bytes, key order included. `sort_keys=True` here would make this blind
# to precisely the thing `_template_blob` was changed to stop being blind to.
check("2c  ...and the template bytes themselves are unchanged, key order included",
      {k: _digest_text(_template_blob(v)) for k, v in _SCENARIO_TEMPLATE_POOL.items()},
      {k: _digest_text(_template_blob(v)) for k, v in _SNAP_POOL.items()})
check("2c  ...and each pool key IS the digest of the bytes stored under it, so "
      "the ref is derived from the template rather than assigned beside it",
      {k for k, v in _SNAP_POOL.items()
       if hashlib.sha256(_template_blob(v).encode("utf-8")).hexdigest()[:16] != k},
      set())
check("2c  no two distinct templates were pooled under one ref",
      _TEMPLATE_POOL_COLLISIONS, [])


# ===========================================================================
# SECTION 3: THE THREE WIDGET KEYS ARE SESSION STATE AND MUST NOT MOVE
# ===========================================================================

print()
print("=" * 70)
print("Section 3: the three widget keys")
print("=" * 70)

_EXPECTED_KEYS = {"repro_collection_filter", "flip_deep_dive_selector",
                  "drift_deep_dive_selector"}
_RENDERED_KEYS = {s["key"] for c in _CAPTURES.values() for s in c["selectboxes"]}

check("3a  all three keys are rendered by the scenarios (a key nothing renders "
      "is a key this file cannot protect)",
      _EXPECTED_KEYS - _RENDERED_KEYS, set())
check("3b  ...and no fourth key appeared", _RENDERED_KEYS - _EXPECTED_KEYS, set())
check("3c  the 'full' scenario carries all three at once",
      sorted(s["key"] for s in _CAPTURES["full"]["selectboxes"]),
      sorted(_EXPECTED_KEYS))
for _sb in _CAPTURES["full"]["selectboxes"]:
    _snap_sbs = {s["key"]: s for s in
                 _SNAP.get("scenarios", {}).get("full", {}).get("selectboxes", [])}
    check(f"3d  {_sb['key']}: label, options and value match the snapshot",
          _sb, _snap_sbs.get(_sb["key"]))


# ===========================================================================
# SECTION 4: THE HOISTED LITERALS, BY VALUE AND BY KEY ORDER
# ===========================================================================

print()
print("=" * 70)
print("Section 4: the four hoisted literal tables")
print("=" * 70)

_LIVE_LITERALS = _literals_from_module(_repro)
_SNAP_LITERALS = _SNAP.get("literals", {})

check("4a  the snapshot carries all four tables",
      sorted(_SNAP_LITERALS), sorted(_LITERAL_NAMES))

for _lit in _LITERAL_NAMES:
    check(f"4b  {_lit} value matches the snapshot",
          _LIVE_LITERALS[_lit]["value"], _SNAP_LITERALS.get(_lit, {}).get("value"))
    check(f"4c  {_lit} KEY ORDER matches the snapshot",
          _LIVE_LITERALS[_lit]["key_order"],
          _SNAP_LITERALS.get(_lit, {}).get("key_order"))

# The two entries no data can reach. Measured, not asserted from the docstring:
# _classify_flip_type only ever runs over rows with 2+ distinct classifications,
# so 'Rejected' is always in tiers_seen.
_RENDERED_FLIP_TYPES = set()
for _cap in _CAPTURES.values():
    for _fig in _cap["plotly"]:
        for _trace in _fig["spec"].get("data", []):
            for _y in (_trace.get("y") or []):
                if isinstance(_y, str) and "↔" in _y:
                    _RENDERED_FLIP_TYPES.add(_y)

check("4d  at least three flip types are actually rendered (so the render "
      "comparison covers the reachable part of _FLIP_TYPE_COLORS)",
      len(_RENDERED_FLIP_TYPES) >= 3, True)
check("4e  'Full Match ↔ Partial Match' is rendered by NO scenario — it is in "
      "the table and unreachable, which is why section 4 exists",
      "Full Match ↔ Partial Match" in _RENDERED_FLIP_TYPES, False)


# ===========================================================================
# SECTION 5: NEGATIVE CONTROLS — EVERY ASSERTION ABOVE, SHOWN TO FAIL
# ===========================================================================
#
# Each plant is applied to a COPY of the module written into a temporary
# directory. `caught` means the plant produced a difference the corresponding
# check would report. A plant that changes nothing is a no-op, not a control,
# and is a FAILURE of this section.

print()
print("=" * 70)
print("Section 5: planted defects (every one must be caught)")
print("=" * 70)

_FULL_FRAME = _frame_for(_ALL_INFERENCES, "FULL-", None)

# THE PLANTS ARE COMPARED AGAINST THE COMMITTED SNAPSHOT, not against the live
# render, so a control says "this defect would have failed section 2" rather
# than "this defect differs from whatever the module does today". Section 2 has
# already asserted the two are equal; the fallback is for the first run after
# --update-snapshot, where the file was written moments ago.
_BASE = _SNAP.get("scenarios", {}).get("full") or _CAPTURES["full"]


def _plant_and_render(label, old, new, fields, expect_caught=True, count=1):
    """Plant, render the 'full' frame through the copy, report which fields moved.

    Returns (moved fields, capture). The capture is returned because the two
    template plants need the per-figure refs out of it, not only the verdict.
    """
    name, occurrences = _plant_module(old, new, count)
    check(f"5.  [{label}] the planted text occurs exactly once in the module",
          occurrences, 1)
    cap, _, _ = _render(name, _FULL_FRAME, extra_path=_PLANT_DIR)
    if cap["exception"]:
        check(f"5.  [{label}] the planted module rendered without raising",
              cap["exception"], [])
        return set(), cap
    moved = {f for f in _FIELDS if cap[f] != _BASE[f]}
    for field in fields:
        check(f"5.  [{label}] caught by {field}", field in moved, expect_caught)
    return moved, cap


def _figure_refs(cap):
    """The template ref of every figure in a capture, in document order."""
    return [f["spec"].get("layout", {}).get("__template_ref__") for f in cap["plotly"]]


def _at(seq, index, label):
    """``seq[index]``, or a string naming the absence.

    A check that indexes a list which a defect can SHORTEN raises, and a raise
    at module level in a file like this one aborts the run and hides every
    check below it -- one traceback where ten failures were owed.
    ``tests/test_storage_query_layer.py`` had to fix exactly this, and the
    first version of section 5n reproduced it: a planted defect that made a
    render produce no figures at all took `_refs_5n[0]` with it and section 5o
    and the whole of section 6 never ran. Measured against that plant, not
    anticipated.

    The IndexError is not swallowed: it is converted into a value that makes
    the caller's `check()` FAIL and say what was missing.
    """
    try:
        return seq[index]
    except IndexError:
        return f"<no {label} at index {index}: only {len(seq)} captured>"


_BASE_REFS = _figure_refs(_BASE)


# --- 5a  a rendered colour ---------------------------------------------------
_plant_and_render(
    "5a rendered flip-type colour",
    "'Rejection ↔ Partial Match': '#ff7f0e',",
    "'Rejection ↔ Partial Match': '#ff7f0f',",
    ("plotly",))

# --- 5b  an UNREACHABLE colour: the render must NOT see it, section 4 must ---
_name_5b, _occ_5b = _plant_module(
    "'Full Match ↔ Partial Match': '#2ca02c',",
    "'Full Match ↔ Partial Match': '#2ecc71',")
check("5.  [5b unreachable colour] the planted text occurs exactly once", _occ_5b, 1)
_cap_5b, _, _ = _render(_name_5b, _FULL_FRAME, extra_path=_PLANT_DIR)
_moved_5b = {f for f in _FIELDS if _cap_5b[f] != _BASE[f]}
check("5b  the RENDER comparison sees nothing — this is pass 20f-4's actual "
      "shipped defect, and it is invisible to every element",
      _moved_5b, set())
sys.path.insert(0, _PLANT_DIR)
try:
    _mod_5b = importlib.import_module(_name_5b)
finally:
    sys.path.remove(_PLANT_DIR)
check("5b  ...and the LITERAL check catches it",
      _literals_from_module(_mod_5b)["_FLIP_TYPE_COLORS"]["value"]
      == _LIVE_LITERALS["_FLIP_TYPE_COLORS"]["value"], False)

# --- 5c  key ORDER of _FAILURE_CATEGORIES -----------------------------------
_ORDER_OLD = """    'Temporal / Resolved Status': {
        'keywords': ["""
_ORDER_NEW = """    'Zzz Placeholder Category': {
        'keywords': ['__never_matches__'],
        'color': '#000000',
        'fix': 'placeholder',
    },
    'Temporal / Resolved Status': {
        'keywords': ["""
_moved_5c, _ = _plant_and_render(
    "5c failure-category key order", _ORDER_OLD, _ORDER_NEW, ())
check("5c  a reordered _FAILURE_CATEGORIES is caught by the literal check",
      _literals_from_source(os.path.join(_PLANT_DIR, f"repro_plant_{_PLANT_SEQ[0]}.py"))
      ["_FAILURE_CATEGORIES"]["key_order"]
      == _LIVE_LITERALS["_FAILURE_CATEGORIES"]["key_order"], False)

# --- 5d  a 'fix' string, which reaches the recommended-fix table ------------
_plant_and_render(
    "5d recommended-fix string",
    "'fix': 'Present-tense rule (prompt)',",
    "'fix': 'Present tense rule (prompt)',",
    ("dataframes",))

# --- 5e  a helper's sort direction ------------------------------------------
_plant_and_render(
    "5e flip-table sort direction",
    "flip_display = flip_display.sort_values('score_spread', ascending=False)",
    "flip_display = flip_display.sort_values('score_spread', ascending=True)",
    ("dataframes",))

# --- 5f  _normalize_criterion drops .lower() --------------------------------
# Pass 20f-4 planted this and measured it as a NO-OP, because the criteria in
# the flip it selected already agreed in case. The seed above spells one
# criterion in a different case across runs, so here it bites.
_plant_and_render(
    "5f normalize_criterion loses .lower()",
    "return ' '.join(text.lower().strip().split())",
    "return ' '.join(text.strip().split())",
    ("dataframes",))

# --- 5g  a metric computation ------------------------------------------------
_plant_and_render(
    "5g group metric arithmetic",
    "'identical_scores': identical / n * 100,",
    "'identical_scores': identical / n * 99,",
    ("metrics",))

# --- 5h  a figure height ------------------------------------------------------
# MEASURED, AND THE OBVIOUS PLANT WAS A NO-OP. The first version changed the
# per-row term, `max(200, len(sorted_types) * 60)` -> `* 61`: three flip types
# render, so both arms evaluate max(200, 180) and max(200, 183) and both are
# 200. The FLOOR is what this figure's height actually is on this data, so the
# floor is what the control moves.
_plant_and_render(
    "5h figure height",
    "height=max(200, len(sorted_types) * 60),",
    "height=max(210, len(sorted_types) * 60),",
    ("plotly",))

# --- 5i  a WIDGET KEY --------------------------------------------------------
_plant_and_render(
    "5i widget key renamed",
    'key="flip_deep_dive_selector"',
    'key="flip_deep_dive_selector_v2"',
    ("selectboxes",))

# --- 5j  a status display string --------------------------------------------
_plant_and_render(
    "5j status display map",
    'return "➖ Not Applicable"',
    'return "➖ N/A"',
    ("dataframes",))

# --- 5k  an element that is only a layout change ----------------------------
_plant_and_render(
    "5k a caption rendered as markdown",
    '    st.caption(\n        "**Threshold rationale:** "',
    '    st.markdown(\n        "**Threshold rationale:** "',
    ("element_order", "captions", "markdown"))


# --- 5m  INSIDE layout.template ITSELF ---------------------------------------
# The one part of the snapshot with custom machinery between capture and
# comparison was the one part with no plant behind it: 5a moves a marker colour
# and 5h a figure height, and BOTH live outside `layout.template`. So the claim
# that a changed template is still compared rested on reading `_pack_plotly_spec`
# rather than on breaking it.
#
# This plant changes ONE integer ~7 KB deep inside the template of ONE of the
# six figures the 'full' scenario renders, and touches nothing else. Measured
# before it was chosen: plotly COPIES a named template into the figure on
# assignment, so mutating `fig.layout.template` leaves `plotly.io.templates`
# and every other figure in this process untouched -- a plant that corrupted the
# registry would poison the renders after it and look like a much larger defect.

_moved_5m, _cap_5m = _plant_and_render(
    "5m one field inside layout.template",
    "    return fig_flip_types",
    "    fig_flip_types.layout.template.layout.font.size = 99\n"
    "    return fig_flip_types",
    ("plotly",))

_refs_5m = _figure_refs(_cap_5m)
_new_5m = set(_TEMPLATE_POOL) - set(_SCENARIO_TEMPLATE_POOL)

check("5m  the mutated template produced a NEW pool entry — it did not reuse "
      "the entry the unmutated template already had",
      len(_new_5m), 1)
check("5m  ...and the pool kept the original entry alongside it, so the new "
      "template did not overwrite the old one",
      set(_SCENARIO_TEMPLATE_POOL) <= set(_TEMPLATE_POOL), True)
check("5m  ...still with no two distinct templates under one ref",
      _TEMPLATE_POOL_COLLISIONS, [])
check("5m  exactly ONE of the six figures changed its ref",
      sum(1 for a, b in zip(_refs_5m, _BASE_REFS) if a != b), 1)
check("5m  ...and it is the flip-type chart, the figure the plant edits "
      "(figure 2 of 6 in document order)",
      [i for i, (a, b) in enumerate(zip(_refs_5m, _BASE_REFS)) if a != b], [2])
check("5m  ...and the other five still carry the unmutated ref, so the change "
      "was not smeared across the pool",
      [r for i, r in enumerate(_refs_5m) if i != 2],
      [r for i, r in enumerate(_BASE_REFS) if i != 2])

# --- 5n  A WHOLE DIFFERENT TEMPLATE ------------------------------------------
# The realistic version of the same defect: somebody restyles one chart. Two
# genuinely different templates are then live in one pool at once, which is the
# state the collision question is about.
_moved_5n, _cap_5n = _plant_and_render(
    "5n one figure switched to a different named template",
    "        template='plotly_white',\n        xaxis=dict(range=[-0.05, 1.05]),",
    "        template='plotly_dark',\n        xaxis=dict(range=[-0.05, 1.05]),",
    ("plotly",))

_refs_5n = _figure_refs(_cap_5n)

check("5n  the swapped template is a second NEW pool entry",
      len(set(_TEMPLATE_POOL) - set(_SCENARIO_TEMPLATE_POOL) - _new_5m), 1)
check("5n  exactly ONE of the six figures changed its ref",
      sum(1 for a, b in zip(_refs_5n, _BASE_REFS) if a != b), 1)
check("5n  ...and it is the min/max scatter, figure 0 of 6",
      [i for i, (a, b) in enumerate(zip(_refs_5n, _BASE_REFS)) if a != b], [0])
check("5n  ...with the two mutated templates distinct from each other and from "
      "the original (three refs, not one absorbing the others)",
      len({_at(_refs_5m, 2, "5m figure ref"),
           _at(_refs_5n, 0, "5n figure ref"),
           _at(_BASE_REFS, 0, "baseline figure ref")}), 3)
check("5n  ...and still no collisions", _TEMPLATE_POOL_COLLISIONS, [])


# --- 5l  THE ISOLATION ASSERTION ITSELF -------------------------------------
# Section 1 asserts the render only ever opened the scratch database. Run the
# same assertion against a DECOY standing in for a wrong path and it must FAIL.
# A decoy rather than the real production database, on File 41's precedent: a
# demonstration that proved the point by reading production would be the defect
# it is testing for.

print()
print("  --- 5l the scratch-database isolation assertion, shown to fail ---")

_build_database(_DECOY_DB, _inf_rows, _tm_rows)


def _isolation_holds(connected):
    """No path opened during a render was anything but the scratch database.

    A SUBSET, not equality, and the difference is a real branch rather than
    leniency: `no_collection_column` returns before ``load_trial_matches_data``
    is called, so it legitimately opens nothing at all. Equality would report
    that scenario as a failure. The empty case is closed separately below --
    that scenario is asserted to open NOTHING, and the six that do read are
    asserted to have opened exactly the scratch file.
    """
    return {os.path.abspath(p) for p in connected} <= {os.path.abspath(_SCRATCH_DB)}


for _scn, _paths_seen in _CONNECTS.items():
    check(f"5l  [{_scn}] no sqlite3.connect during the render opened anything "
          f"but the scratch database", _isolation_holds(_paths_seen), True)
check("5l  ...and the production database was never opened by any render",
      any(os.path.abspath(p) == _PRODUCTION_DB
          for paths_seen in _CONNECTS.values() for p in paths_seen), False)
check("5l  the scenario that returns before the database read opened NOTHING",
      _CONNECTS["no_collection_column"], [])
check("5l  ...and every other scenario opened exactly the scratch database, so "
      "the subset check above is not passing on empty lists (non-degeneracy)",
      {s: {os.path.abspath(p) for p in v} for s, v in _CONNECTS.items()
       if s != "no_collection_column"},
      {s: {os.path.abspath(_SCRATCH_DB)} for s, _, _ in _SCENARIOS
       if s != "no_collection_column"})

_paths._RESOLVED["inferences_path"] = _DECOY_DB
_, _decoy_connects, _ = _render("oncotriage.dashboard.tabs.reproducibility",
                                _FULL_FRAME)
_paths._RESOLVED["inferences_path"] = _SCRATCH_DB

check("5l  CONTROL: pointed at a decoy database, the identical assertion FAILS",
      _isolation_holds(_decoy_connects), False)
check("5l  ...and the decoy is what it opened, so the control failed for the "
      "reason claimed",
      {os.path.abspath(p) for p in _decoy_connects},
      {os.path.abspath(_DECOY_DB)})


# --- 5o  NO OUTBOUND CONNECTION, MEASURED RATHER THAN READ -------------------
# "No network" was a docstring claim with nothing behind it. Every render above
# ran with `socket.socket.connect`, `socket.socket.connect_ex`,
# `socket.create_connection` and `socket.getaddrinfo` replaced by a recorder
# that RAISES, so a render that reached out would have failed at the call site
# and named it. What follows reads what those recorders saw.

print()
print("  --- 5o the offline guard ---")

for _scn, _attempts in _NETWORK.items():
    check(f"5o  [{_scn}] the render attempted no outbound connection",
          _attempts, [])

check("5o  ...which is not a claim about an unarmed trap: the guard is armed "
      "and disarmed around every render, and is disarmed now",
      (socket.socket.connect is _REAL_SOCKET_CONNECT
       and socket.create_connection is _REAL_CREATE_CONNECTION
       and socket.getaddrinfo is _REAL_GETADDRINFO), True)

# CONTROL: arm the identical guard and make the identical call. It must raise,
# and it must record what it blocked -- otherwise the seven checks above are
# seven readings of a list nothing could ever have appended to.
def _offline_control_call():
    """Named on purpose: 5o asserts the guard reports THIS frame.

    `basename(__file__)` alone would not discriminate -- the guard's own
    stand-ins live in this file too, so a guard that named itself would satisfy
    it. Naming the function that made the call is the assertion that can only
    be satisfied by walking the stack correctly.
    """
    socket.create_connection(("clinicaltrials.gov", 443), timeout=1)


_arm_offline_guard()
del _NETWORK_ATTEMPTS[:]
_control_raised = False
try:
    _offline_control_call()
except OSError as _exc:
    _control_raised = "[offline guard]" in str(_exc)
finally:
    _disarm_offline_guard()

check("5o  CONTROL: the armed guard blocks a real outbound call", _control_raised, True)
check("5o  ...and records the call and its target",
      [(a["call"], a["target"]) for a in _NETWORK_ATTEMPTS],
      [("socket.create_connection", repr(("clinicaltrials.gov", 443)))])
check("5o  ...naming the frame that made the call, not one of its own",
      _NETWORK_ATTEMPTS[0]["caller"].endswith(" in _offline_control_call")
      if _NETWORK_ATTEMPTS else "nothing recorded", True)
check("5o  ...and in this file, at a real line",
      _NETWORK_ATTEMPTS[0]["caller"].startswith(os.path.basename(__file__) + ":")
      if _NETWORK_ATTEMPTS else "nothing recorded", True)
del _NETWORK_ATTEMPTS[:]

check("5o  ...and the guard is off again afterwards, so nothing below runs "
      "against a patched socket module",
      socket.create_connection is _REAL_CREATE_CONNECTION, True)


# ===========================================================================
# SECTION 6: NOTHING IN THE REPOSITORY WAS TOUCHED
# ===========================================================================

print()
print("=" * 70)
print("Section 6: the shipped module and the production database are untouched")
print("=" * 70)

# The resolver cache goes back to whatever it held. `None` means the name had
# not been resolved when this file started, and the right restore is to remove
# the key rather than to cache a None that would answer every later read.
if _SAVED_RESOLVED is None:
    _paths._RESOLVED.pop("inferences_path", None)
else:
    _paths._RESOLVED["inferences_path"] = _SAVED_RESOLVED

check("6a  the module under test is byte-identical to what this run started with",
      _digest_file(_REPRO_FILE), _REPRO_DIGEST_BEFORE)
check("6a  ...and that digest is a real one, not 'absent' on both sides",
      _REPRO_DIGEST_BEFORE != "absent", True)
check("6b  the production inferences database is byte-identical",
      _digest_file(_PRODUCTION_DB), _PRODUCTION_DIGEST_BEFORE)
check("6c  every planted copy lived in the temporary directory, none beside "
      "the shipped module",
      sorted(p.name for p in Path(os.path.dirname(_REPRO_FILE)).glob("repro_plant_*.py")),
      [])
check("6d  the paths resolver cache is restored",
      _paths._RESOLVED.get("inferences_path"), _SAVED_RESOLVED)

st.cache_data.clear()
shutil.rmtree(_TMP, ignore_errors=True)


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
print(f"Runtime: {time.time() - _T_START:.1f}s")

if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(f"  - {_f}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
