"""Where output goes: the structured JSON logger, and the console UI channel.

ONE MODULE, TWO CHANNELS, AND THEY ARE NOT THE SAME THING.

    LOGGING is machine-readable. One JSON object per line, a severity, a
    correlation ID on every line, an allowlisted field set. Something greps it
    six months from now.

    CONSOLE UI is for a human watching a long run: the tqdm progress bar, the
    mid-run drift banner, the per-patient match report. Turning those into JSON
    makes a 22,000-patient run unwatchable, so they stay exactly as loud as they
    were.

Both live here because both answer the same question -- which stream does this
byte go to, and what has to happen to the progress bar first -- and because the
answer has to be ONE choke point. Two modules would need to import each other.


THE STREAM COLLISION, AND WHY THIS IS THE SHAPE
-----------------------------------------------
tqdm draws its bar on **stderr** by moving the cursor. Any other writer on
stderr while a bar is live interleaves with the redraw and the two shred each
other -- the bar's line is overwritten mid-frame and the log line loses its
head. That is the exact problem ``oncotriage/batch/runner.py`` was solving by
monkey-patching ``builtins.print``, and moving to a logger does not make it go
away: a ``logging.StreamHandler(sys.stderr)`` collides with a bar just as a
``print`` did.

STDOUT IS NOT AN OPTION. ``mcp_server.py`` speaks JSON-RPC over stdout, one
message per line, and one stray byte ends the session. Nothing this project
writes may land there. That rules out the obvious split (bar on stderr, logs on
stdout).

A THIRD STREAM IS NOT AN OPTION EITHER. Logs to a file or to fd 3 keeps the
terminal clean and takes the logs out of ``docker logs``, which is where a
containerised deployment reads them and half the reason this pass exists.

So: **both channels write to stderr, and the writer is bar-aware.** Everything
-- every log record and every console line -- funnels through ``_emit_line()``,
which routes through ``tqdm.write()`` while a bar is live and writes the stream
directly when one is not. ``tqdm.write()`` clears the bar, writes the line and
redraws, which is the mechanism the monkey-patch was borrowing. What changes is
that a caller no longer has to be tricked into it: the bar registers itself
here, through ``console.attach_bar()``, and the routing is a property of this
module rather than of a hijacked builtin.

WHY NOT JUST CALL ``tqdm.write`` UNCONDITIONALLY. It works with no bar live,
but it would put ``import tqdm`` in the leaf of this project's import graph --
this module is imported by ``oncotriage/paths.py``, which is imported by
everything -- for a dependency only two modules in the package actually draw a
bar with. The registration slot costs one attribute read per line and keeps
the ``import tqdm`` local to ``attach_bar()``.


WHY THE MONKEY-PATCH HAD TO GO, BEYOND TASTE
--------------------------------------------
The replacement it installed was::

    def _tqdm_print(*args, **kwargs):
        text = " ".join(str(a) for a in args)
        tqdm.write(text)

``**kwargs`` is accepted and DISCARDED. While that patch was live -- the whole
of a 22,000-patient batch -- every ``print(..., end="")`` in the process grew a
newline, every ``print(..., sep="")`` grew spaces, every ``print(...,
file=some_handle)`` was redirected to the terminal, and ``flush=`` did nothing.
Not in the batch runner: in every module, every library, every dependency, for
the life of the run. ``console.out()`` below honours all four.


THE CORRELATION ID IS A ``contextvars.ContextVar``
--------------------------------------------------
NOT a module-level global -- ``MAX_WORKERS`` is 12 and a global is one slot
twelve threads write to, so the ID on a line would be whichever patient was
most recently submitted rather than the one that emitted it.

NOT a field threaded through ``TrialMatchState`` either, and that is the choice
worth arguing. The state reaches the six graph nodes and nothing else, while the
lines that most need correlating are emitted BELOW them: the MedCPT load in
``agent/deps.py``, the alias resolution retry in ``utils.py``, the write in
``storage/database_logger.py``. Threading the ID into those means a signature
change on every one, and they would still have nothing to carry for a caller
that is not the agent.

A ContextVar isolates by construction, which is stronger than isolating by
discipline: **a thread starts with an empty Context**, so a value set on the
main thread is not visible in a worker, and a value set in a worker cannot be
read by its sibling. Measured, not assumed -- see
``tests/test_observability_logging.py`` section 3, which runs 12 threads through
the real accessor and asserts every line carries its own patient's ID.

``ThreadPoolExecutor`` reuses its workers, so a value merely SET would outlive
the task that set it and be inherited by the next patient on that thread.
``correlation_scope()`` is a context manager that resets its token in a
``finally``; nothing else should call ``set`` directly.

LINES THAT BELONG TO NO PATIENT -- startup, an index build, shutdown -- carry
``correlation_id: "-"`` (``NO_CORRELATION``). A documented sentinel, never a
missing key: a consumer that has to test ``"correlation_id" in record`` before
every read is a consumer that will forget once.


THE FIELD ALLOWLIST IS ENFORCED IN THE FORMATTER
------------------------------------------------
Not at the call sites. A call site can be added by anyone; the formatter is the
one place every record must pass through, including a record from a caller that
went around this module's helpers and used ``logging.getLogger("oncotriage.x")``
directly.

The reason is stated plainly because it will not be obvious on Synthea data: the
node prints this pass converts carried condition displays, lab values with
units, and a 300-character preview of the model's response. Printed to a
terminal that is transient. Piped into structured JSON, keyed by a correlation
ID, shipped to a log aggregator and retained -- that is a durable, searchable
clinical record, built by accident, and it is what this project's data terms
will later prohibit. Defining the allowlist now costs nothing; retrofitting it
onto a year of retained logs is not possible at all.

Anything not on the list is DROPPED, and its KEY NAME (never its value) is
reported in ``dropped_fields`` on the same record and counted in
``FIELD_DROPS``. Dropping silently would make a redaction indistinguishable
from a caller that forgot to pass the field.

**THE LIMIT, STATED RATHER THAN GLOSSED.** The allowlist governs structured
FIELDS. It cannot police the free-text ``message``, because by the time an
f-string reaches this module it is already a ``str`` and no longer distinguishable
from a constant. The convention that closes it is "the message is a template,
the data goes in fields", and it is enforced STATICALLY rather than left to
discipline: ``tests/test_observability_logging.py`` section 5c walks
``oncotriage/agent/`` by AST and fails on any logger call whose message argument
is an f-string (``ast.JoinedStr``).
"""

