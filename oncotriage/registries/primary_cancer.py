"""Which of a patient's conditions is the primary cancer.

Item 20c, pass 2c. One function, moved out of
``oncotriage/storage/database_logger.py`` where it had no business living.

WHY IT IS HERE AND NOT IN storage
---------------------------------
``_resolve_primary_cancer`` is a DOMAIN question about SNOMED and ICD-10 codes;
it opens no database and knows nothing about one. It sat in File 14 only because
File 14 is where the answer was first needed, and the consequence was an import
edge pointing the wrong way: ``13- LangGraph Agent.py``'s three terminal nodes
call it, so the AGENT depended on the STORAGE layer for a registry lookup.

Now both callers import it from here:

    oncotriage/agent/terminal.py           node_finalize, node_no_candidates,
                                           node_error_handler
    oncotriage/storage/database_logger.py  log_inference's primary_condition
                                           fallback

and neither of them imports the other. The function is byte-identical to the one
pass 2b left in File 14 -- ``47- Package Split Test.py`` re-derives that with
``ast.unparse`` against git HEAD, so the move is provably a move.

WHY IT IS NOT IN cancer_code_registry.py
----------------------------------------
That module is the registry: the code sets, the three detection layers, the two
registry classes and their accessors. It is also the module whose SOURCE TEXT
Files 42 and 43 read -- 42 extracts the inline comment beside every code as the
claim under audit, 43 plants defects into it and hashes the restore. Adding a
consumer of the registry to the file those two tests treat as a code table would
put non-table content inside their reading frame for no benefit.

This module CONSUMES the registry through ``load_registry()``, the same
thread-safe cached accessor everything else uses. Importing it builds nothing;
the registry is constructed on the first call, which is when
``_build_icd10_cancer_sets()`` imports the ICD-10-CM release.

THE NAME KEEPS ITS LEADING UNDERSCORE. It is public API of this module in every
practical sense -- two packages and a re-export shim import it by name -- but
renaming it would break `08-`/`14-`-era callers reading it out of the shared exec
namespace for no gain, and the underscore is now the only remaining trace of
where it used to live. ``38- Birth Date and Demographics Parser Test.py``
section 9b calls it directly, in the one chain in the repository that loads the
storage logger without the agent, which is the chain where reading File 13's
global used to raise NameError.
"""

from typing import Dict, List, Optional

from oncotriage.registries.cancer_code_registry import load_registry


#------------------------------------------------------------------------------


def _resolve_primary_cancer(conditions: List[Dict]) -> Optional[str]:
    """
    Identify the primary cancer condition from a patient's condition list.

    Mirrors the exact logic used by node_query_expansion (13- LangGraph Agent.py,
    lines 460-471) so the database always records the same primary diagnosis
    that drove the pipeline's query expansion and trial matching.

    Resolution order:
      1. Filter out refuted/entered-in-error conditions (verification_status)
      2. Filter to primary cancer conditions via CancerCodeRegistry (3-layer detection)
      3. Tiebreak: confirmed > unconfirmed, active > remission, most recent onset
      4. Return display text of the winning condition

    Fallback: if no cancer condition is found (edge case for non-cancer patients
    that somehow entered the pipeline), returns the first condition's display.
    If the condition list is empty, returns None.

    The registry comes from load_registry(), the module's own thread-safe cached
    accessor. It used to be read as a bare _CANCER_REGISTRY out of the shared
    exec namespace, where "13- LangGraph Agent.py" assigns it at line 64 -- a
    layering violation that also meant this function raised NameError in any
    chain that loaded 14 without 13. load_registry() returns the same singleton
    File 13's own assignment gets, so a chain loading both is unaffected.
    """
    if not conditions:
        return None

    # Resolved once per call, not per condition: load_registry() takes a lock on
    # the first construction and the loops below would otherwise take it for
    # every element.
    cancer_registry = load_registry()

    # Step 1: Exclude refuted/entered-in-error
    valid = [
        c for c in conditions
        if (c.get("verification_status") or "unknown")
        not in cancer_registry.exclude_verification
    ]
    if not valid:
        valid = conditions  # fallback: use all if filter empties list

    # Step 2: Filter to primary cancer conditions
    cancer_conditions = [
        c for c in valid
        if cancer_registry.is_primary_cancer(c)
    ]

    # Step 3: Tiebreak and return
    if cancer_conditions:
        primary = sorted(cancer_conditions, key=cancer_registry.sort_key)[0]
        return primary.get("display")

    # Fallback: no cancer found, return first valid condition
    return valid[0].get("display") if valid else None


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
