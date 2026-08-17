# Ragas Evaluation Run
######################

"""Score a recorded evaluation run with reference-free Ragas metrics. Entry
point.

THIS COSTS MONEY, ON THE ANTHROPIC API, AT STANDARD (NON-BATCH) RATES. Ragas
drives the judge synchronously -- one request at a time inside each metric --
so the Message Batches API that ``rater_run.py`` uses is not available here and
its 50% discount does not apply. ``--dry-run`` builds both datasets, counts
every judge and embedding call exactly, prices them as a range, and calls
nothing. It is free and needs no credentials.

WHAT IT MEASURES. Three reference-free metrics: without-reference context
precision over the retrieval side, and faithfulness plus response relevancy
over the generation side. Every one is a judge model's opinion about text.
None is a correctness measurement and none has been validated against a
clinician.

WHAT IS OUT OF SCOPE, AND SAID SO IN THE OUTPUT. Context recall needs labelled
reference contexts, which this project does not have; the scope note is a field
in ``ragas_manifest.json`` rather than an absence a reader has to notice.

THE ONE OPENAI CALL. Response relevancy scores cosine similarity between the
real question and questions the judge reverse-engineered from the response, so
it needs an embedding model -- ``config.EMBEDDING_MODEL``, on OpenAI. It is an
embedder, not a judge: it renders no verdict and reads no criterion, so the
different-family separation this harness exists to preserve is intact. Nothing
else here calls OpenAI.

WHAT IT DOES NOT TOUCH. It re-runs no pipeline stage, opens no database, reads
no characterization fixture and writes nothing inside this repository. It reads
an evaluation run directory and writes two JSON files beside it.

RAGAS IS NOT A PIPELINE DEPENDENCY AND IS NOT IN pyproject.toml. Installing it
into the project environment would drag ``openai`` from 1.x to 2.x and bump
``langgraph``, both of which the pipeline depends on. Run this file from an
isolated environment that has ragas, anthropic and openai installed; the
harness itself imports all three lazily, inside function bodies, so importing
``oncotriage.evaluation.ragas_harness`` loads neither ragas nor the Anthropic
SDK -- which is what lets ``--help`` and ``--dry-run`` run in the project
environment, where ragas is absent. It does load ``openai``, and this line used
to say it loaded none of the three: ``oncotriage/config.py`` does a
module-scope ``from openai import OpenAI`` and this harness imports ``config``
at module scope, so ``openai`` has always arrived transitively. That costs
nothing -- ``openai`` is a pipeline dependency and is present wherever this
repository runs -- but the claim was wrong, and
``tests/test_evaluation_ragas_manifest.py`` section 7 now pins the reading so
it cannot drift back into being right by accident.

EVERY RUN RECORDS THE ENVIRONMENT IT RAN UNDER, because nothing in this
repository pins it. ``ragas_manifest.json`` carries an ``environment`` block --
``sys.version``, ``sys.executable`` and the installed versions of ragas,
anthropic, openai and langchain-core, each read from distribution metadata and
recorded as ``absent`` when the distribution is not installed. ``--dry-run``
prints the same block. A ragas whose metric prompts or statement decomposition
have moved produces different scores, and without this the drift would be
indistinguishable from pipeline drift; faithfulness is already documented as
non-reproducible sample to sample, so the environment must not add a second
unrecorded source on top of a known one. In the project environment -- which
deliberately does NOT have ragas -- a dry run stamps ``ragas absent``, and that
is the correct record of the interpreter that produced the plan.

WHY THIS FILE IS NOT NUMBERED. Same reason as ``evaluation_run.py``,
``rater_run.py``, ``fixture_capture.py``, ``fixture_replay.py``,
``measure_medcpt_scores.py`` and ``mcp_server.py``: the numbered sequence says
what you can run in pipeline order, and this is an evaluation tool run by hand.

THIN ENTRY POINT. Every definition lives in
``oncotriage/evaluation/ragas_harness.py``, which documents the dataset
mapping, the judge wiring, the ``top_p`` removal that Claude 4 models require,
the usage seam that makes the reported cost measured rather than modelled, and
what each persisted field is for.

USAGE
-----
    python ragas_run.py --dry-run                # free: counts and a range
    python ragas_run.py --limit 3                # COSTS MONEY: a smoke run
    python ragas_run.py                          # COSTS MONEY: the full run
    python ragas_run.py --output-dir <scratch>
    python ragas_run.py --max-workers 8

Exit codes:
    0 -- every (sample, metric) pair was scored and the outputs were written
    1 -- refused before spending anything (bad run dir, missing credentials,
         an unpriced model, a judge that could not be wired safely)
    3 -- the run happened and some pairs are unscored, or a post-check failed;
         ragas_results.json names each unscored pair and why
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
    # here: reading this file must not import ragas, the anthropic SDK or the
    # OpenAI client, and `--help` must not either. main() parses its arguments
    # and prices the plan before it constructs anything.
    from oncotriage.evaluation.ragas_harness import main

    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug 11 2026

@author: ramyalsaffar
"""
