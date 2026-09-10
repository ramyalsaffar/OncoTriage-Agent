# Shared Harness for the Operator-Control Tests
###############################################

"""What the five lock/stop test files were each carrying a private copy of.

NO ``test_`` PREFIX, DELIBERATELY. Every runner this project has -- the CI
bucket table's ``--run A``, ``pytest tests/``, a ``for f in tests/test_*.py``
loop -- selects on that prefix, and this file holds no checks. It would report
"0 passed" and be counted as a file that ran, which is the shape a test suite
learns to ignore. ``ci_test_buckets.py``'s completeness check selects on the
same prefix, so this file is correctly outside its table rather than a hole in
it.

WHAT IT OWNS, AND WHY EACH ITEM WAS WORTH MOVING
------------------------------------------------
``CLOSED_PORT_URL``   Five files wrote ``"http://127.0.0.1:1"`` out by hand as
                      the no-spend backstop that does not depend on a stand-in
                      working. Five copies of a magic string is five chances to
                      typo one into a port something is LISTENING on, and the
                      symptom of that typo is a test that quietly makes a real
                      request.

``wait_for``          Four files had the same deadline loop as a method on
                      their own ``Run`` class, and each had to remember the
                      same non-obvious detail: a loop that only tests the
                      predicate hangs for the whole timeout when the process
                      being waited on has already DIED, so the answer has to be
                      "re-check once and give up" rather than "keep waiting".

THE PARK PROTOCOL     Three files parked a worker so that a started-count is a
                      statement about CANCELLATION rather than about the
                      scheduler -- and they did it three incompatible ways:
                      ``ONC_PARK`` as ``"1"``/``"0"``, ``ONC_PARK`` as
                      ``"yes"``/``"no"``, and ``ONC_PARK_PHASE`` as a phase
                      name with ``"none"`` for never. Two of them read the SAME
                      variable with different vocabularies, so a hook copied
                      from one file into the other would park on ``"no"`` --
                      truthy, and exactly wrong. One protocol, one vocabulary.

THE PROTOCOL
------------
``ONC_PARK`` names WHICH phase parks. ``PARK_NONE`` never parks; ``PARK_ALL``
parks in every phase; anything else parks only in the phase whose name matches.
The phase-name form is the general one and the other two are its endpoints,
which is why it is the one that shipped: the batch runner has two passes (main
and resample) and a stop must be measurable in each separately, while the
preflight and ablation harnesses have one and want it always.

``park()`` runs in the CHILD, inside the ``usercustomize`` stand-in. It writes
``ONC_READY`` when the FIRST worker of the parked phase arrives -- so the parent
can wait for saturation rather than sleeping -- and then blocks until
``ONC_RELEASE`` appears or ``ONC_CAP`` seconds pass. The cap is a DEADLOCK
GUARD, not a timing knob: a test that dies without releasing must not hang the
suite forever.

WHY PARKING AND NOT SLEEPING. The first version of one of these harnesses slept
instead, and was measured FLAKY under bucket-A load: the started count then
measured how fast the machine was rather than whether the shipped code
cancelled anything.

THIS FILE IMPORTS NOTHING FROM THE PROJECT, and that is required rather than
tidy: it is imported by ``usercustomize.py`` stand-ins that run at INTERPRETER
STARTUP, before the entry point under test has done anything, and an import of
``oncotriage`` there would change what the process under test had already
loaded before its own first line.
"""

import os
import threading
import time


_SUBMIT_LOCK = threading.Lock()
"""Serializes appends to the submission ledger.

The batch runner submits from one thread, so this is not load-bearing today; it
is what keeps the ledger correct if a future caller ever submits from several,
and it costs one uncontended acquire per submission.
"""


CLOSED_PORT_URL = "http://127.0.0.1:1"
"""Where a subprocess under test is pointed so no request can succeed.

THE NO-SPEND BACKSTOP THAT DOES NOT DEPEND ON A STAND-IN WORKING. Every one of
these harnesses replaces the graph, the index build and the pipeline with
stand-ins, so no billed call should be reachable -- and "should" is the word
this closes. Port 1 is reserved, is not in the ephemeral range, and needs
privilege to bind, so a connection there is refused rather than answered.

It is handed to the child as ``ONCOTRIAGE_QDRANT_URL``, which
``oncotriage/settings.py`` resolves ahead of the credentials file: so even a
child whose hook failed to install reaches a dead endpoint rather than the
production cluster.
"""


