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


# ===========================================================================
# THE ONE CANCER GROUPING VOCABULARY
# ===========================================================================
#
# WHAT WAS HERE BEFORE: TWO VOCABULARIES FOR ONE CONCEPT, AND THE OLDER ONE HAD
# GONE STALE WITHOUT ANYTHING FAILING.
#
#   * `oncotriage/ablation/study.py:_cancer_group_key` -- fifteen anatomical
#     groups plus "other", used to stratify the ablation draw.
#   * `oncotriage/evaluation/sampling.py:classify_cancer` -- THREE groups
#     (breast, colon, lung) plus "other", used to build the evaluation extract
#     and, through `evaluation/medcpt_calibration.py`, the pool
#     `config.MEDCPT_SCORE_FLOOR` was calibrated over.
#
# The three-group vocabulary was fitted to a retired corpus in which "other"
# was one patient. On the corpus this project runs today "other" is 289 of
# 1,000 -- every prostate, myeloma and leukaemia patient -- so the evaluation
# sampler was drawing from 71% of the corpus and calling the rest ungrouped,
# and at a cohort of a few hundred its fixed ten-per-group draw RAISED on the
# lung stratum. Nothing detected either: a classifier that answers "other" is
# not a classifier that errors, and the raise arrived as a ValueError about
# patient counts rather than about a vocabulary.
#
# TWO GROUPERS FOR ONE CONCEPT IS THE DRIFT DEFECT THIS PROJECT KEEPS FINDING
# -- `CROSS_ENCODER_MODEL`, `"Qdrant/bm25"`, `_LATEST_RUN_PER_CONFIG_SQL`,
# `_RUN_HEALTH_CASE_SQL` -- and the answer here is the same one: one owner, and
# an AST pin that no second vocabulary survives.
#
# WHY THIS MODULE OWNS IT. The question "which broad group is this cancer in"
# is a projection of "which condition is THE cancer", which is what this module
# already answers, and the two consumers may not import each other:
# `oncotriage/ablation/study.py` imports the agent, the graph and the storage
# layer, and `oncotriage/evaluation/sampling.py` opens a SQLite database -- an
# import edge either way would put one of those in the other's graph for a
# keyword table. `oncotriage.registries` is the layer both already sit above,
# it imports nothing from either, and `cancer_group_key` needs no registry at
# all. It is NOT in `cancer_code_registry.py` for the reason this module's own
# header gives: that file's SOURCE TEXT is the corpus two tests read, and this
# is not a code table.
#
# THE VOCABULARY IS `oncotriage/ablation/study.py`'s, MOVED AND NOT REWRITTEN.
# The ordered keyword list below is that function's, byte-for-byte, including
# its ordering -- which is load-bearing, because the lists overlap ("small cell
# lung carcinoma" matches `lung` before it could match anything else) and a
# reordering would silently regroup patients. It was chosen over the
# three-group list because it is the one that is not stale, and widening the
# narrow one to five groups would have been inventing a third vocabulary.

CANCER_GROUP_KEYWORDS = (
    ("lung",        ("lung", "pulmonary", "bronch", "nsclc", "sclc")),
    ("breast",      ("breast",)),
    ("colorectal",  ("colon", "rectal", "rectum", "colorectal")),
    ("prostate",    ("prostate",)),
    ("pancreatic",  ("pancrea",)),
    ("ovarian",     ("ovary", "ovarian")),
    ("uterine",     ("uterus", "uterine", "cervix", "cervical")),
    ("hematologic", ("leukemia", "leukaemia", "lymphoma", "myeloma")),
    ("melanoma",    ("melanoma",)),
    ("liver",       ("liver", "hepato", "hepatic")),
    ("kidney",      ("kidney", "renal")),
    ("bladder",     ("bladder",)),
    ("thyroid",     ("thyroid",)),
    ("brain",       ("brain", "glioma", "glioblastoma")),
    ("head_neck",   ("oropharyn", "oral cavity", "head and neck")),
)
"""The ordered (group, keywords) table. FIRST MATCH WINS and the order matters.

A TUPLE OF TUPLES RATHER THAN A DICT OF LISTS, which is what
``oncotriage/ablation/study.py`` built per call. Ordering is the semantics here,
and a mutable module-level table is the hazard
``tests/test_package_invariants.py`` section 6a exists for one package over --
``MATCH_TIERS`` and ``MATCH_TIER_COLORS`` are checked for mutation because a
module-level list mutated once leaks into every later caller in the process.
"""

