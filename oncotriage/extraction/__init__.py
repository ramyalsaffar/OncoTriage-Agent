"""Rule-based, zero-LLM extraction from trial criteria and patient records.

Item 20c, pass 2a. This is "10- Structured Eligibility Extractor.py", split
three ways:

    negation    _is_negated and the three constants only it reads
    stage       stage requirement extraction (File 10 up to line 698)
    histology   histology tag extraction (File 10 from line 699)

THE SPLIT IS A MEASURED FACT, NOT A JUDGEMENT CALL. Every top-level definition
in each half was walked with ast for Name loads resolving to a top-level
definition in the other half. Exactly one edge exists in either direction:
``_is_histology_negated`` calls ``_is_negated``. That single shared helper is
what ``negation`` holds, and both halves import it.

If a second shared name ever turns up, it belongs in ``negation`` too — or the
boundary was drawn in the wrong place and should be re-measured rather than
patched.

Nothing here reads a config constant, a path, a file or a client. Importing any
of the three compiles regexes and stops.
"""

__all__ = ["negation", "stage", "histology"]


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
