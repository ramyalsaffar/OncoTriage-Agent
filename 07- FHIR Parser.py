# FHIR Parser for Patients Data
###############################

"""
FHIR Patient Parser — corpus smoke run.

THIN ENTRY POINT (item 20c pass 2b moved the logic; pass 20e removed the shim)
------------------------------------------------------------------------------
Every definition lives in ``oncotriage/fhir/parser.py``. What is left here is
the ``__main__`` block File 07 has always carried: parse every bundle in the
corpus and print how many came back. It is the cheapest answer to "does the
patient corpus still parse", and it is a script, so it does not belong in the
library.

WHY THE 45-NAME SHIM WENT. It existed because Files 05, 17, 25, 26, 38, 39, 40
and 45 exec-chained this file and read its names out of the shared exec
namespace. Pass 20e measured all eight and none of them is a chainer any more:
17, 25 and 26 became thin entry points (passes 20c-3b, 20c-3d), 38, 39 and 40
became modules under ``tests/`` that import the package directly (pass 20d-1),
45 became ``oncotriage/fixtures/capture.py`` (pass 20c-3d), and 05 became a thin
entry point in this pass. Every one of the 45 names, and the string
"07- FHIR Parser.py" itself, was grepped across every .py, .md, .toml and .yml
in the tree; what is left is prose.

The argument the shim's docstring carried -- that the four module-level Counters
are shared objects rather than copies, so a caller reading one after
``load_all_patients()`` sees that run's numbers -- moved into
``oncotriage/fhir/parser.py``, where the Counters are.

NO EXEC BOOTSTRAP, and that is what makes this block work at all. Before pass
2b the definitions above it needed ``Dict``, ``Counter``, ``relativedelta`` and
a dozen more names that only "01- Imports.py" bound, so running this file
directly died at the first annotated assignment. The parser imports its own
dependencies now.

Run from terminal:
    python "07- FHIR Parser.py"
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py" and "16- Database Query.py".
# `pip install -e .` from 03- Code/ makes it a no-op; without it the code
# directory is added to sys.path and the fact is printed rather than left
# silent.
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


# Call the parser
#----------------
# data_fhir_path is imported INSIDE the guard, deliberately. It is a lazy
# attribute on oncotriage.paths and reading it resolves the sibling data tree,
# so a module-scope `from oncotriage.paths import data_fhir_path` would make
# merely LOADING this file glob a directory that a wheel install or a fresh CI
# checkout does not have. That is the exact hole pass 20c-2c found in
# oncotriage/registries/mesh.py. Inside the guard it resolves only on a real run.
#
# The shim this file replaced printed WHICH of two sources data_fhir_path came
# from -- the shared exec namespace, or oncotriage.paths -- because there were
# two. There is one now, so there is nothing to disambiguate and nothing to log.
if __name__ == '__main__':

    from oncotriage.paths import data_fhir_path
    from oncotriage.fhir.parser import load_all_patients

    print(f"[07] data_fhir_path: {data_fhir_path}")

    # Load all patients
    all_patients = load_all_patients(data_fhir_path)
    print(f"\nTotal patients loaded: {len(all_patients)}\n")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb  9 19:42:33 2026

@author: ramyalsaffar
"""