import contextlib
import contextvars
import json
import logging
import sys
import threading
import time
import uuid
from collections import Counter

from oncotriage.settings import resolve_log_level


#------------------------------------------------------------------------------


# ===========================================================================
# THE OUTPUT STREAM AND THE BAR-AWARE CHOKE POINT
# ===========================================================================

def _console_stream():
    """The one stream both channels write to.

    ``sys.stderr`` is read at CALL time, never captured at import. A test that
    installs ``contextlib.redirect_stderr`` (and this project's do) must see its
    own buffer, and a module-level ``_STREAM = sys.stderr`` would have bound the
    real one before the redirect existed.
    """
    return sys.stderr


_WRITER_LOCK = threading.RLock()
_ACTIVE_WRITER = None
"""Set to a ``tqdm.write``-shaped callable while a progress bar is live.

Read on every emitted line, written only by ``attach_bar()`` /
``detach_bar()``. A plain attribute
read is atomic under the GIL, so the read side takes no lock; the write side
does, because it is a read-modify-write on ``_WRITER_DEPTH`` beside it.
"""

_WRITER_DEPTH = 0
"""Nesting count for ``attach_bar()``. The writer is cleared when it hits 0."""


def _emit_line(text, end="\n", flush=True):
    """Put one line of text on the console stream, bar or no bar.

    EVERY byte this project writes -- console UI and structured log record
    alike -- comes through here. That is the whole design: one place decides
    what happens when a progress bar is live, so a log line and a ``console.out``
    line cannot disagree about it.
    """
    writer = _ACTIVE_WRITER
    if writer is not None:
        try:
            writer(text, end=end)
            return
        except Exception as exc:
            # A torn-down bar must not take the line with it. Counted rather
            # than swallowed, and the line still gets written below.
            EMIT_FAILURES[f"bar_writer:{type(exc).__name__}"] += 1

    stream = _console_stream()
    try:
        stream.write(text + end)
        if flush:
            stream.flush()
    except (OSError, ValueError) as exc:
        # A closed or broken stderr (a killed pager, a closed pipe) must not
        # raise out of a log call and kill the pipeline. Nothing is left to
        # write the diagnosis to, so the counter is the entire record.
        EMIT_FAILURES[f"stream:{type(exc).__name__}"] += 1


EMIT_FAILURES = Counter()
"""Times a line could not be written, keyed ``{where}:{ExceptionType}``.

The project's standing rule is that no exception is caught without being
re-raised or recorded. Neither of these two can be re-raised -- a broken output
stream must not kill a 22,000-patient run -- so they are counted. Read by
``emit_failure_report()``.
"""


def emit_failure_report():
    """A one-line summary of ``EMIT_FAILURES``, or ``None`` when it is clean."""
    if not EMIT_FAILURES:
        return None
    return (f"output was degraded: {dict(EMIT_FAILURES)} -- some console or log "
            f"lines could not be written")


#------------------------------------------------------------------------------


# ===========================================================================
# THE CONSOLE UI CHANNEL
# ===========================================================================