CANCER_GROUP_OTHER = "other"
"""A resolved cancer display that matches no keyword set.

DISTINCT FROM ``CANCER_GROUP_UNRESOLVED`` and the distinction is the whole
reason both names exist: "we found this patient's cancer and it is not one of
the fifteen" and "we could not find this patient's cancer at all" send a reader
to two different places, and collapsing them is how the old three-group
vocabulary hid 289 prostate, myeloma and leukaemia patients inside one bucket.
"""

CANCER_GROUP_UNRESOLVED = "unknown"
"""No primary cancer condition could be resolved for this patient at all."""

CANCER_GROUPS = tuple(name for name, _ in CANCER_GROUP_KEYWORDS) + (
    CANCER_GROUP_OTHER, CANCER_GROUP_UNRESOLVED)
"""Every value ``cancer_group_key`` or ``patient_cancer_group`` can return.

DERIVED FROM THE TABLE rather than written out, so a group added above joins
this tuple in the same edit. A consumer that branches exhaustively -- a
stratified draw reporting per-group counts, a test pinning the vocabulary --
reads this and cannot be one member behind.
"""

# A group name repeated in the table would make the second entry unreachable
# and its patients silently join the first. RuntimeError rather than assert:
# `python -O` deletes asserts, and this is a correctness guard. Same shape as
# `RUN_STOP_REASONS`' duplicate check in
# oncotriage/storage/database_logger.py and the seed guard in
# oncotriage/evaluation/cohort.py.
if len(set(CANCER_GROUPS)) != len(CANCER_GROUPS):
    raise RuntimeError(
        "CANCER_GROUP_KEYWORDS carries a duplicate group name, or a group is "
        "named identically to CANCER_GROUP_OTHER / CANCER_GROUP_UNRESOLVED. "
        "The second entry would be unreachable and its patients would be "
        "counted under the first.")


def cancer_group_key(display: Optional[str]) -> str:
    """Map a cancer DISPLAY NAME onto one broad anatomical group.

    A pure function of a string. It builds no registry, opens nothing and is
    the one place in this project that answers this question -- see the block
    above for what it replaced and why it is here.

    Args:
        display: the resolved primary cancer's display text. ``None`` and the
            empty string answer ``CANCER_GROUP_OTHER``, because a missing
            display is a cancer that matched no keyword rather than a patient
            with no cancer; the second state is
            ``patient_cancer_group``'s ``CANCER_GROUP_UNRESOLVED``.

    Returns:
        A member of ``CANCER_GROUPS``. Never ``None``.
    """
    if not display:
        return CANCER_GROUP_OTHER
    display_lower = str(display).lower()
    for group_name, keywords in CANCER_GROUP_KEYWORDS:
        if any(kw in display_lower for kw in keywords):
            return group_name
    return CANCER_GROUP_OTHER


