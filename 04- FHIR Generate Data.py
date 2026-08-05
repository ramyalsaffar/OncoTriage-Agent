# Generate Synthea Patients with Cancer
#########################################

"""
Step 1: Generate synthetic cancer patients using Synthea
Calls Synthea JAR file via subprocess to generate FHIR patient data

THIN ENTRY POINT (item 20c, pass 3a)
------------------------------------
Everything this file used to define lives in ``oncotriage/fhir/generate.py``.
What is left is the ``__main__`` block and the imports it needs.

THERE IS NO exec() BOOTSTRAP ANY MORE, and no re-export shim either. Both were
dropped because nothing in the repository chains this file: every top-level name
it defined was grepped against every .py, .md, .toml and .yml in the tree and the
only hits are prose in CLAUDE.md and 'Exception and Fallback Audit.md', plus two
files (39 and 40) that print the COMMAND LINE below as a suggestion. So there is
no shared exec namespace to feed, and this file needs neither File 01's
third-party import block nor Files 02 and 03.

That makes it cheap in a way it was not: `--help` used to exec Files 01, 02 and
03 first, which imports torch, transformers, streamlit and langgraph, builds an
OpenAI client and a Qdrant client, and resolves the whole sibling data tree —
all to print an argument list. The ARGUMENT SURFACE is unchanged and that is
asserted: the argparse section of `--help` was captured before and after this
change and diffed byte for byte.

Also writes and loads a custom Generic Module Framework module that records an
ECOG performance status for cancer patients. See build_ecog_module() in the
package module, and ``oncotriage/config.py`` for the two uncalibrated holding
values that shape it.

Every run writes a JSON run manifest next to the generated data recording the
command, the Synthea JAR hash, the ECOG module filename and content hash, the
configured score distribution and missingness fraction, and what was actually
observed in the output. That manifest is the artifact a regeneration needs.
"""

import argparse
import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# `pip install -e .` from the code directory is the supported arrangement and
# makes this block a no-op. Without it, the package still has to be found: when
# this file is run as a script sys.path[0] is already its own directory, but
# that is not guaranteed for every launcher, so the directory is added
# explicitly when — and only when — the import fails. Which candidate won is
# PRINTED; a package found somewhere unexpected must not be silent. Same three
# candidates, in the same order and for the same reasons, as
# "01- Imports.py"'s _ensure_oncotriage_importable().
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

from oncotriage.config import Project_Name
from oncotriage.fhir.generate import POPULATION_SIZE, run_generation, write_ecog_module
from oncotriage.utils import CaffeinateSession


#------------------------------------------------------------------------------


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Generate Synthea patients with an ECOG performance status module."
    )
    parser.add_argument("--population", type=int, default=POPULATION_SIZE,
                        help=f"Population size (default: {POPULATION_SIZE})")
    parser.add_argument("--output-dir", default=None,
                        help="Synthea --exporter.baseDirectory. Defaults to the LIVE "
                             "corpus directory; pass a scratch path to leave it alone.")
    parser.add_argument("--seed", type=int, default=None,
                        help="Synthea -s population seed (default: Synthea's own)")
    parser.add_argument("--clinician-seed", type=int, default=None,
                        help="Synthea -cs clinician seed (default: Synthea's own). "
                             "Independent of --seed: Synthea draws its clinician / "
                             "provider population from a separate generator, so a "
                             "corpus is only reproducible when both are pinned.")
    parser.add_argument("--label", default=None,
                        help="Free-text label recorded in the run manifest")
    parser.add_argument("--module-only", action="store_true",
                        help="Write the ECOG module and exit without generating")
    parser.add_argument("--force", action="store_true",
                        help="Generate even if the output directory already holds "
                             "bundles. Synthea appends rather than replaces, so this "
                             "interleaves populations -- off by default.")
    args = parser.parse_args()

    print()
    print("╔═══════════════════════════════════════════════════════════════════════╗")
    print(f"║                   {Project_Name}: PATIENT GENERATION                  ║")
    print("╚═══════════════════════════════════════════════════════════════════════╝")
    print()

    if args.module_only:
        write_ecog_module()
        print()
        print("Module written. Generation skipped (--module-only).")
    else:
        with CaffeinateSession("Synthea Generation"):
            outcome = run_generation(
                population_size=args.population,
                output_dir=args.output_dir,
                seed=args.seed,
                clinician_seed=args.clinician_seed,
                label=args.label,
                force=args.force,
            )

        if outcome["success"]:
            stats = outcome["manifest"]["verification"]

            if stats and stats['total_files'] > 0:
                print()
                print("SUCCESS! Patients generated successfully.")
                print()
                print("NEXT STEP: Run the FHIR data clean script filter to only keep the cancer patient.")
                print()
            else:
                print()
                print("WARNING: Generation completed but no files found.")
                print("Check Synthea output for errors.")
                print()
        else:
            print()
            print("FAILED: Patient generation did not complete successfully.")
            print(f"Reason: {outcome['generation'].get('failure_reason')}")
            print("Please check error messages above.")
            print()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 10:15:45 2026

@author: ramyalsaffar
"""