class _Console:
    """Human-facing output. Loud on purpose, unstructured on purpose.

    This is the channel for the things a person watching a run needs to see as
    they happen and would not grep for afterwards: the progress bar, the drift
    banner, the per-patient match report in ``agent/display.py``.

    It writes to **stderr**, which is a behaviour change from the ``print()``
    calls it replaces, and the change is required rather than incidental:
    ``mcp_server.py`` serves JSON-RPC on stdout, so a stray byte there ends a
    client session. Piping a run's stdout somewhere now yields nothing, and
    that is the point -- ``tests/test_observability_logging.py`` section 6
    captures stdout across a full pipeline run and asserts it is empty.
    """

    @staticmethod
    def out(*args, sep=" ", end="\n", file=None, flush=False):
        """``print()``, honouring every keyword ``print()`` honours.

        The signature is deliberately identical, because this is what the 1,100
        converted ``print`` calls became and a mechanical conversion that
        silently changed ``end=`` or ``sep=`` would be the monkey-patch's own
        defect reintroduced by hand.

        ``file=`` is honoured when it names a real destination -- one call site
        writes a report into an open handle -- and IGNORED when it names
        ``sys.stdout`` or ``sys.stderr``, which are requests for "the terminal"
        and are what this channel exists to answer.
        """
        text = sep.join(str(a) for a in args)
        if file is not None and file is not sys.stdout and file is not sys.stderr:
            try:
                file.write(text + end)
                if flush:
                    file.flush()
            except (OSError, ValueError) as exc:
                EMIT_FAILURES[f"explicit_file:{type(exc).__name__}"] += 1
            return
        _emit_line(text, end=end, flush=True)

    @staticmethod
    def banner(*lines):
        """A block that must stay visually loud even under a live bar.

        The mid-run drift banner in ``oncotriage/batch/runner.py`` is the reason
        this exists as its own name rather than as a loop over ``out()``: it is
        the one piece of console output whose whole job is to be impossible to
        miss, and it is emitted while a progress bar is redrawing. Routing it a
        line at a time through the same choke point keeps the bar intact
        between lines.
        """
        for line in lines:
            _Console.out(line)

    @staticmethod
    def attach_bar():
        """Tell this module a progress bar is now live. Returns a token.

        THE IMPERATIVE FORM, and it exists because the three call sites in this
        project create their bar in one place and close it in a ``finally``
        eighty lines later. A ``with``-statement form would be the safer shape
        and is a recorded follow-up -- see the note below ``detach_bar()`` for
        why it is not here yet; wrapping those three would have meant
        re-indenting the whole batch loop, which is a diff nobody can read in a
        pass whose promise is that only output routing changed.

        Pair it with ``detach_bar(token)`` in a ``finally``. It sits exactly
        where ``builtins.print = _original_print`` used to.

        The ``tqdm`` import is inside the function on purpose. CLAUDE.md's rule
        forbids a PACKAGE import in a function body and explicitly exempts a
        third-party one (``import icd10`` inside ``_build_icd10_cancer_sets``);
        this is the same case, and the reason is the same -- this module is a
        leaf that everything imports, and it must not drag tqdm in for the two
        modules that draw a bar.
        """
        global _ACTIVE_WRITER, _WRITER_DEPTH
        from tqdm import tqdm

        def _bar_writer(text, end="\n"):
            """``tqdm.write``, forced onto the console stream.

            THE ``file=`` IS THE WHOLE POINT AND LEAVING IT OFF IS A REAL BUG,
            found by running a bar rather than by reading. ``tqdm.write``'s
            signature is ``write(s, file=None, end="\\n")`` and it resolves
            ``file=None`` to **sys.stdout** -- so a bare ``tqdm.write`` sends
            every console line and every JSON log record to STDOUT for as long
            as a bar is live. That is the stream ``mcp_server.py`` serves
            JSON-RPC on, and it is the stream this pass promises is empty; a
            22,000-patient batch would have put a million lines on it.

            It is also a correctness bug about the bar itself, not only about
            the stream: ``tqdm(...)`` draws on stderr by default, so the
            clear-write-redraw dance would have cleared stderr and written the
            text to stdout -- the two halves of one operation on two different
            streams, which is precisely the shredding this module exists to
            prevent.

            The stream is resolved per call, never captured, so a test that
            redirects stderr sees its own buffer.
            """
            tqdm.write(text, file=_console_stream(), end=end)

        with _WRITER_LOCK:
            _WRITER_DEPTH += 1
            _ACTIVE_WRITER = _bar_writer
        return _WRITER_DEPTH

    @staticmethod
    def detach_bar(token=None):
        """Undo one ``attach_bar()``. Safe to call more times than it was attached.

        The depth counter is clamped at zero rather than allowed to go negative,
        because an unbalanced ``detach`` in a ``finally`` on an error path must
        not leave the counter below zero and make the NEXT bar's attach fail to
        register. ``token`` is accepted and unused; it is there so a call site
        reads as the pair it is.
        """
        global _ACTIVE_WRITER, _WRITER_DEPTH
        with _WRITER_LOCK:
            _WRITER_DEPTH -= 1
            if _WRITER_DEPTH <= 0:
                _WRITER_DEPTH = 0
                _ACTIVE_WRITER = None

    # ``progress()`` -- a @contextlib.contextmanager wrapping tqdm and the
    # attach/detach pair -- WAS HERE AND IS DELETED, before it shipped rather
    # than after. It was written as "the shape new code should use", and then
    # all three bar sites in this project used attach_bar/detach_bar instead,
    # because wrapping them meant re-indenting an eighty-line batch loop inside
    # a pass that promises only output routing changed. That left two ways to
    # register a bar and a caller for exactly one of them, which is the dead
    # declaration tests/test_package_invariants.py check 2h exists to find and
    # what passes 20e and 20f-2 deleted three times over.
    #
    # The context-manager form is still the better shape and is a recorded
    # follow-up for whichever pass next restructures run_batch(); it is four
    # lines over attach_bar/detach_bar and belongs in the commit that gets a
    # caller for it.


