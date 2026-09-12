# LLM Rater Run
###############

"""Have an independent, different-family LLM rate every criterion decision in
an evaluation run, then persist the ratings and an agreement summary. Entry
point.

THIS COSTS MONEY, ON THE OPENAI BATCH API -- a different FAMILY from the one
the pipeline calls, which is the point: a rater from the same family as the
classifier measures family agreement, not decision quality. Until 2026-09-08
this file said exactly that while the rater was Claude and the classifier had
moved to Claude, so the claim is now CHECKED at import and again before the
first billed request (`oncotriage/evaluation/judge_independence.py`) rather than
asserted here. Every criterion decision in
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

ANCHORED BY DEFAULT, BLIND WITH ``--blind``. Anchored shows the rater the
recorded status and asks agree/disagree, which leaks the answer and makes every
anchored figure an admitted UPPER BOUND; it is the default because the earlier
runs were rated that way and are comparable history. ``--blind`` withholds the
status: the rater assigns its own and agreement is computed offline. Blind runs
write to ``<run-dir>/rater_blind/`` and carry their own state file, so the two
cannot overwrite or resume each other. What blind rating still does NOT remove
-- chiefly that the quoted patient_value is the judged model's own extract -- is
stated in every summary.json it writes.

WHAT THE RATER IS NOT SHOWN. Trial title, trial phase, the trial-level verdict,
the match score, the assessment text, any rank or retrieval score, and the name
of the model that produced the decisions -- and in blind mode, the recorded
status itself. One criterion decision per request, in isolation.

NOTHING IS SUBMITTED UNTIL THE WORST CASE IS RESERVED. A batch reports no
usage until it is collected, so a cap checked afterwards cannot stop the batch
that broke it. Every chunk is priced at its maximum liability -- every input
token at the dearest input class, every reply at the full ceiling, at batch
rates -- and the submission is refused outright if that exceeds what the rater
budget has left.

WHAT IT DOES NOT TOUCH. It re-runs no pipeline stage, opens no database, reads
no characterization fixture and writes nothing inside this repository. It reads
an evaluation run directory and writes three JSON files beside it, plus one raw
JSONL per batch which it REFUSES to overwrite -- that file is the only
untransformed record of what was paid for. (It does call OpenAI now, which this
line used to say it did not: that changed with the judge, and the embedder was
never on this path at all.)

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
    python rater_run.py --resume batch_...        # poll/retrieve, no new spend
    python rater_run.py --submit --limit 40       # a cheap pilot
    python rater_run.py --submit --output-dir <scratch>

    # blind: the recorded status is withheld, agreement computed offline
    python rater_run.py --dry-run --blind --run-dir <run>
    python rater_run.py --dry-run --blind --retest-fraction 0.05 --run-dir <run>
    python rater_run.py --submit  --blind --retest-fraction 0.05  # COSTS MONEY

    # a named subset: one patient_id|nct_id|arm|index per line
    python rater_run.py --dry-run --blind --include-keys keys.txt --run-dir <run>

    # a population that SPANS TWO RUN DIRECTORIES, in ONE session and under
    # ONE budget. --run-dir is repeatable; the include list names decisions
    # from either. Two invocations would be two processes, and before
    # oncotriage/spend_journal.py existed they were two independent $50 caps.
    python rater_run.py --dry-run --blind --include-keys keys.txt \
        --run-dir <run A> --run-dir <run B>

**THE CAP IS CUMULATIVE ACROSS EVERY SESSION AND EVERY PROCESS.** Each
invocation seeds its ledger from ``oncotriage/spend_journal.py`` -- one
append-only file under ``09- Testing/Evaluation Runs/``, one exclusive lock per
write, one entry per collected batch keyed so that ``--resume`` cannot charge
the same money twice -- and prints what the judge has spent in total and what
remains of ``config.RATER_SPEND_CAP_USD`` before it submits anything. The
reservation gate is compared against THAT remainder.

``--resume`` REBUILDS THE REQUEST INDEX FROM THE RUN DIRECTORY, so a resumed
session must repeat every flag that shaped it -- ``--blind``,
``--retest-fraction``, ``--retest-seed``, ``--limit``, ``--include-keys``, and
EVERY ``--run-dir``. Forgetting one does not mis-join silently. Widening the index
(resuming a subset batch WITHOUT the flag) is caught by the state file's
recorded include-list sha256; narrowing it is caught by the join, because the
returned custom_ids would not be in the rebuilt index.

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
    from oncotriage import config
    from oncotriage.control import EXIT_LOCKED
    from oncotriage.evaluation.rater import dispatches_billed_calls, main
    from oncotriage.observability import console
    from oncotriage.provider_resilience import (
        AlreadyPacing,
        ScopeLockUnavailable,
        exclusive_scope_lock,
        scope_lock_refusal_lines,
        scope_lock_unavailable_lines,
    )

    # ── ONE PROCESS PER PROVIDER ALLOWANCE ─────────────────────────────────
    #
    # THE ALLOWANCE IS THE BATCH API's MANAGEMENT ENDPOINTS, NOT THE JUDGE's
    # INFERENCE QUOTA. `files.create`, `batches.create`, `batches.retrieve` and
    # `files.content` are governed by the account's request limits for those
    # endpoints; the inference the batch enqueues runs on the provider's own
    # schedule inside the completion window and is bounded by the batch caps
    # and the spend gate, not by anything here. That is argued at
    # `config.PROVIDER_QUOTA_SCOPE_OPENAI_BATCH`, and taking the SYNC
    # allowance instead would refuse a campaign for a scope this program never
    # touches.
    #
    # NOT TAKEN FOR A DRY RUN. `dispatches_billed_calls` asks this module's own
    # parser whether a spending mode was requested; a dry run issues no request
    # and must stay runnable while a live session holds the allowance. See that
    # function for why it is a parser call rather than a test on sys.argv.
    #
    # `contextlib.ExitStack` RATHER THAN A CONDITIONAL `with`, so there is ONE
    # call to main() and one exit path. Two branches each calling main() is two
    # places for the exit code to be got wrong.
    if not dispatches_billed_calls():
        sys.exit(main())

    import contextlib

    try:
        with contextlib.ExitStack() as _stack:
            _held = _stack.enter_context(exclusive_scope_lock(
                config.PROVIDER_QUOTA_SCOPE_OPENAI_BATCH))
            console.out(f"[Pacing] Provider allowance held for this session: "
                        f"{_held}")
            sys.exit(main())
    except AlreadyPacing as _pacing:
        # ANOTHER PROCESS ON THIS HOST IS ALREADY PACING THIS ALLOWANCE.
        # EXIT_LOCKED, on `25- Batch Runner.py`'s stated reason: the holder
        # finishes and the allowance frees itself, which is what that code
        # means. It does not collide with this program's own vocabulary -- 0,
        # 1, 2 and 3 are documented at the top of this file and 3 there means
        # "the run happened and some decisions are unrated", so the CONSOLE
        # LINE is what distinguishes them and it is unambiguous. Widening the
        # vocabulary for a refusal that spends nothing would be a contract
        # change to a program whose exit codes a person reads.
        console.out()
        for _line in scope_lock_refusal_lines(_pacing):
            console.out(_line)
        sys.exit(EXIT_LOCKED)
    except ScopeLockUnavailable as _pacing_error:
        # THE LOCK COULD NOT BE ATTEMPTED -- a different finding from "somebody
        # holds it", and waiting does not fix it. 1 is what every other refusal
        # in this program returns and carries the same standing: nothing was
        # submitted and nothing was spent.
        console.out()
        for _line in scope_lock_unavailable_lines(_pacing_error):
            console.out(_line)
        sys.exit(1)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug 11 14:20:00 2026

@author: ramyalsaffar
"""
