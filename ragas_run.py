# Ragas Evaluation Run
######################

"""Score a recorded evaluation run with reference-free Ragas metrics. Entry
point.

THIS COSTS MONEY, ON THE OPENAI API, AT STANDARD (NON-BATCH) RATES. Ragas
drives the judge synchronously -- one request at a time inside each metric --
so the Batch API that ``rater_run.py`` uses is not available here and its 50%
discount does not apply. ``--dry-run`` builds both datasets, counts
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

THE JUDGE AND THE EMBEDDER ARE NOW THE SAME VENDOR, AND THAT IS NOT A LOSS OF
SEPARATION. The judge is ``gpt-5.6-terra`` on OpenAI; the embedder is
``config.EMBEDDING_MODEL``, also on OpenAI, and was always so. The property
that matters is that NEITHER is the family that wrote the text under audit --
the classifier is Claude Sonnet 4.6 on Bedrock -- and it is CHECKED rather than
claimed: see ``oncotriage/evaluation/judge_independence.py``, which compares
families rather than model strings, because a Claude on Bedrock is still an
Anthropic model.

WHAT IT DOES NOT TOUCH. It re-runs no pipeline stage, opens no database, reads
no characterization fixture and writes nothing inside this repository. It reads
an evaluation run directory and writes two JSON files beside it.

RAGAS IS NOT A PIPELINE DEPENDENCY AND IS NOT IN pyproject.toml. Installing it
into the project environment would drag ``openai`` from 1.x to 2.x and bump
``langgraph``, both of which the pipeline depends on. Run this file from an
isolated environment that has ragas and openai installed; the harness imports
ragas lazily, inside function bodies, so importing
``oncotriage.evaluation.ragas_harness`` loads no part of ragas -- which is what
lets ``--help`` and ``--dry-run`` run in the project environment, where ragas is
absent. ``anthropic`` is no longer needed on this path at all.

THE INSTALLED RAGAS CANNOT MAP THIS MODEL'S PARAMETERS AND THE HARNESS REPAIRS
IT. ragas 0.4.3 decides whether a model is a reasoning model by ``int()``-ing
the text between ``gpt-`` and the next dash; for ``gpt-5.6-terra`` that is
``"5.6"``, which raises and is swallowed, so ragas leaves ``max_tokens``
un-renamed and leaves ``temperature`` and ``top_p`` in place -- all three of
which this model rejects. ``build_judge`` performs the mapping ragas would have
performed, then ASSERTS the result, so a future ragas that changes its
behaviour produces a named refusal rather than a run that 400s on every
sample. It does load ``openai``, and this line used
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

WHICH RECORDED TEXT IS SCORED (``--response-field``). At PROMPT_VERSION 1.5.0
the stored ``assessment`` stopped being the model's prose and became text
COMPOSED from the trial's own criterion rows, quoting both of the sample's
contexts verbatim -- so faithfulness over it is expected near its ceiling and
measures the RENDERER. The model's own prose survives in the run artifact as
``assessment_draft``; scoring that measures the MODEL. The default does not
move, both readings are wanted, the manifest records which was scored, and the
outputs are NAMED after the field so two passes cannot overwrite each other. A
verdict lacking the selected field is a refusal, never a fallback to the other
one -- a mixture of the two under one metric name is a mean about nothing.

USAGE
-----
    python ragas_run.py --dry-run                # free: counts and a range
    python ragas_run.py --limit 3                # COSTS MONEY: a smoke run
    python ragas_run.py                          # COSTS MONEY: the full run
    python ragas_run.py --output-dir <scratch>
    python ragas_run.py --max-workers 8
    python ragas_run.py --response-field assessment_draft   # score the model
    python ragas_run.py --overwrite              # replace an existing result
    python ragas_run.py --resume                 # finish an interrupted run

RESUME. Every completed (sample, metric) pair is written to
``ragas_partial.json`` beside the outputs as it lands, atomically, so a kill
loses only the pairs in flight. ``--resume`` carries those pairs forward instead
of re-judging them -- provided the partial file's environment (the four package
versions, judge model, temperature, max tokens, embedding model, response field
and run directory) matches this run's, and provided each pair's own recorded
input hash still matches the text the metric would be handed today. An
environment difference is a REFUSAL naming what moved: scores from two
environments are a mean about nothing. The partial file is deleted when a run
completes cleanly and KEPT when a post-check fails, so fixing a post-check and
re-running costs nothing. On a resumed run ``cost`` and ``wall_seconds`` in the
manifest cover THIS invocation only, and ``cost_scope`` says so.

Exit codes:
    0 -- every (sample, metric) pair was scored and the outputs were written
    1 -- refused before spending anything (bad run dir, missing credentials,
         an unpriced model, a judge that could not be wired safely, a verdict
         missing the selected --response-field, an existing output that
         --overwrite was not given for, or a --resume whose partial score file
         was written under a different environment)
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
    from oncotriage import config
    from oncotriage.control import EXIT_LOCKED
    from oncotriage.evaluation.ragas_harness import (dispatches_billed_calls,
                                                     main)
    from oncotriage.observability import console
    from oncotriage.provider_resilience import (
        AlreadyPacing,
        ScopeLockUnavailable,
        exclusive_scope_lock,
        scope_lock_refusal_lines,
        scope_lock_unavailable_lines,
    )

    # ── ONE PROCESS PER PROVIDER ALLOWANCE, AND THIS HARNESS DRAWS ON TWO ──
    #
    # THE JUDGE AND THE EMBEDDER ARE DIFFERENT ENDPOINTS WITH DIFFERENT
    # ALLOWANCES, so this is two locks rather than one:
    # `ragas_judge` -> `openai:chat-completions`, shared with Stage 5's OpenAI
    # arm, and `ragas_embedding` -> `openai:embeddings`. Taking one would leave
    # the other endpoint unguarded; taking a single "ragas" lock would be a
    # third name for two allowances, which is the defect the bucket table
    # exists to remove.
    #
    # BOTH ARE TAKEN UNCONDITIONALLY RATHER THAN ONLY WHEN AN EMBEDDER WILL BE
    # BUILT, AND THAT IS A DELIBERATE OVER-REFUSAL. Whether one is built
    # depends on which metrics are selected, which `main()` decides tens of
    # lines in -- so asking here would mean a second copy of that derivation
    # (`METRICS_NEEDING_EMBEDDINGS` against the parsed `--metrics`) in a file
    # that must not import ragas. The cost is that a judge-only run also holds
    # the embeddings allowance; the cost of getting the copy wrong is an
    # unguarded endpoint, which is the failure this lock exists to stop.
    #
    # ORDERED, AND THE ORDER IS THE ONE THING TWO LOCKS MUST AGREE ON. Two
    # programs taking the same pair in opposite orders deadlock -- except that
    # this flock is NON-BLOCKING, so the second is REFUSED rather than hung.
    # Sorting the scope names makes every future taker of a pair agree by
    # construction rather than by everyone remembering.
    #
    # NOT TAKEN FOR A DRY RUN: it issues no request, and its resume preview is
    # the one free way to see what a resume would cost.
    if not dispatches_billed_calls():
        sys.exit(main())

    import contextlib

    _SCOPES = sorted((config.PROVIDER_QUOTA_SCOPE_RAGAS_JUDGE,
                      config.PROVIDER_QUOTA_SCOPE_RAGAS_EMBEDDING))

    try:
        with contextlib.ExitStack() as _stack:
            for _scope in _SCOPES:
                _held = _stack.enter_context(exclusive_scope_lock(_scope))
                console.out(f"[Pacing] Provider allowance held for "
                            f"{_scope}: {_held}")
            sys.exit(main())
    except AlreadyPacing as _pacing:
        # EXIT_LOCKED, on `25- Batch Runner.py`'s stated reason. The refusal
        # names the SCOPE, which matters more here than anywhere else: the
        # judge's allowance is SHARED WITH STAGE 5's OpenAI arm, so the holder
        # this names may be a campaign rather than another ragas run.
        console.out()
        for _line in scope_lock_refusal_lines(_pacing):
            console.out(_line)
        sys.exit(EXIT_LOCKED)
    except ScopeLockUnavailable as _pacing_error:
        # The lock could not be attempted; waiting does not fix it. 1 is what
        # every other refusal in this harness returns.
        console.out()
        for _line in scope_lock_unavailable_lines(_pacing_error):
            console.out(_line)
        sys.exit(1)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug 11 2026

@author: ramyalsaffar
"""