console = _Console()
"""The console UI channel. ``console.out(...)`` is what a ``print`` became."""


def bar_is_live():
    """True while a ``console.attach_bar()`` bar is registered. Diagnostic only."""
    return _ACTIVE_WRITER is not None


#------------------------------------------------------------------------------


# ===========================================================================
# THE CORRELATION ID
# ===========================================================================

NO_CORRELATION = "-"
"""What ``correlation_id`` carries on a line that belongs to no patient.

Startup, an index build, a shutdown. A documented sentinel rather than a missing
key, so no consumer has to branch on presence. It is deliberately not a valid
ID: IDs are 12 lowercase hex characters, and ``-`` cannot be mistaken for one.
"""

_CORRELATION_ID = contextvars.ContextVar("oncotriage_correlation_id",
                                         default=NO_CORRELATION)


def new_correlation_id():
    """A fresh opaque ID: 12 lowercase hex characters.

    Derived from ``uuid4`` rather than from the patient ID, and that is a
    decision rather than an accident. Two runs of the same patient -- a resume,
    a resample, an ablation config -- must be distinguishable in the log, and a
    correlation ID that IS the patient ID makes them one stream. The patient is
    carried alongside, in the allowlisted ``patient_id`` field, so the join is
    still one query.
    """
    return uuid.uuid4().hex[:12]


def current_correlation_id():
    """The ID in force on THIS thread right now, or ``NO_CORRELATION``."""
    return _CORRELATION_ID.get()


@contextlib.contextmanager
def correlation_scope(correlation_id=None):
    """Bind a correlation ID for the duration of the block, then put it back.

    The reset is the load-bearing half and it is why this is a context manager
    rather than a setter. ``ThreadPoolExecutor`` REUSES its worker threads, so
    an ID merely set would outlive the task that set it: the next patient
    scheduled onto that worker would inherit the previous patient's ID for
    every line emitted before its own scope opened. Resetting the token in a
    ``finally`` closes that window whether the body returns or raises.

    Yields the ID, so a caller that passed ``None`` can record what it got.
    """
    value = correlation_id or new_correlation_id()
    token = _CORRELATION_ID.set(value)
    try:
        yield value
    finally:
        _CORRELATION_ID.reset(token)


#------------------------------------------------------------------------------


# ===========================================================================
# THE FIELD ALLOWLIST
# ===========================================================================

RESERVED_KEYS = frozenset({
    "ts", "level", "logger", "message", "correlation_id", "dropped_fields",
})
"""The record envelope. A field may not be named any of these.

Fields are flattened to the top level of the JSON object -- ``{"stage": 2}``
rather than ``{"fields": {"stage": 2}}`` -- because that is what makes a log
aggregator's query language usable on them. Flattening is only safe if a field
cannot shadow an envelope key, which is why the two sets are asserted disjoint
at import, below.
"""

