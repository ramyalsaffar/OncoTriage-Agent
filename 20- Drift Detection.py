# Drift Detection for OncoMatch Agent
#####################################

"""
Monitors data drift, retrieval drift and performance drift in the clinical trial
matching pipeline. Uses statistical tests (KS, PSI, z-score) to detect
distribution shifts and performance degradation.

THIN ENTRY POINT (item 20c pass 3b moved the logic; pass 20e removed the shim)
------------------------------------------------------------------------------
Every definition lives in ``oncotriage/monitoring/drift.py``. What is left here
is a ``__main__`` guard and one call to ``main()``.

WHY THE SIXTEEN-NAME SHIM WENT. It existed for one consumer:
``41- ECOG Availability Metric Test.py`` exec-chained this file and read nine
names out of the shared exec namespace. Pass 20d-1 moved that file to
``tests/test_monitoring_ecog_availability_drift.py``, which imports
``oncotriage.monitoring.drift`` directly -- so from that pass onward NOTHING in
the repository chained File 20. What kept the shim alive for one more pass was
the check that tested it: pass 20d-1 rewrote section 8b to exec this file into a
THROWAWAY namespace and assert the sixteen names arrived, because comparing the
test's own imported globals against the package would have been true by
construction. That is a check whose only subject is the shim, which is a
circular reason to keep one. Section 8b retired with the shim in pass 20e and
was replaced by a check on what this file actually is now: a guard that calls
``drift.main`` and re-exports nothing.

`python "20- Drift Detection.py"` WORKS, AND BEFORE PASS 3b IT NEVER DID.
File 20 contained ZERO import statements. Not "few" -- zero. It reached for
numpy, pandas, sqlite3, datetime, timezone, Tuple, Dict, traceback, ks_2samp,
inferences_path and eight config constants, and every one of them resolved only
because some OTHER file had exec'd "01- Imports.py" and "03- Config.py" into the
namespace first. Run directly, it died on ``PSI_BINS`` at the first ``def``
statement -- while the ``__main__`` block below told the user to run exactly
that command, and the dashboard's drift tab told them the same.

THREE THINGS THE MODULE'S DOCSTRING ARGUES IN FULL: every reader and writer
takes ``db_path`` (File 41 rebound the global instead, which a module function
cannot see -- it was the last writer in the repository that did);
``log_drift_metrics`` returns the path it wrote to, so an isolation test can
assert on it; and ``SCIPY_AVAILABLE`` is a real ``ImportError`` guard rather
than a ``NameError`` guard on somebody else's namespace.

DELIBERATELY NOT THE 01/02 EXEC BOOTSTRAP, and it never was. Running drift
detection must not import torch, transformers, streamlit, matplotlib and
langgraph, and must not build an OpenAI and a Qdrant client, in order to run
three statistical tests over a SQLite table.

THE REFERENCE IS DESIGNATED, NOT INFERRED FROM A DATE (the drift redesign).
``get_baseline_and_current_data`` is gone: it took the earliest rows in the
table as the baseline and the latest as the comparison, so which rows the
pipeline was measured against was decided by insertion order -- and a baseline
captured AFTER something went wrong read as no drift at all. An operator now
chooses the reference campaign explicitly and the choice is recorded with its
run ids, its row ids and a content digest.

THE COMPARISON CAMPAIGN IS CHOSEN EXPLICITLY, AND THERE IS NO DEFAULT. The
engine used to resolve ``MAX(runs.id)`` itself, so which campaign was measured
was decided by insertion order -- the same defect the reference designation
replaced, surviving on the other side of the comparison. ``--comparison`` is
REQUIRED for a detection run: a run id, or the literal ``latest``, which is an
explicit opt-in and is RECORDED as one on every row.

Run from terminal:
    python "20- Drift Detection.py" --comparison <run_id>  # run the detection
    python "20- Drift Detection.py" --comparison latest    # ...on whatever ran last
    python "20- Drift Detection.py" --show-reference       # what is designated
    python "20- Drift Detection.py" --designate-reference <run_id> [--label L]
    python "20- Drift Detection.py" --clear-reference      # retire the active one

Every subcommand accepts ``--db PATH``. It is READ-ONLY except for the
designation commands and the drift rows the detection writes; nothing here
calls a model and nothing costs money.
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# The same six-line block Files 04, 06, 11, 12, 15, 16 and 17 carry: import the
# package, falling back to putting this directory on sys.path and PRINTING that
# it did. `pip install -e .` makes it a no-op. Same three candidates, in the
# same order, as the bootstrap that used to live in "01- Imports.py".
try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals()
         else None, "__file__"),
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


#------------------------------------------------------------------------------


# ===========================================================================
# COMMAND-LINE EXECUTION
# ===========================================================================

if __name__ == "__main__":
    """
    Run drift detection, or manage the reference designation.

    Usage:
        python "20- Drift Detection.py" --comparison <run_id>
        python "20- Drift Detection.py" --comparison latest
        python "20- Drift Detection.py" --show-reference
        python "20- Drift Detection.py" --designate-reference <run_id>
        python "20- Drift Detection.py" --clear-reference

    A BARE INVOCATION NO LONGER RUNS THE DETECTION. It exits 2 naming
    ``--comparison``, which is a CONTRACT CHANGE stated as one: a bare
    invocation used to measure whatever campaign happened to be last, and a
    default is exactly what that argument exists to remove. The three reference
    subcommands do not take it -- they read or write a designation and measure
    nothing.

    THIS COMMAND WORKS NOW. It did not before item 20c pass 3b, for the reason
    written at the top of this file: File 20 had no imports and resolved only
    inside somebody else's exec namespace. The instruction above was wrong for
    as long as it has been written down.

    THE ARGUMENT PARSER LIVES IN THIS GUARD, not in ``drift.main()``. Same
    reason "05- FHIR Clean Data.py" puts ``--dry-run`` here: ``main()`` takes no
    arguments, an embedder calls it programmatically, and a ``main()`` that
    started reading ``sys.argv`` would ``SystemExit(2)`` inside somebody else's
    process. A bare invocation is unchanged; an unrecognised flag exits 2 with
    usage, which it did not before -- nothing read ``sys.argv`` at all, so a
    mistyped flag silently ran the detection.

    DESIGNATION IS A SEPARATE COMMAND AND NOT A FLAG ON THE RUN. It writes, it
    is deliberate, and an operator who typed it meant it; folding it into the
    run would make "detect drift" a command that can silently change what drift
    is measured against.
    """

    # Imported inside the guard, not at module scope. oncotriage.monitoring.drift
    # imports oncotriage.paths and oncotriage.config; neither resolves a
    # directory at import (pass 20c-2b made paths lazy), but this file's whole
    # remaining job is one call, and a module-scope import would be a name in
    # this namespace that nothing but the call reads -- which after pass 20e is
    # the one thing this file is not allowed to have.
    import argparse

    from oncotriage.monitoring import drift as _drift
    from oncotriage.monitoring.drift_reference import DesignationError

    _parser = argparse.ArgumentParser(
        description="Drift detection, and the reference it is measured "
                    "against.")
    _parser.add_argument(
        "--db", default=None,
        help="Database to read and write. Defaults to the configured "
             "production inferences database.")
    _group = _parser.add_mutually_exclusive_group()
    _group.add_argument(
        "--designate-reference", metavar="RUN_ID", type=int, default=None,
        help="Designate the campaign containing RUN_ID as THE drift "
             "reference. The whole campaign is resolved from it, resumes "
             "included, and its row ids and content digest are recorded.")
    _group.add_argument(
        "--show-reference", action="store_true",
        help="Resolve and print the active designation. Runs nothing else.")
    _group.add_argument(
        "--clear-reference", action="store_true",
        help="Retire the active designation. It is deactivated, never "
             "deleted: drift rows already written name it.")
    _parser.add_argument(
        "--comparison", default=None, metavar="RUN_ID|latest",
        help="WHICH campaign to measure: a run id, or the literal 'latest' "
             "as an explicit opt-in to whatever ran last. REQUIRED for a "
             "detection run; there is no default, and which of the two was "
             "used is recorded on every row in "
             "drift_metrics.comparison_selection.")
    _parser.add_argument(
        "--label", default=None,
        help="A short name for the designation, carried into every caption. "
             "Only meaningful with --designate-reference.")
    _parser.add_argument(
        "--note", default=None,
        help="Free text recorded with the designation. Only meaningful with "
             "--designate-reference.")
    _args = _parser.parse_args()

    if _args.designate_reference is not None:
        try:
            _drift.designate_reference(_args.designate_reference,
                                       db_path=_args.db, label=_args.label,
                                       note=_args.note)
        except DesignationError as _exc:
            # EXIT 1 AND NOT A TRACEBACK. A designation that cannot name a
            # campaign is an operator-facing refusal with a remedy in its
            # message, and a traceback buries the message under a stack.
            print(f"✗ Could not designate a reference: {_exc}",
                  file=sys.stderr)
            sys.exit(1)
    elif _args.show_reference:
        _drift.show_reference(db_path=_args.db)
    elif _args.clear_reference:
        _drift.clear_reference(db_path=_args.db)
    else:
        # THE REFUSAL IS HERE RATHER THAN IN `main()`, and it is argparse's own
        # exit 2 rather than a raise: an operator who typed no comparison gets
        # the usage text naming the flag, which is what a missing required
        # argument is supposed to look like. `main()` still takes it as a
        # REQUIRED positional, so an embedder cannot omit it either.
        if _args.comparison is None:
            _parser.error(
                "--comparison is required for a detection run: a run id, or "
                "'latest' as an explicit opt-in to whatever ran last. There "
                "is no default -- which campaign a drift run measures is a "
                "decision, and a tool that picked one silently is the defect "
                "the reference designation already removed from the other "
                "side of the comparison.")
        if _args.comparison == _drift.COMPARISON_LATEST:
            _comparison = _drift.COMPARISON_LATEST
        else:
            try:
                _comparison = int(_args.comparison)
            except ValueError:
                _parser.error(
                    f"--comparison {_args.comparison!r} is neither an integer "
                    f"run id nor {_drift.COMPARISON_LATEST!r}.")
        _drift.main(_comparison, db_path=_args.db)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 16 21:09:14 2026

@author: ramyalsaffar
"""
