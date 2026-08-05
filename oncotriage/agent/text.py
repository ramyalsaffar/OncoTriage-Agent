"""BM25 tokenization, shared by index time and query time.

Item 20c, pass 2c: "13- LangGraph Agent.py" lines 72-112, verbatim.

Its own module because it is the smallest thing in the agent with the widest
blast radius. ``tokenize_for_bm25`` is applied at BOTH index time (File 11's
corpus build) and query time (Stage 2), and rank_bm25 does zero preprocessing --
tokens are compared as exact strings, so the two sides agreeing is not a
convenience, it is the whole contract. A module with one function and one
regex is harder to change on one side only than a function sitting halfway
down a 5,565-line file.

Imports nothing from the project. Importing it compiles one regex.
"""

import re
from typing import List


#------------------------------------------------------------------------------


# Pre-compiled regex for BM25 tokenization (module-level for performance)
_BM25_PUNCT_PATTERN = re.compile(r"[^\w\-]")  # keep word chars + hyphens


def tokenize_for_bm25(text: str) -> List[str]:
    """Tokenize text for BM25 indexing or querying.

    Applied at BOTH index time (corpus) and query time (search) to ensure
    consistent token matching. rank_bm25 does zero preprocessing — tokens
    are compared as exact strings, so "adenocarcinoma," ≠ "adenocarcinoma".

    Processing steps:
      1. Lowercase
      2. Split on whitespace
      3. Strip non-word characters from token boundaries
         (commas, periods, colons, parentheses, etc.)
      4. Discard empty tokens and pure-numeric tokens

    Preserves:
      - Hyphenated compounds: "instability-high", "HER2-positive"
      - Alphanumeric terms: "HER2", "BRAF", "BM25"

    Discards:
      - Empty tokens from consecutive delimiters
      - Pure numbers: "79", "18" (age values that match everywhere)

    Not applied:
      - Stemming: biomedical terms lose specificity
      - Stop-word removal: BM25 IDF handles this naturally
    """
    tokens = []
    for raw_token in text.lower().split():
        # Strip punctuation from boundaries, keep internal hyphens
        cleaned = _BM25_PUNCT_PATTERN.sub("", raw_token).strip("-")
        if not cleaned:
            continue
        # Discard pure-numeric tokens (age, years, doses — match everywhere)
        if cleaned.isdigit():
            continue
        tokens.append(cleaned)
    return tokens


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