LOGGABLE_FIELDS = frozenset({
    # --- correlation and provenance ---------------------------------------
    # patient_id is a Synthea bundle identifier, already stored in every
    # `inferences` row. It is the join key between a log line and the database
    # and it carries no clinical content, so it is allowed. It is NOT the
    # correlation ID; see new_correlation_id().
    "patient_id", "run_id", "config_name", "collection", "pipeline_version",
    # Infrastructure, not clinical: the Qdrant URL a probe could not reach is
    # the first thing an operator needs and the last thing that could identify
    # a patient. It is already printed by oncotriage/config.py on every start.
    "endpoint",

    # --- where in the pipeline --------------------------------------------
    "stage", "node", "event", "status", "phase", "mode",

    # --- timing ------------------------------------------------------------
    "duration_s", "elapsed_s", "delay_s",

    # --- counts. Cardinalities, never contents. ----------------------------
    "count", "total", "index", "depth", "trials_in", "trials_out",
    "dropped", "kept", "lost", "retrieved", "reranked", "evaluated",
    "eligible", "not_eligible", "not_evaluable", "chunks", "query_count",
    "variant_count", "trees_count", "criteria_count", "response_chars",
    "bm25_retrieved", "vector_retrieved", "fusion_pool", "ranked",
    "query_length", "positive", "unboosted",
    # What resolve_patient_mesh() reported, as counts and layer NAMES. How many
    # of the patient's cancer conditions resolved, and which crosswalk layer
    # had to be escalated past, is the diagnosis for a bad expansion. The
    # conditions themselves are not here and must never be.
    "conditions_total", "conditions_resolved", "conditions_pan_only",
    "conditions_unmapped", "pan_only_layers",
    # Stage 1's variant detectors, one count each. A run whose variants came
    # only from the free-text path is searching on weaker evidence than one
    # backed by mCODE records, and only the split says which.
    "variants_mcode", "variants_structured", "variants_free_text",
    # Stage 2 asked for N and got M. Both, because "75 returned" means nothing
    # without "225 requested".
    "channels_ok", "channels_expected", "bm25_requested", "vector_requested",
    # Stage 3's MeSH boost: how many trials each arm moved, and by how much.
    "boosted_direct", "boosted_pan", "boost_direct", "boost_pan",
    # Stage 4's funnel, one field per drop reason. Folded into a sentence they
    # would need a regex to query, which is the thing this pass exists to stop.
    # age_unparsed is deliberately NOT a drop: those trials were KEPT with the
    # age check skipped, and conflating the two would misreport the funnel.
    "mesh_dropped", "stage_dropped", "histology_dropped", "age_dropped",
    "age_unparsed", "sex_dropped", "quality_dropped",
    # The quality gate is two independent knobs, so one drop count cannot
    # describe it. They OVERLAP -- a trial can fail both -- which is why
    # quality_dropped_floor_only is carried as well: without it, a floor count
    # and a percentile count that sum past the total are uninterpretable.
    # medcpt_floor is the configured value the absolute knob used, so a stored
    # line says where the cut was without a second lookup into the config of
    # the day. None of the four names a trial, a patient or a diagnosis.
    "quality_dropped_percentile", "quality_dropped_floor",
    "quality_dropped_floor_only", "medcpt_floor",
    # The cross-encoder's sequence limit, both sides of the comparison plus
    # WHICH declaration was consulted. All three are model geometry -- a token
    # count and an attribute path -- and none of them can carry a patient, a
    # trial or a diagnosis. They are here rather than folded into the message
    # because 6c of tests/test_observability_logging.py forbids interpolating
    # data into a message, and because "unverified" is only actionable when the
    # record says which half was unverified and against what number.
    "max_length_configured", "max_length_declared", "max_length_source",
    # How many of the trials a render pass covered were changed by it, beside
    # `total` (how many it covered) and `count` (how many sequences it
    # rewrote). Three numbers rather than one because they answer three
    # different questions -- 1 sequence in 15 trials and 60 sequences in 15
    # trials are the same `count`-free story and a very different one -- and
    # none of them is derivable from the others. It is a CARDINALITY over
    # public trial objects: no nct_id, no criteria text, nothing patient-side.
    # See _build_trials_text in oncotriage/agent/evaluation.py, whose per-trial
    # decode line moved to DEBUG when this aggregate replaced it.
    "trials_affected",

    # --- retrieval and filtering shape ------------------------------------
    "channel", "channels", "degraded", "expansion_path", "mesh_resolution",
    "mesh_path", "boost_path", "filter", "skip_reason", "reason",
    "ablation_flag", "score_min", "score_max", "rrf_min", "rrf_max",
    "threshold",

    # --- model and cost ----------------------------------------------------
    # WHICH PROVIDER SERVED THE CALL -- "openai" or "bedrock",
    # config.MATCHING_PROVIDER's value and nothing else. It is infrastructure,
    # the same kind of fact as `endpoint` above, and it cannot carry a patient,
    # a trial or a diagnosis. It is a FIELD rather than message text because
    # section 6c of tests/test_observability_logging.py forbids interpolating
    # data into a message, and because a Bedrock degradation line is only
    # actionable when the record says which provider produced it.
    "provider",
    "model", "tokens_in", "tokens_out", "tokens_estimated", "tokens_actual",
    "finish_reason", "retry", "max_retries", "truncations", "cost_usd",
    "tokens_reasoning", "estimate_ratio", "calls",
    # Stage 5's validators, as counts. What was remapped, corrected or excluded
    # -- never WHICH criterion, because a criterion label plus a verdict is a
    # clinical statement about this patient.
    "criteria_not_applicable", "empty_denominator_trials",
    "mesh_filter_applied",
    # A LIST of public trial identifiers. Same argument as nct_id.
    "nct_ids",
    # Stage 5's out-of-set detector reports its drops in TWO buckets, because
    # they are two different faults: `fabricated` names an id that is in no
    # candidate set of this run at all (the clinical fault, and the only one
    # written to inferences.hallucinated_trials), `cross_chunk` names a real
    # candidate belonging to another chunk of a split request (a provider
    # quirk that costs the patient nothing). One count and one id list each;
    # `count` beside them is the total. Same argument as nct_ids for the lists.
    "fabricated_count", "fabricated_nct_ids",
    "cross_chunk_count", "cross_chunk_nct_ids",
    # And its duplicate-answer collapse reports the two cases apart for the
    # same reason: identical verdicts are one answer typed twice (the first is
    # kept), conflicting verdicts are the model contradicting itself (the trial
    # becomes not evaluable). A single count could not tell a reader which of
    # those a run saw, and only the second is a quality signal.
    "duplicate_identical_count", "duplicate_identical_nct_ids",
    "duplicate_conflicting_count", "duplicate_conflicting_nct_ids",
    # Stage 5's temporal-conflict detector: {marker: count} for each of its two
    # hand-authored word lists, one call's worth. THE KEYS ARE OUR OWN
    # VOCABULARY -- members of _RESOLVED_STATE_MARKERS and
    # _ACTIVE_REQUIREMENT_MARKERS in oncotriage/agent/evaluation.py -- so they
    # are code identifiers, on exactly the footing that makes
    # `degradation_totals` allowlistable while its counters' own KEYS are not.
    # The values are cardinalities.
    #
    # WHAT IS DELIBERATELY NOT HERE, and this is the reason the detector reports
    # markers rather than matches: the patient_value and the criterion text that
    # co-occurred. Those are the model's clinical statement about this patient
    # -- the same content that keeps `response_preview` off this list -- and the
    # detector never passes them. Knowing that "remission" fired 14 times
    # diagnoses the vocabulary; knowing WHICH remission does not, and the row
    # itself is stored in trial_matches.criterion_details with its flag.
    "temporal_conflict_resolved_markers", "temporal_conflict_active_markers",

    # --- identifiers of PUBLIC objects -------------------------------------
    # An NCT ID names a trial on a public registry. It is not patient data.
    "nct_id",
    # The trial's REGISTERED condition strings, straight off ClinicalTrials.gov.
    # Same argument as nct_id and nct_ids: this is the public registry's own
    # description of a study, published by its sponsor, and it exists in the
    # Qdrant payload of every indexed trial already. It is allowed because the
    # scraper's admission screen must log every drop WITH the conditions that
    # caused it -- a drop record naming only a count is unauditable, and this
    # screen is the one whose losses nothing downstream can detect.
    #
    # NOTE THE DIRECTION. A trial condition describes a STUDY. A patient
    # condition describes a PERSON, and no patient-side condition string is on
    # this list or may ever be added to it -- see conditions_total above, which
    # is how the patient side reports the same shape.
    "trial_conditions",

    # --- the scraper's admission screen ------------------------------------
    # Closed vocabularies (registries/mesh.py:TRIAL_ONCOLOGY_VERDICTS and the
    # evidence strings beside them) plus the MeSH top-level categories that
    # justified a non-oncology verdict. Categories are branch letters like
    # "C19", not tree numbers, and they are about the TRIAL.
    "verdict", "evidence", "mesh_categories",
    # Screen funnel counts.
    "screened", "admitted", "non_oncology_dropped", "unresolved_kept",
    # Defect 3: how split_inclusion_exclusion resolved this trial's criteria,
    # and the corpus-level residue. A closed vocabulary, see CRITERIA_SPLIT_*.
    "split_method", "unsplit_count",
    # The criteria_split ingestion gate's standing measurement, emitted on
    # every index verification whether it passes or fails. Three (count,
    # fraction, ceiling) triples plus the four raw branch counts the fractions
    # are built from. Every one is a CARDINALITY over the trial corpus or a
    # configured threshold -- there is no per-trial content here, and the
    # branch names are the closed CRITERIA_SPLIT_* vocabulary. The counts and
    # the fractions are both carried because a fraction alone cannot say
    # whether a corpus shrank or a population grew, and the ceiling is carried
    # beside each so a stored line says where the cut was without a second
    # lookup into the config of the day -- the same argument medcpt_floor
    # already makes above. `total` and `unsplit_count` are reused rather than
    # duplicated.
    "split_degraded_count", "split_degraded_fraction", "split_degraded_max",
    "split_no_exclusion_count", "split_no_exclusion_fraction",
    "split_no_exclusion_max",
    "split_unusable_count", "split_unusable_fraction", "split_unusable_max",
    "empty_criteria_count", "field_absent_count", "payload_unreadable_count",

    # --- the inference write's durability (the write-durability pass) ------
    # db_path names a FILE on the machine running the pipeline. It is
    # infrastructure, on exactly the footing `endpoint` is already on, and it is
    # the first thing an operator needs when a row is reported lost: with two
    # writing processes and ONCOTRIAGE_INFERENCES_DB able to point either of
    # them elsewhere, "a row was lost" without "from which file" is not
    # actionable. It carries no clinical content -- resolve_inference_db_path
    # returns a configured path, never anything derived from a patient.
    "db_path",
    # The journal mode the pragma actually reported back, and the mode that was
    # asked for. Both are SQLite vocabulary ("wal", "delete", ...), and the pair
    # is the whole point: WAL is a property of the FILE and can fail to take.
    "journal_mode", "journal_mode_requested",
    # Reconciliation: writes attempted by this process, writes whose rows were
    # then FOUND in the table, and the shortfall. `attempted` and `missing` are
    # not covered by `count`/`total`/`lost` without conflating three numbers
    # that only mean anything side by side.
    "attempted", "verified", "missing",
    # The run-end degradation summary: {counter NAME: total}. Names only, and
    # that restriction is the reason this is allowlistable at all -- a counter
    # name is a code identifier, while counter KEYS carry third-party and
    # clinical text (SEX_UNKNOWN_KEPT is keyed by the patient's recorded sex,
    # M_CATEGORY_UNREADABLE by a capped observation display). The keys go to the
    # console block, which is transient, on the same footing as the Stage 5
    # response preview. See oncotriage/degradation.py:totals().
    "degradation_totals",
    # Which attempt of SQLITE_WRITE_MAX_ATTEMPTS succeeded or gave up. `retry`
    # is already here but means "the retry index" at the OpenAI call sites, and
    # a write that succeeded first time has made zero retries and one attempt.
    "attempts",
    # The `inferences.db` run row's integer id -- the join key between a log
    # line and the `runs` table, exactly as `patient_id` joins a line to
    # `inferences`.
    #
    # DELIBERATELY NOT `run_id`, for the reason stated at `tracking_run_id`
    # below and with the same force: `run_id` ALREADY MEANS the ablation
    # database's integer run id (oncotriage/ablation/study.py passes it under
    # that name), and two different integer keys under one field name is a log
    # an aggregator cannot group. Three id spaces now exist and each has its
    # own field: `run_id` (ablation), `tracking_run_id` (MLflow),
    # `inference_run_id` (inferences.db).
    "inference_run_id",

    # --- the run-to-configuration index (the tracking pass) ----------------
    # tracking_run_id is MLflow's run id: a random 32-hex identifier of an
    # INDEX RECORD, generated by the tracking store and derived from nothing.
    # It is the join key between a log line and the tracking store, the way
    # patient_id joins a line to `inferences` -- and it is deliberately NOT
    # `run_id`, which already means the ablation database's integer run id and
    # would silently conflate two different keys in one field name.
    #
    # The other four are a closed vocabulary (`kind` is RUN_KINDS, `status` is
    # RUN_STATUSES) and two cardinalities. tracking_field carries the NAME of a
    # metadata or metric field that degraded -- never its value, which is
    # FIELD_DROPS' own rule applied to the module that reports degradations.
    "tracking_run_id", "tracking_kind", "tracking_status",
    "tracking_param_count", "tracking_metric_count", "tracking_field",

    # --- failures ----------------------------------------------------------
    # error_type is a class name and is always safe. error_message is allowed
    # because an error with no message is not actionable; the judgement is that
    # exception text from Qdrant, OpenAI and the standard library does not carry
    # clinical content. What deliberately is NOT allowed is any payload preview
    # -- `response_preview`, `prompt`, `patient_summary` -- because the model's
    # response DOES carry criterion-level clinical reasoning. Those go to the
    # console channel, which is transient, or nowhere.
    "error_type", "error_message",
})
"""Every field name that may appear in a structured log record.

Anything else is dropped by ``_JsonFormatter``. To add a field, add it here and
say why -- the point of the list is that the decision is made once, visibly,
rather than at each call site by whoever is adding a log line.
"""

