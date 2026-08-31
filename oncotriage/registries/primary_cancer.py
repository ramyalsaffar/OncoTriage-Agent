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
pass 2b left in File 14 -- ``tests/test_package_invariants.py`` re-derives that with
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
where it used to live. ``tests/test_fhir_birth_date_and_demographics.py``
section 9b calls it directly, in the one chain in the repository that loads the
storage logger without the agent, which is the chain where reading File 13's
global used to raise NameError.
"""

from typing import Dict, List, Optional

from oncotriage.constants import UNKNOWN_DATE
from oncotriage.registries.cancer_code_registry import load_registry


#------------------------------------------------------------------------------


def _resolve_primary_cancer_condition(conditions: List[Dict]) -> Optional[Dict]:
    """
    Identify the primary cancer CONDITION -- the whole dict, not a projection.

    THIS IS THE ONE DERIVATION. ``_resolve_primary_cancer`` below is
    ``.get("display")`` of what this returns and ``primary_cancer_onset_date``
    is ``.get("onset_date")`` of it, so the diagnosis the database records, the
    diagnosis the query expansion ran on and the diagnosis an ECOG observation
    is dated against cannot be three different conditions. The resolution order
    is unchanged from the display-only version this function was extracted from
    -- filter, classify, tiebreak -- and every step is still the registry's.

    Resolution order:
      1. Filter out refuted/entered-in-error conditions (verification_status)
      2. Filter to primary cancer conditions via CancerCodeRegistry (3-layer detection)
      3. Tiebreak: confirmed > unconfirmed, active > remission, most recent onset
      4. Return the winning condition

    Fallback: if no cancer condition is found (edge case for non-cancer patients
    that somehow entered the pipeline), returns the first valid condition -- the
    same fallback the display-only version has always taken, kept so the two
    cannot disagree about a non-cancer patient either. If the condition list is
    empty, returns None.

    The registry comes from load_registry(), the module's own thread-safe cached
    accessor, NOT from oncotriage.agent.deps: a stub installed for an agent test
    must not change which condition the parser dates an ECOG observation
    against. Same argument oncotriage/fhir/clean.py makes about the deletion
    path, one consumer over.
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
        return sorted(cancer_conditions, key=cancer_registry.sort_key)[0]

    # Fallback: no cancer found, return first valid condition
    return valid[0] if valid else None


def _resolve_primary_cancer(conditions: List[Dict]) -> Optional[str]:
    """
    Identify the primary cancer condition from a patient's condition list.

    Mirrors the exact logic used by node_query_expansion (13- LangGraph Agent.py,
    lines 460-471) so the database always records the same primary diagnosis
    that drove the pipeline's query expansion and trial matching.

    A PROJECTION OF ``_resolve_primary_cancer_condition`` since the ECOG
    pre-diagnosis pass, which needed the winning condition's onset date and
    could not get it from a display string. The resolution order and every
    fallback are that function's and are unchanged; this returns the display
    text of whatever it picked.

    Fallback: if no cancer condition is found (edge case for non-cancer patients
    that somehow entered the pipeline), returns the first condition's display.
    If the condition list is empty, returns None.
    """
    primary = _resolve_primary_cancer_condition(conditions)
    return primary.get("display") if primary is not None else None


def primary_cancer_onset_date(conditions: List[Dict]) -> Optional[str]:
    """
    Onset date of the primary cancer condition, as the parser wrote it.

    Returns the RAW string (``onsetDateTime``, or the ``onsetPeriod`` fallback
    ``oncotriage/fhir/parser.py:_parse_condition`` applies), or None when there
    is no primary condition or it carries no onset. It does NOT parse: the
    caller does, with ``oncotriage.utils.parse_partial_date``, which is this
    project's one date parser and the same one the caller uses on the other side
    of every comparison it makes. A second parser here would be a second
    convention with nothing failing when the two disagreed about a partial date.

    ``UNKNOWN_DATE`` IS NORMALISED TO None, and that is the point of the
    function rather than a courtesy. The parser writes the string "unknown" into
    ``onset_date`` when a Condition carries no onset at all, and "unknown" sorts
    lexically ABOVE every ISO date -- so a caller that took it as a date would
    read the least-known diagnosis in the corpus as the most recent one. That is
    the ``ecog_date`` trap ``oncotriage/storage/database_logger.py`` argues out
    at length, met one field over. The rule this function enforces is the one
    that column settled on: IT IS A DATE OR IT IS None.
    """
    primary = _resolve_primary_cancer_condition(conditions)
    if primary is None:
        return None
    onset = primary.get("onset_date")
    if not onset or onset == UNKNOWN_DATE:
        return None
    return onset


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