def patient_cancer_group(patient: Dict, registry=None) -> str:
    """The cancer group of a PARSED PATIENT dict. ONE definition of "primary".

    IT DERIVES FROM ``_resolve_primary_cancer_condition`` AND THAT IS THE
    POINT. It was moved here byte-for-byte from
    ``oncotriage/ablation/study.py:_get_patient_group``, and the byte-for-byte
    move preserved a SECOND, DISAGREEING definition of which condition is the
    primary cancer:

        the resolver (used by the pipeline)     this function (used to sample)
        ------------------------------------   -------------------------------
        drops refuted / entered-in-error        did not -- a REFUTED cancer
        conditions before looking               could decide a patient's group
        falls back to the first VALID           answered "unknown"
        condition when no cancer is found

    So a patient could be STAGED, QUERY-EXPANDED and RECORDED in
    ``inferences.primary_condition`` by one condition and SAMPLED by another.
    That is this project's recurring drift defect in its most damaging form:
    the two answers agree on almost every patient, nothing fails when they
    disagree, and the disagreement lands in which patients a study measures.

    WHAT IT IS NOT. This does not simply return the resolver's answer, because
    the resolver's FALLBACK arm is not a cancer: when no condition passes
    ``is_primary_cancer`` it returns the first valid condition so that
    ``inferences.primary_condition`` records SOMETHING for a non-cancer patient
    that reached the pipeline. Grouping on that display would put a patient
    with no cancer into ``other`` -- "we found their cancer and it is not one
    of the fifteen" -- which is exactly the conflation
    ``CANCER_GROUP_UNRESOLVED`` exists to prevent. So the fallback arm is
    detected with the registry predicate the resolver itself used, and answers
    ``CANCER_GROUP_UNRESOLVED``.

        THE PREDICATE IS ASKED OF THE RESOLVER'S ANSWER, not re-derived over
        the condition list. There is one selection and one test of it; a second
        walk of the conditions would be a second definition again.

    MEASURED OVER THE WHOLE CORPUS, ONE PARSE: **0 of 1,000 patients change
    group**, and the drawn 500-patient cohort's membership digest is
    byte-identical before and after (``7ac166944199ea64``). THAT NUMBER IS NOT
    EVIDENCE THAT THE FIX DOES NOTHING, and the census that says why was run
    rather than assumed:

        53,040 conditions across the 1,000 bundles, every one of them
        `confirmed` (52,698) or `unconfirmed` (342). ZERO `refuted`, ZERO
        `entered-in-error`, and ZERO patients carrying conditions but no
        primary cancer.

    So NEITHER of the two inputs that separate the derivations occurs in this
    corpus at all -- Synthea does not generate a refuted diagnosis, and
    ``oncotriage/fhir/clean.py`` has already deleted every non-cancer patient.
    The disagreement was latent, not absent: it is one refuted diagnosis away
    on any real EHR extract, and on that day it would have staged a patient by
    one condition and sampled them by another with nothing failing. The
    constructed patients in
    ``tests/test_cancer_grouping_single_owner.py`` section 8 are the only
    thing that can exercise it, which is stated there.

    ONE ARM IS INHERITED RATHER THAN RE-DECIDED, and it surfaced when a check
    written to assert the opposite failed: when EVERY condition is refuted the
    resolver's step 1 falls back to the unfiltered list ("use all if filter
    empties list") and a refuted cancer decides the group after all. That is
    the resolver's own documented behaviour, and following it is what one
    definition MEANS -- second-guessing it here would recreate the divergence
    this function was changed to remove.

    Args:
        patient:  a parsed FHIR patient dict.
        registry: a ``CancerCodeRegistry``. ``None`` -- what every caller
            outside the ablation study passes -- resolves ``load_registry()``,
            the module's own thread-safe cached accessor. It is an ARGUMENT
            because the ablation study already holds one and resolving it per
            patient inside a 1,000-patient loop would take the construction
            lock a thousand times.

            It is deliberately NOT ``oncotriage.agent.deps.get_cancer_registry``
            when unsupplied: a stub installed for an agent test must not change
            which patients a draw selects. Same argument
            ``oncotriage/fhir/clean.py`` makes about the deletion path -- and
            the same one ``_resolve_primary_cancer_condition`` makes one
            function up, which is why the two now agree about that too.

    Returns:
        A member of ``CANCER_GROUPS``. Never ``None``.
    """
    primary = _resolve_primary_cancer_condition(patient.get("conditions", []))
    if primary is None:
        # No conditions at all. The resolver returns None only here.
        return CANCER_GROUP_UNRESOLVED
    if registry is None:
        registry = load_registry()
    if not registry.is_primary_cancer(primary):
        # The resolver's non-cancer fallback arm. See above.
        return CANCER_GROUP_UNRESOLVED
    return cancer_group_key(primary.get("display"))


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
