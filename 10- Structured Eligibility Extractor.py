# Structured Eligibility Extractor
##################################
#
# ITEM 20c, PASS 2a: THIS FILE IS A SHIM.
#
# The file split three ways on the way into the package:
#
#   oncotriage/extraction/negation.py   _is_negated and the three constants
#                                       only it reads (File 10 lines 124-184)
#   oncotriage/extraction/stage.py      stage requirements (up to line 698)
#   oncotriage/extraction/histology.py  histology tags (line 699 onward)
#
# Logic byte-for-byte unchanged in all three.
#
# THE SPLIT IS A MEASURED FACT. Every top-level definition in each half was
# walked with ast for Name loads resolving to a top-level definition in the
# other half. Exactly one edge exists, in one direction:
# _is_histology_negated() calls _is_negated(). That single shared helper is what
# negation.py holds, and both halves import it. A grep would not have settled
# this — it cannot tell a call from a mention in a docstring.
#
# Files 11 and 13 exec-chain this file; Files 30 and 33 chain it for their
# tests. Explicit, by name, never a star import.


#------------------------------------------------------------------------------


# The one shared helper, and the three constants only it reads.
from oncotriage.extraction.negation import (
    _CLAUSE_BOUNDARIES,
    _NEGATION_LOOKBACK,
    _NEGATION_PREFIXES,
    _is_negated,
)


# Stage requirement extraction. enrich_structured_eligibility() runs at
# index time (File 11); extract_patient_stage() / is_stage_mismatch() run
# at query time (File 13).
from oncotriage.extraction.stage import (
    _LOCALLY_ADVANCED_RE,
    _METASTATIC_RE,
    _NON_METASTATIC_RE,
    _NON_ONCOLOGY_CONTEXT_WINDOW,
    _NON_ONCOLOGY_STAGE_CONTEXT_RE,
    _PATIENT_STAGE_RE,
    _RANGE_RE,
    _SINGLE_RE,
    _SNOMED_DISPLAY_STAGE_RE,
    _STAGE_ALT,
    _STAGE_EXTRACTION_COUNTS,
    _STAGE_FULL_RANGE_MIN_CEILING,
    _STAGE_MAX_ORDINAL,
    _STAGE_MIN_ORDINAL,
    _STAGE_ORDINAL,
    _collect_stage_ordinals,
    _extract_accepts_metastatic,
    _extract_stage_from_text,
    _extract_stage_upper_bound_from_exclusion,
    _is_full_range_span,
    _is_non_oncology_stage,
    _stage_negated,
    enrich_structured_eligibility,
    extract_patient_stage,
    get_stage_extraction_stats,
    is_stage_mismatch,
    reset_stage_extraction_stats,
)


# Histology tag extraction. Same index-time / query-time pair.
from oncotriage.extraction.histology import (
    _ADENOCARCINOMA_RE,
    _EXCLUSIVE_PAIRS,
    _HISTOLOGY_EXTRACTION_COUNTS,
    _HISTOLOGY_SUFFIX_WINDOW,
    _LUNG_CONTEXT_RE,
    _NEGATION_SUFFIXES,
    _NEUROENDOCRINE_RE,
    _NON_MORPH_LOOKBACK,
    _NON_MORPH_PREFIX_RE,
    _NON_SMALL_CELL_RE,
    _NSCLC_ABBREV_RE,
    _SCLC_ABBREV_RE,
    _SMALL_CELL_RE,
    _SQUAMOUS_RE,
    _TRACHEAL_RE,
    _extract_histology_tags,
    _find_exclusive_pair,
    _has_affirmative_match,
    _has_conflict,
    _is_histology_negated,
    enrich_histology_tags,
    extract_patient_histology,
    get_histology_extraction_stats,
    is_histology_mismatch,
    reset_histology_extraction_stats,
)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Mar  1 2026

@author: ramyalsaffar
"""