_COLLISION = RESERVED_KEYS & LOGGABLE_FIELDS
if _COLLISION:
    raise RuntimeError(
        f"observability: {sorted(_COLLISION)} is both an envelope key and an "
        f"allowlisted field. Fields are flattened to the top level of the "
        f"record, so a name in both sets would let a caller overwrite the "
        f"level, the correlation ID or the timestamp of its own log line.")
del _COLLISION

FIELD_DROPS = Counter()
"""Times a field was dropped for not being on the allowlist, keyed by field name.

Never keyed by value: a counter that recorded what it withheld would be the
leak it exists to prevent.
"""


def filter_fields(fields):
    """Split a field dict into (allowed, sorted names of the dropped).

    Public because ``tests/test_observability_logging.py`` asserts on it
    directly, and because the enforcement being inspectable is worth more than
    it being private.
    """
    kept, dropped = {}, []
    for key in sorted(fields):
        if key in LOGGABLE_FIELDS:
            kept[key] = fields[key]
        else:
            dropped.append(key)
            FIELD_DROPS[key] += 1
    return kept, dropped


#------------------------------------------------------------------------------


# ===========================================================================
# THE FORMATTER AND THE HANDLER
# ===========================================================================

FIELDS_ATTR = "oncotriage_fields"
"""The ``record`` attribute structured fields ride on, set via ``extra=``."""


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, with the allowlist applied.

    THE ALLOWLIST IS APPLIED HERE, not in ``StructuredLogger``, and the
    difference matters: a caller that reaches for ``logging.getLogger(
    "oncotriage.whatever").info(msg, extra={FIELDS_ATTR: {...}})`` -- bypassing
    every helper in this module -- still passes through this formatter, because
    the handler owns it. There is no path to the stream that is not filtered.
    """

    converter = time.gmtime
    """UTC, and the ``Z`` suffix below is only honest because of this line.

    ``logging.Formatter.formatTime`` defaults to ``time.localtime``. A record
    stamped with a local time and suffixed ``Z`` is worse than one with no
    timezone at all: it parses cleanly, sorts cleanly, and is wrong by the
    machine's UTC offset -- which on a container built in one region and run in
    another is the difference between a log line landing before or after the
    event it describes.
    """

    def format(self, record):
        raw = getattr(record, FIELDS_ATTR, None) or {}
        if not isinstance(raw, dict):
            # A non-dict is a caller error, not a reason to lose the line.
            raw = {"error_message": f"non-dict log fields: {type(raw).__name__}"}
        kept, dropped = filter_fields(raw)

        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") +
                  f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None)
                              or NO_CORRELATION,
            "message": record.getMessage(),
        }
        if dropped:
            payload["dropped_fields"] = dropped
        payload.update(kept)

        if record.exc_info:
            # The traceback is a developer artefact, not a field. It is placed
            # in error_type/error_message shape so the record stays flat.
            payload.setdefault("error_type", record.exc_info[0].__name__)
            payload.setdefault("error_message", str(record.exc_info[1]))

        try:
            return json.dumps(payload, default=str, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            # A field that will not serialise must not lose the line.
            EMIT_FAILURES[f"json:{type(exc).__name__}"] += 1
            return json.dumps({
                "ts": payload["ts"], "level": payload["level"],
                "logger": payload["logger"],
                "correlation_id": payload["correlation_id"],
                "message": payload["message"],
                "error_type": type(exc).__name__,
                "error_message": "log fields were not JSON-serialisable",
            })


class _BarAwareHandler(logging.Handler):
    """Writes formatted records through ``_emit_line``.

    Not a ``StreamHandler``. A ``StreamHandler`` binds its stream and writes to
    it directly, which is precisely the collision described at the top of this
    file: it would draw over a live tqdm bar. Going through ``_emit_line``
    means a log record and a ``console.out`` line are routed by the same rule.
    """

    def emit(self, record):
        try:
            _emit_line(self.format(record))
        except Exception:  # noqa: BLE001 - logging must never kill the caller
            self.handleError(record)


class _CorrelationFilter(logging.Filter):
    """Stamps the current thread's correlation ID onto every record.

    A FILTER rather than something the caller passes, so a record created by
    ``logging.getLogger("oncotriage.x")`` directly is stamped too. An explicit
    ``extra={"correlation_id": ...}`` still wins, which is what lets a caller
    log on behalf of a patient whose scope it is not inside.
    """

    def filter(self, record):
        if not getattr(record, "correlation_id", None):
            record.correlation_id = current_correlation_id()
        return True


#------------------------------------------------------------------------------


# ===========================================================================
# CONFIGURATION
# ===========================================================================

ROOT_LOGGER_NAME = "oncotriage"

_CONFIG_LOCK = threading.RLock()
_CONFIGURED = False


def configure_logging(level=None, force=False):
    """Attach the handler to the ``oncotriage`` logger. Idempotent.

    Called lazily by ``get_logger``; a caller that wants a specific level can
    call it first. ``propagate`` is turned OFF so records do not also reach the
    root logger -- an application that has called ``logging.basicConfig()``
    would otherwise get every line twice, once as JSON and once as plain text.
    """
    global _CONFIGURED
    with _CONFIG_LOCK:
        if _CONFIGURED and not force:
            return logging.getLogger(ROOT_LOGGER_NAME)

        logger = logging.getLogger(ROOT_LOGGER_NAME)
        for existing in list(logger.handlers):
            logger.removeHandler(existing)

        handler = _BarAwareHandler()
        handler.setFormatter(_JsonFormatter())
        handler.addFilter(_CorrelationFilter())
        logger.addHandler(handler)
        logger.setLevel(level if level is not None else resolve_log_level())
        logger.propagate = False
        _CONFIGURED = True
        return logger


class StructuredLogger:
    """The call shape: ``log.info("message", stage=2, trials_out=75)``.

    A thin wrapper rather than a ``logging.Logger`` subclass, because the field
    dict has to be routed into ``extra=`` under a single key and a subclass
    would have to reimplement all five level methods to do it anyway. Nothing
    here enforces the allowlist -- that is the formatter's job, and doing it
    twice would let the two disagree.
    """

    __slots__ = ("_logger",)

    def __init__(self, logger):
        self._logger = logger

    def _log(self, level, message, fields, exc_info=False):
        if not self._logger.isEnabledFor(level):
            return
        self._logger.log(level, message, extra={FIELDS_ATTR: fields},
                         exc_info=exc_info)

    def debug(self, message, **fields):
        self._log(logging.DEBUG, message, fields)

    def info(self, message, **fields):
        self._log(logging.INFO, message, fields)

    def warning(self, message, **fields):
        self._log(logging.WARNING, message, fields)

    def error(self, message, **fields):
        self._log(logging.ERROR, message, fields)

    def exception(self, message, **fields):
        """ERROR with the active exception attached. Call from an except block."""
        self._log(logging.ERROR, message, fields, exc_info=True)

    @property
    def std(self):
        """The underlying ``logging.Logger``. For ``isEnabledFor`` and tests."""
        return self._logger


def get_logger(name):
    """A ``StructuredLogger`` under the ``oncotriage`` tree.

    ``get_logger(__name__)`` from a package module gives a logger named for the
    module, which is what lands in the record's ``logger`` field.
    """
    configure_logging()
    if name == ROOT_LOGGER_NAME or name.startswith(ROOT_LOGGER_NAME + "."):
        full = name
    else:
        full = f"{ROOT_LOGGER_NAME}.{name}"
    return StructuredLogger(logging.getLogger(full))


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug  7 2026

@author: ramyalsaffar
"""
