# LLM Rater Run
###############

"""Have an independent, different-family LLM rate every criterion decision in
an evaluation run, then persist the ratings and an agreement summary. Entry
point.

THIS COSTS MONEY, ON THE ANTHROPIC API -- a different vendor from the one the
pipeline calls, which is the point: a rater from the same family as the judge
measures family agreement, not decision quality. Every criterion decision in
the run is one billed request; the 10-patient run under
``09- Testing/Evaluation Runs/`` holds 2,212 of them. ``--dry-run`` builds every
request, prices it as a range, and submits nothing. It is free and it needs no
credentials.

WHAT IT MEASURES. An AGREEMENT rate, never an accuracy rate. The rater is a
second opinion with its own error, not ground truth, and nothing here decides
which of the two models is right. The rater judges under the SAME rules the
recorded decision was made under -- the rule sections are sliced verbatim out
of ``oncotriage/agent/prompts.py`` at run time -- so a disagreement is about the
decision rather than about a rubric the rater never agreed to.

WHAT THE RATER IS NOT SHOWN. Trial title, trial phase, the trial-level verdict,
the match score, the assessment text, any rank or retrieval score, and the name
of the model that produced the decisions. One criterion decision per request, in
isolation.

WHAT IT DOES NOT TOUCH. It calls no OpenAI endpoint, re-runs no pipeline stage,
opens no database, reads no characterization fixture and writes nothing inside
this repository. It reads an evaluation run directory and writes three JSON
files beside it.

WHY THIS FILE IS NOT NUMBERED. Same reason as ``evaluation_run.py``,
``fixture_capture.py``, ``fixture_replay.py``, ``measure_medcpt_scores.py`` and
``mcp_server.py``: the numbered sequence says what you can run in pipeline
order, and this is an evaluation tool run by hand.

THIN ENTRY POINT. Every definition lives in ``oncotriage/evaluation/rater.py``,
which documents the rubric lift, the request shape, the caching decision, the
bucketing of every failure mode and what each persisted field is for.

USAGE
-----
    python rater_run.py --dry-run                 # free: counts, tokens, cost
    python rater_run.py --dry-run --count-tokens  # free: measured token counts
    python rater_run.py --submit                  # COSTS MONEY
    python rater_run.py --resume msgbatch_...     # poll/retrieve, no new spend
    python rater_run.py --submit --limit 40       # a cheap pilot
    python rater_run.py --submit --output-dir <scratch>

Exit codes:
    0 -- every decision was rated and the outputs were written
    1 -- refused before spending anything (bad run dir, missing credentials,
         a rubric that could not be lifted, a reference-date mismatch)
    2 -- nothing to do (no mode flag), or the run happened and rated nothing
    3 -- the run happened and some decisions are unrated; ratings.json names
         each one and why
"""


# Run needed file
#----------------
# The six-line package bootstrap. `pip install -e .` makes it a no-op.
import os
import sys

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


if __name__ == "__main__":
    # Imported inside the guard, on the precedent of every other entry point
    # here: reading this file must not import the anthropic SDK, langgraph or
    # the OpenAI client, and `--help` must not either. main() parses its
    # arguments before it resolves anything.
    from oncotriage.evaluation.rater import main

    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug 11 14:20:00 2026

@author: ramyalsaffar
"""