# --- the park protocol ------------------------------------------------------

ENV_PARK = "ONC_PARK"
ENV_READY = "ONC_READY"
ENV_RELEASE = "ONC_RELEASE"
ENV_CAP = "ONC_CAP"
"""The four wire names. THREE OF THEM ARE READ ONLY IN THIS FILE AND THAT IS THE
DESIGN RATHER THAN DEAD CODE.

``park_env`` writes them and ``park`` reads them back, so a caller never touches
``ONC_READY``, ``ONC_RELEASE`` or ``ONC_CAP`` by hand -- which is exactly what
went wrong before: three files each wrote their own set and two of them gave
``ONC_PARK`` different vocabularies. ``ENV_PARK`` is the one exception and is
read outside, by the phase-selective hook in
``tests/test_runner_stop_switch.py``: that worker counts arrivals of ITS OWN
phase, so it has to know which phase was asked for before it decides whether to
park.

A future reader tidying the other three away would re-open the seam this file
closed, which is why the asymmetry is written down rather than left to look like
an oversight.
"""

PARK_NONE = "none"
"""Never park. The arm that proves a scenario's control is not the parking."""

PARK_ALL = "all"
"""Park in every phase. What a single-phase harness asks for."""

DEFAULT_CAP_SECONDS = 120.0
"""The deadlock guard, in seconds. See the module docstring: a cap, not a knob."""


def park_env(phase, ready, release, cap=DEFAULT_CAP_SECONDS):
    """The parent's half: the four variables a parked child reads.

    Returned as a dict to merge into the child's environment rather than
    exported here, because the parent runs several children with different
    control files and mutating ``os.environ`` would leak one scenario's release
    file into the next.
    """
    return {ENV_PARK: str(phase),
            ENV_READY: str(ready),
            ENV_RELEASE: str(release),
            ENV_CAP: str(cap)}


def park(phase="all", *, arrival=1):
    """The child's half: block if this phase is the parked one. Returns whether
    it parked.

    Args:
        phase: the phase this worker is in. Compared against ``ONC_PARK``.
        arrival: how many workers of this phase have arrived INCLUDING this one.
            The ready file is written on arrival 1 and only then, so the parent
            waits for the first parked worker rather than for a count it would
            have to guess. Callers that do not track arrivals pass 1 every time,
            which writes the file repeatedly and is harmless -- the parent only
            tests for existence.

    IT RETURNS RATHER THAN RAISING WHEN THE CAP EXPIRES. A worker that has
    waited out the deadlock guard has already failed the scenario; raising here
    would replace the scenario's own diagnosis -- a started count, a missing
    checkpoint -- with a traceback about the harness.
    """
    wanted = os.environ.get(ENV_PARK, PARK_NONE)
    if wanted == PARK_NONE or (wanted != PARK_ALL and wanted != phase):
        return False
    if arrival == 1:
        with open(os.environ[ENV_READY], "w", encoding="utf-8") as handle:
            handle.write("go")
    release = os.environ[ENV_RELEASE]
    deadline = time.time() + float(os.environ.get(ENV_CAP, DEFAULT_CAP_SECONDS))
    while not os.path.exists(release) and time.time() < deadline:
        time.sleep(0.01)
    return True


def release_park(release_path):
    """The parent's release gesture. One line, so no caller invents a second."""
    with open(release_path, "w", encoding="utf-8") as handle:
        handle.write("go")


# --- waiting on a subprocess ------------------------------------------------

def wait_for(predicate, seconds, alive=None, interval=0.02):
    """Poll ``predicate`` until it is true, the process dies, or time runs out.

    Args:
        predicate: called with no arguments; its truth is the answer.
        seconds: the deadline.
        alive: optional; called with no arguments and returning False once the
            process being waited on has exited.
        interval: the poll period.

    Returns:
        The predicate's final value -- NOT a bare timeout flag, so a caller can
        distinguish "it happened" from "it did not" without a second call.

    ``alive`` IS THE HALF THAT IS EASY TO FORGET AND EXPENSIVE TO OMIT. Without
    it, a wait for a marker a DEAD process was never going to write burns the
    whole timeout before answering -- and these harnesses wait on several
    markers per scenario, so a suite that ought to fail in a second takes
    minutes to say so. The predicate is re-tested ONCE after the process exits,
    because a process that wrote its marker and then exited between two polls
    would otherwise be reported as never having written it.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        if alive is not None and not alive():
            return predicate()
        time.sleep(interval)
    return predicate()


# --- the submission ledger --------------------------------------------------

ENV_SUBMITTED = "ONC_SUBMITTED"
"""Where the child records one line per future the batch pool has accepted.

