# LangGraph Agentic Patient-Trial Matching
##########################################

"""
LangGraph-Orchestrated Patient-Trial Matching — single-patient smoke run.

The pipeline itself is ``oncotriage/agent/``, twelve modules. Six stages, wired
by ``build_matching_graph()`` in ``oncotriage/agent/graph.py`` over
``TrialMatchState`` (``oncotriage/agent/state.py``):

  1  node_query_expansion        deterministic MeSH expansion, no LLM
  2  node_hybrid_retrieval       Qdrant-native BM25 + dense, fused by RRF
  3  node_cross_encoder_rerank   MedCPT, multi-query RRF, stable argsort
  4  node_rule_based_filter      MeSH site, stage, histology, age, sex, cost cap
  5  node_gpt4o_evaluation       one call, per-criterion verdicts, JSON retry
  6  node_finalize               split eligible / not_eligible / not_evaluable

Conditional edges route to ``node_no_candidates`` when a stage empties the pool,
and any exception lands in ``node_error_handler``, which still emits a
well-formed result. ``match_patient_to_trials(patient_data, graph)`` is the
public entry point and lives in ``oncotriage/agent/graph.py``.

THIN ENTRY POINT (item 20c pass 2c moved the logic; pass 20e removed the shim)
------------------------------------------------------------------------------
All 5,565 lines moved into the package in pass 2c. This file kept an 87-name
re-export shim because Files 12, 17, 25, 26, 31, 32, 35, 36, 37, 39, 40 and 45
exec-chained it and read those names out of the shared exec namespace. Pass 20e
measured all twelve and not one of them is a chainer any more: 12, 17, 25 and 26
became thin entry points (passes 20c-3a, 20c-3b, 20c-3d); 31, 32, 35, 36, 37, 39
and 40 became modules under ``tests/`` in pass 20d-1 and import the package
directly; 45 became ``oncotriage/fixtures/capture.py`` in pass 20c-3d. Every one
of the 87 names, and the string "13- LangGraph Agent.py" itself, was grepped
across every .py, .md, .toml and .yml in the tree; what is left is prose and two
diagnostic strings.

TWO MECHANISMS DIED WITH THE SHIM AND THEIR ARGUMENTS DID NOT. Both are now in
``oncotriage/agent/deps.py``, under "WHAT DIED WITH FILE 13'S SHIM":

  ``_LazyAgentDependency``  bound three model names in this namespace to proxies
                            so an exec-chain caller could read a NAME without
                            loading MedCPT. No exec-chain caller exists; every
                            consumer calls ``deps.get_medcpt_model()`` and
                            friends, which is lazier and cannot answer wrongly.
                            The rule it taught -- an implicit special method is
                            looked up on the TYPE, so a partial proxy lies about
                            bool / == / len / iter / in / repr -- is recorded
                            there for whoever writes the next proxy.
  ``_assert_no_legacy_``    refused to run if any of nine names in THIS
  ``rebinding()``           namespace had been rebound. There is nowhere left to
                            rebind: ``oncotriage.agent.deps`` is the only way to
                            redirect anything the agent reaches, its key set is
                            closed, and both fixture harnesses assert by
                            identity that what the agent got is theirs.

NO EXEC BOOTSTRAP. This file used to raw-exec "01- Imports.py" and
"02- Utility Functions.py" and then chain 03, 08, 09 and 10. All five of those
files are deleted by pass 20e.

Run from terminal (after setting RUN_TEST_ON_EXECUTE below):
    python "13- LangGraph Agent.py"
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


# ===========================================================================
# MAIN EXECUTION
# ===========================================================================
#
# A one-patient end-to-end run against the LIVE pipeline. IT COSTS MONEY: Stage
# 5 is a billed call, measured at $0.13 to $0.17 per patient over the rows in
# the production database. That is why the flag defaults to False and why the
# block is edit-to-arm rather than a command-line switch -- the same shape, and
# the same reason, as `Flag` in "15- Database Wipe All Tables.py".
# `fixture_replay.py` exercises twelve recorded patients through the same six
# stages and costs nothing.
#
# EVERY IMPORT IS INSIDE THE GUARD, AND INSIDE THE FLAG. Reading this file must
# not import langgraph, torch or transformers, must not open an OpenAI or a
# Qdrant client, and must not resolve the sibling data tree -- `results_path`
# and `data_fhir_path` are lazy attributes on oncotriage.paths and reading
# either one globs it. Under the old shim all of that happened at load, because
# the shim exec'd File 01.

RUN_TEST_ON_EXECUTE = False

if __name__ == "__main__" and RUN_TEST_ON_EXECUTE:

    import json
    from pathlib import Path

    from oncotriage.agent.display import display_match_results
    from oncotriage.agent.graph import build_matching_graph, match_patient_to_trials
    from oncotriage.config import Project_Name
    from oncotriage.fhir.parser import load_all_patients
    from oncotriage.paths import data_fhir_path, results_path

    print("\n" + "="*80)
    print(f"{Project_Name}: LangGraph Matching Agent")
    print("="*80 + "\n")

    # Step 1: Compile the LangGraph pipeline
    # BM25 index is now built at index time (File 11) and stored in Qdrant.
    # No in-memory BM25 index needed at inference time.
    graph = build_matching_graph()

    # Step 2: Load patients
    all_patients = load_all_patients(data_fhir_path)

    if not all_patients:
        print("No patients found. Run 07- FHIR Parser first.")
    else:
        # Filter to adult patients (age >= 18) for cancer trial matching
        adult_patients = [
            p for p in all_patients
            # age is None, not absent, when the bundle's birthDate was partial
            # beyond use or unparseable (File 07), so the key's default never
            # fires and the comparison would raise on None.
            if (p["demographics"].get("age") or 0) >= 18
        ]

        if not adult_patients:
            print("No adult patients found. Using first patient anyway.")
            test_patient = all_patients[0]
        else:
            test_patient = adult_patients[0]

        # Print patient details
        print(f"\n{'='*80}")
        print("PATIENT DEBUG INFO")
        print(f"{'='*80}")
        print(f"Patient ID: {test_patient['patient_id']}")
        print(f"Age: {test_patient['demographics'].get('age')} years")
        print(f"Sex: {test_patient['demographics'].get('sex')}")
        print(f"\nConditions ({len(test_patient['conditions'])} total):")
        for idx, condition in enumerate(test_patient["conditions"][:15], 1):
            print(f"  {idx}. {condition['display']} (onset: {condition.get('onset_date', 'unknown')})")

        unique_meds = list({med['display'] for med in test_patient['medications']})
        print(f"\nMedications ({len(unique_meds)} unique, {len(test_patient['medications'])} records):")
        for idx, med in enumerate(unique_meds[:10], 1):
            print(f"  {idx}. {med}")

        print(f"{'='*80}\n")

        # Step 3: Run matching pipeline
        result = match_patient_to_trials(test_patient, graph)

        # Step 4: Display results
        display_match_results(result)

        # Step 5: Save results
        output_file = Path(results_path) / f"match_result_{test_patient['patient_id']}.json"
        with open(output_file, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Results saved to: {output_file}\n")

#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