WHY IT EXISTS, MEASURED RATHER THAN ARGUED. The park protocol above lets a
parent wait until the pool is SATURATED, which establishes that the queue is
non-empty -- and three of these harnesses' checks need a strictly stronger fact:
that the submit loop has FINISHED, so the queue holds every remaining patient.
``run_batch`` submits every future up front, but the parent has no way to see
that from outside: the runner prints nothing per submission, and a worker can
only start once its own future exists, so the started count saturates at
MAX_WORKERS and says nothing about the rest.

WITHOUT THE DISTINCTION THE COUNTS MEASURE THE SCHEDULER. A signal or a sentinel
delivered while the main thread is still inside the submit loop reaches a pool
holding only the futures created so far: the pre-fix drain then drains THOSE and
the check that expects the whole corpus fails, and the cancelled-count strings
report `len(futures) - MAX_WORKERS` where the check expects
`corpus - MAX_WORKERS`. MEASURED on this project: with a 4 ms delay injected
into the submit loop, six consecutive runs recorded 40, 14, 13, 40, 14 and 16
futures -- and in EVERY one the number of patients that ran equalled the number
of futures submitted, exactly. A hosted CI runner produced 15. The drain was
never at fault; the precondition was never waited for.
"""


def submit_env(path):
    """The parent's half: the one variable a counting pool reads.

    Returned as a dict to merge into the child's environment, for ``park_env``'s
    own reason -- several children per test, each with its own ledger file, and
    mutating ``os.environ`` here would leak one scenario's path into the next.
    """
    return {ENV_SUBMITTED: str(path)}


def record_submission(path):
    """The child's half: append one line for a future that now EXISTS.

    CALLED AFTER the real ``submit`` returns, never before, so the file can
    under-report a submission in flight and can never claim one that failed to
    be created. The parent's predicate is ``>=``, so under-reporting costs a
    little more waiting and never a false precondition.

    Opened and closed per line rather than held open: the parent reads this file
    by polling, and a buffered handle in the child would make a submission
    invisible until something happened to flush it -- which is the whole failure
    this ledger exists to remove.
    """
    with _SUBMIT_LOCK:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("1\n")


def count_submitted(path):
    """The parent's half: how many futures the pool has accepted so far.

    A missing file is 0 rather than an error: it does not exist until the first
    submission, and "none yet" is the honest reading of that.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def counting_executor(real_cls, path):
    """A ``ThreadPoolExecutor`` subclass that records each accepted submission.

    Args:
        real_cls: the executor class the module under test would otherwise use,
            passed IN rather than imported -- this file imports nothing from the
            project (see the module docstring) and must not start.
        path: the ledger file, from ``ENV_SUBMITTED``.

    IT IS A PURE OBSERVER. ``submit`` delegates to ``super()`` with the
    arguments untouched and returns the same future, on the same thread, in the
    same order; the only addition is one append per call. Nothing about
    scheduling, cancellation or shutdown changes, which is what lets the
    harnesses claim the pool under test is still the real one.

    ``*args, **kwargs`` RATHER THAN THE REAL SIGNATURE, deliberately:
    ``Executor.submit`` is ``(self, fn, /, *args, **kwargs)`` and restating it
    here would be a second copy of a signature that has already changed once
    across Python versions. Delegating wholesale cannot get it wrong.

    THE APPEND IS THE ONLY COST AND IT FALLS ON THE SUBMITTING THREAD, which
    makes the submit loop marginally slower -- so a harness that FORGOT to wait
    for the ledger is marginally more likely to lose the race, not less. That is
    the safe direction for an instrument whose absence is the defect it detects.
    """

    class _CountingExecutor(real_cls):

        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)
            record_submission(path)
            return future

    return _CountingExecutor


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug 24 2026

@author: ramyalsaffar
"""
