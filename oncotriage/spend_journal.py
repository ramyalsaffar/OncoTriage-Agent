"""The CROSS-PROCESS spend record: one append-only file, one owner.

``oncotriage/spend.py``'s ledger is PROCESS-LOCAL by design and says so. That
is right for the campaign budget, which has a cross-process chain of its own --
``database_logger.campaign_spend_before`` walks the ``runs`` table backwards
over identical fingerprint columns -- and it was WRONG for the judge, which has
no ``runs`` row, writes no ``inferences``, and whose only memory was the state
file of the session that happened to be running.

**THE DEFECT THIS CLOSES, MEASURED RATHER THAN ARGUED.**
``rater.rater_spend_before`` seeded a session's ledger from THAT SESSION'S OWN
``rater_state.json``, so a fresh output directory started at the full cap.
``config.RATER_SPEND_CAP_USD``'s own docstring said "the most one JUDGE SESSION
may spend". Item 11 needed two populations that lived in two run directories,
which ``--include-keys`` could not span, so it was two sessions -- and as two
``rater_run.py`` invocations they would have been two processes with two
module-global ledgers and TWO INDEPENDENT $50 BUDGETS. Nothing would have said
so. What prevented it was that item 11's driver called ``main()`` twice inside
one interpreter, which is a property of a hand-written script rather than of
this project.

**THE OPERATOR RULING IS THAT THE CAP IS CUMULATIVE FOR THE CAMPAIGN**, and a
cumulative cap needs a store both invocations can read. This is it.

────────────────────────────────────────────────────────────────────────────
WHY JSONL AND flock, AND WHAT THAT BUYS
────────────────────────────────────────────────────────────────────────────

SQLite was the other instrument and it was declined for two reasons that are
about this file rather than about databases. The record has to survive being
read by a person with ``cat`` on a machine where nothing is installed -- it is
the artifact an operator consults when a judge refuses and they need to know
whose money it was -- and it has to be APPEND-ONLY in a way a reader can see:
a row that was edited leaves no trace in a table and leaves a duplicated
``entry_id`` here, which ``read_entries`` reports.

ATOMICITY IS AN EXCLUSIVE ``flock`` HELD ACROSS THE WHOLE READ-THEN-APPEND, not
a bare ``O_APPEND`` write. A single short line under ``O_APPEND`` is atomic on
a local filesystem, which would be enough if appending were all this did -- but
the write is CONDITIONAL on what is already in the file (an ``entry_id``
already present is not appended twice), and a check-then-act split across two
system calls is a race whatever the second one guarantees. The lock is released
by the KERNEL however the process exits, which is why it is not a lock file:
``oncotriage/control.py`` makes that argument at length for the run lock and it
is the same argument.

**AND THE LOCK IS ADVISORY, WHICH IS STATED RATHER THAN ASSUMED.** ``flock``
binds only writers that also take it. Every writer in this project goes through
``append``; a hand-edit with a text editor does not, and the guard against that
is the duplicate-id report rather than the lock.

────────────────────────────────────────────────────────────────────────────
IDEMPOTENCY, WHICH IS THE HARD PART
────────────────────────────────────────────────────────────────────────────

A judge session appends once per COLLECTED BATCH, and ``--resume <batch id>``
re-collects a batch that has already been collected -- deliberately, because
that is what a resume IS. Appending again would charge the same money twice and
the cap would refuse a session for spend that never happened.

``entry_id`` is DERIVED from the facts that identify the charge -- the budget,
the source, the state file and the batch id -- rather than generated, so the
second append computes the same id and is refused by the reader that is holding
the lock. A generated id would make the record honest about how many times the
batch was collected and useless as a cap, which is the wrong trade for a file
whose only consumer is a budget.

**AND THE MIGRATION ENTRY CARRIES THE BATCH IDS IT ALREADY COVERS.** A state
file that existed before this journal did has its whole spend in ONE migration
entry; if that session is later resumed and re-collects one of its old batches,
the per-batch entry would be new and would double-count against the migration
entry. ``covers_batch_ids`` is read out of ``state["batches"]`` at migration
time and ``total`` skips any per-batch entry whose id is inside it.
"""

import fcntl
import hashlib
import io
import json
import os
import threading
import time
from collections import Counter
from datetime import datetime, timezone

from oncotriage import paths, settings, spend
from oncotriage.observability import console, get_logger

log = get_logger(__name__)

JOURNAL_BASENAME = "spend_journal.jsonl"
"""The file's name. One owner; every writer resolves it through
``journal_path`` rather than joining this itself."""

SCHEMA_VERSION = 1
"""Stamped on every entry. A reader that meets a HIGHER version refuses the
entry rather than guessing at it -- ``initialize_database``'s rule for the
same reason: a record written by a build that knew more fields than this one
cannot be summed by a build that does not know which of them are money."""

ENTRY_KIND_MIGRATION = "migration"
ENTRY_KIND_BATCH = "batch"
ENTRY_KIND_RUN = "run"

ENTRY_KINDS = (ENTRY_KIND_MIGRATION, ENTRY_KIND_BATCH, ENTRY_KIND_RUN)
"""How a charge came to be recorded. CLOSED.

``migration``  one pre-journal state file, summed from the artifact on disk.
``batch``      one collected batch of the rater's Batch API submission.
``run``        one whole invocation of a harness that has no batch structure
               to key on -- the ragas judge, which charges per response and
               would otherwise write thousands of lines and take thousands of
               locks.

An unrecognised kind is COUNTED AND SKIPPED rather than summed: a kind this
build does not know is a charge it cannot say the shape of, and adding it to a
cap would be spending a number nobody here defined.
"""

JOURNAL_FAULTS = Counter()
"""Everything this module could not read or write, keyed by what it was.

REGISTERED IN ``oncotriage/degradation.py`` so a run that could not record its
own spend says so in the run-end block rather than in a line nobody kept. The
direction of every fault here is the UNDER-recording one -- an entry that was
not written is money the next session will not see -- which is why it is a
degradation and not a census.
"""


def journal_path(path=None):
    """Where the record lives. THREE TIERS, first match wins.

      1. an explicit ``path`` argument -- never cached, because it answers a
         question about one CALL rather than about the machine;
      2. ``ONCOTRIAGE_SPEND_JOURNAL``, through
         ``settings.resolve_spend_journal``;
      3. ``09- Testing/Evaluation Runs/spend_journal.jsonl``.

    THE VARIABLE IS NOT DECORATION AND THE REASON WAS MEASURED. This module
    resolves its own default, so ANY process that drives a harness which
    records spend writes to the real journal -- including
    ``tests/test_resume_capture_and_ragas.py``, which drives the real ragas
    ``main()`` ten times and put ten entries into the production file the first
    time this was run. A test cannot redirect a store the code resolves for
    itself, and ``ONCOTRIAGE_INFERENCES_DB`` exists one layer over for exactly
    that reason: ``oncotriage/api/server.py`` calls ``log_inference`` with no
    path, so Files 18 and 19 had no way to keep their POSTs out of the real
    database either.

    THE DEFAULT IS UNDER ``09- Testing/Evaluation Runs/`` because that is the
    parent of every rater output directory and every state file this migrates
    from, so the record sits beside the artifacts it is about. It is
    deliberately NOT a new entry in ``oncotriage/paths.py``: a path variable
    there has to be added to the local glob table, the Docker literal table AND
    ``.github/scripts/provision_ci_paths.py`` and is cross-checked against all
    three, which is a large blast radius for a file that lives inside a
    directory those tables already name.
    """
    if path:
        return os.path.abspath(os.path.expanduser(path))
    override, _source = settings.resolve_spend_journal()
    if override:
        return os.path.abspath(override)
    return os.path.join(paths.testing_evaluation_path, JOURNAL_BASENAME)


def resolved_journal_path(path=None):
    """``journal_path`` or None, when the default cannot be resolved at all.

    **THIS EXISTS BECAUSE FIVE FUNCTIONS CLAIMED "NEVER RAISES" AND ALL FIVE
    RAISED, AND IT WAS MEASURED RATHER THAN REVIEWED.** The default path is
    built from ``paths.testing_evaluation_path``, which is a LAZY GLOB over the
    sibling data tree -- and ``_glob_one`` raises a ``RuntimeError`` naming the
    pattern when nothing matches it. So on a machine with no ``09- Testing/``
    directory -- a wheel install, a CI checkout of the code alone, a container
    before its data volume is mounted -- ``rater_spend_before`` refused to
    start a judge session because a DIRECTORY was missing, which is the exact
    failure its own docstring says it must not have. It is the pass-20c-2b
    defect (a lazy resolver firing where nothing expected it) reached through a
    new module.

    ``journal_path`` itself still raises, deliberately: it is asked WHERE the
    file is and there is no honest answer. Every caller that has to keep going
    asks this instead, and an unresolvable default is COUNTED -- because "there
    is no journal" and "there is a journal and I could not find it" are the
    same empty reading and only one of them is a machine that needs fixing.
    """
    try:
        return journal_path(path)
    except Exception as exc:                                    # noqa: BLE001
        JOURNAL_FAULTS[f"unresolvable_path:{type(exc).__name__}"] += 1
        return None


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def entry_id(budget, source, scope, unit):
    """The id that makes an append idempotent. DERIVED, never generated.

    ``scope`` identifies the writer -- a state file for the rater, an output
    directory for a harness with no state file -- and ``unit`` identifies the
    charge within it: a batch id, or a run stamp. Two appends of the same
    charge compute the same id and the second is refused; two different charges
    cannot collide unless they share all four, which is what "the same charge"
    means.
    """
    blob = "\x00".join((str(budget), str(source), str(scope), str(unit)))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def decode_journal_line(raw):
    """One line's bytes as text, or ``None`` having COUNTED why not.

    **THE JOURNAL IS A BYTE STREAM AND A KILL CAN CUT A LINE MID-CHARACTER.**
    Every reader here used to open the file with ``encoding="utf-8"`` and read
    it whole, so one truncated multi-byte character raised ``UnicodeDecodeError``
    -- which is a ``ValueError`` and NOT an ``OSError``, so no handler in this
    module caught it. Measured: a journal ending in a half-written ``\xc3``
    made ``read_entries`` RAISE, and with it ``total``, ``describe``,
    ``confirmed_usd_for_scope`` and ``rater_spend_before`` -- so a single
    truncated byte took the cumulative cap out of service and refused to start
    a judge session, from three functions whose docstrings all say NEVER
    RAISES.

    A line that cannot be decoded is SKIPPED and COUNTED, exactly as one that
    cannot be parsed already was. It is not decoded with ``errors="replace"``:
    a mojibake line would then reach ``json.loads``, and on the off chance it
    parsed it would put invented characters into a record this module reports
    as fact. The bytes stay on disk as evidence either way.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        JOURNAL_FAULTS["parse:not_utf8"] += 1
        return None


def read_entries(path=None):
    """Every well-formed entry, in file order. NEVER RAISES.

    An absent file is an empty list, which is the correct reading: nothing has
    been recorded yet. A line that cannot be DECODED, that will not parse, or
    that carries a schema version this build does not know, is COUNTED into
    ``JOURNAL_FAULTS`` and SKIPPED -- summing a line whose shape is unknown
    would put an unverified number into a cap.

    **IT READS BYTES AND SPLITS ON LINES ITSELF**, rather than opening the file
    as text. A truncated multi-byte character is a real state for a file that
    is appended to under a kill, and decoding the whole file makes ONE such
    byte unreadable EVERYTHING -- including every record written before it and
    every record written after. See ``decode_journal_line``.

    THE UNDER-READING DIRECTION IS THE UNSAFE ONE HERE AND IT IS NOT HIDDEN:
    a skipped entry is money the next session will not be charged for, so every
    skip is a counted degradation that reaches the run-end report.
    """
    p = resolved_journal_path(path)
    if p is None:
        return []
    try:
        with io.open(p, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return []
    except OSError as exc:
        JOURNAL_FAULTS[f"read:{type(exc).__name__}"] += 1
        console.out(f"  [Spend journal] could not read {p}: "
                    f"{type(exc).__name__}: {exc}")
        return []
    out = []
    for lineno, rawline in enumerate(raw.splitlines(), 1):
        if not rawline.strip():
            continue
        line = decode_journal_line(rawline)
        if line is None:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            JOURNAL_FAULTS["parse:not_json"] += 1
            continue
        if not isinstance(entry, dict):
            JOURNAL_FAULTS[f"parse:{type(entry).__name__}"] += 1
            continue
        version = entry.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool):
            JOURNAL_FAULTS["schema:absent"] += 1
            continue
        if version > SCHEMA_VERSION:
            JOURNAL_FAULTS[f"schema:from_the_future:{version}"] += 1
            continue
        entry["_lineno"] = lineno
        out.append(entry)
    return out


APPEND_WROTE = "wrote"
APPEND_DUPLICATE = "duplicate"
APPEND_CONFLICT = "conflict"
APPEND_FAILED = "failed"
APPEND_UNCERTAIN = "uncertain"

APPEND_OUTCOMES = (APPEND_WROTE, APPEND_DUPLICATE, APPEND_CONFLICT,
                   APPEND_FAILED, APPEND_UNCERTAIN)
"""What one attempt to append can have done. CLOSED, and the split between
the last four is the whole reason this exists.

``append`` answers a two-valued question -- did a line get written -- and
callers used it as if it answered a four-valued one. It returns ``False`` for
"the id is already there, so the money IS recorded" AND for "the write failed,
so the money is NOT recorded", and those have OPPOSITE consequences for a
caller keeping a running total.

**MEASURED, NOT ARGUED.** ``RunSpendCheckpointer`` advanced its recorded total
on that ``False``. A $0.30 delta whose append failed was never retried,
finalization then computed its remainder against the already-advanced total and
wrote **$0.00**, and the checkpointer reported ``recorded`` of $0.50 against a
journal holding $0.20. The $0.30 was lost permanently and nothing said so.

  ``wrote``      the line is in the file and fsynced.
  ``duplicate``  an entry with this id is already there AND names the same
                 charge AND the same amount -- so the money is recorded and a
                 caller may advance past it. This is what makes a RETRY safe.
  ``conflict``   an entry with this id is there and does NOT match. Two
                 different charges derived one id, or the file was edited. The
                 money this attempt carries is NOT recorded by that entry and a
                 caller must never advance past it.
  ``failed``     nothing was persisted, to the best of this process's
                 knowledge: the error happened before the write was attempted.
  ``uncertain``  the error happened at or after the write. The line may or may
                 not be in the file. A caller must RETRY under the SAME id and
                 the SAME amount, which is exactly the case ``duplicate``
                 resolves.

``failed`` and ``uncertain`` are kept apart even though a caller treats both as
"not confirmed", because they are different diagnoses: one says the store is
unreachable and the other says the store may hold a line this process cannot
see the result of.
"""

# THE AMOUNTS ARE COMPARED WITH A TOLERANCE, and it is small enough to be an
# equality for every value this project writes. A charge is dollars with at
# most six decimals (`round(..., 6)` at the rater's own writer), so 1e-9 is
# three orders below the smallest meaningful difference and cannot make two
# genuinely different charges look alike. Exact `==` would be correct for the
# checkpointer -- it re-serializes the SAME float, and Python's float repr
# round-trips -- and would be brittle for any other caller that recomputes.
_AMOUNT_EPSILON = 1e-9


def _same_charge(existing, payload):
    """Does an entry already in the file record the SAME charge as ``payload``?

    Compares the four fields ``entry_id`` is DERIVED from plus the amount --
    "charge identity AND amount". It deliberately does NOT compare the whole
    dict: ``recorded_at_utc`` differs on every attempt, so a whole-dict
    comparison would report every retry as a conflict, which is the one thing
    that must not happen to a retry.
    """
    for field in ("budget", "source", "scope", "unit"):
        if existing.get(field) != payload.get(field):
            return False
    a, b = existing.get("usd"), payload.get("usd")
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return a == b
    if a != a or b != b:                                        # NaN
        return False
    return abs(float(a) - float(b)) <= _AMOUNT_EPSILON


def _serialize_entry(payload):
    """One entry as the bytes that go on the wire, terminator included.

    A NAMED FUNCTION RATHER THAN AN INLINE EXPRESSION, and the reason is that
    the read-back confirmation below has to be exercisable: a control needs a
    way to make one append produce a line that cannot be parsed back, and
    patching this is the narrowest seam that does it. It is also the one place
    the newline is appended, which is the fact the boundary recovery is about.
    """
    return (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")


def _needs_boundary(fh):
    """Is the file non-empty and NOT terminated by a newline?

    **THE STATE THIS DETECTS IS A KILL MID-WRITE**, and it has two shapes that
    are indistinguishable from here and must be: an INCOMPLETE RECORD (the
    process died part-way through ``fh.write``) and a COMPLETE RECORD MISSING
    ONLY ITS TERMINATOR (the bytes landed, the newline did not). Both leave the
    same signature -- a final line with no ``\n`` -- and both cause the next
    append to CONCATENATE onto it.

    IT ASKS FOR THE LAST BYTE AND NOT THE LAST CHARACTER. The file is a byte
    stream and the fragment may be cut mid-character; a text-mode read to find
    the terminator would raise on exactly the file this exists to repair.
    """
    fh.seek(0, os.SEEK_END)
    size = fh.tell()
    if not size:
        return False
    fh.seek(size - 1)
    return fh.read(1) != b"\n"


def append_with_outcome(entry, path=None):
    """Append one entry and say WHICH of ``APPEND_OUTCOMES`` happened.

    NEVER RAISES, for ``append``'s reason: it is called after money has already
    been spent, and a record that could not be written must not discard the
    collection it is about.

    THE READ AND THE WRITE ARE UNDER ONE EXCLUSIVE ``flock``, and so are the
    boundary recovery and the read-back. The decision to write depends on what
    the file already holds, so releasing the lock between any of them would let
    a second process append in the window.

    ═══ BOUNDARY RECOVERY, BEFORE ANYTHING ELSE ═══

    **THE DEFECT: A JOURNAL WHOSE FINAL LINE IS UNTERMINATED MADE THE NEXT
    APPEND CONCATENATE ONTO IT.** Reproduced -- a good entry, then a killed
    partial write, then an append: the helper reported ``wrote``, the merged
    line would not parse, ``read_entries`` counted-and-skipped it, and the
    caller advanced its total for money the file could not return. The
    read-back at finalize saw the shortfall and could not recover it.

    So a missing terminator is RESTORED FIRST: one ``\n`` is appended, which
    makes whatever is there its own isolated line. **NOTHING IS TRUNCATED,
    REWRITTEN OR DELETED** -- the bytes on disk are evidence of a kill and this
    module is not entitled to decide what they were going to say.

    The two shapes then diverge, which is the point of not distinguishing them
    here:

      * an INCOMPLETE FRAGMENT becomes its own unparseable line, stays on disk,
        and surfaces through ``read_entries``' existing fault counting;
      * a COMPLETE RECORD MISSING ITS NEWLINE becomes fully readable -- it
        gains the terminator it was denied, and the very next thing this
        function does is scan for duplicates, so if it names the same charge
        the append is correctly refused as one.

    RECOVERY RUNS ABOVE THE DUPLICATE SCAN AND NOT BESIDE THE WRITE, AND NOT
    FOR THE REASON THIS PARAGRAPH USED TO GIVE. It claimed a
    complete-but-unterminated record would be invisible to the scan below it,
    and that is FALSE: the scan reads the file with ``splitlines()``, which
    yields the trailing chunk as its own line, so such a record is ALREADY
    READABLE to the scan and to ``read_entries`` alike, terminator or not.

    THE REAL REASON IS THAT THE SCAN CAN RETURN EARLY. A duplicate or a
    conflict returns without writing, so recovery placed beside the write would
    not run on those calls and the torn boundary would survive them. Above the
    scan it runs whatever the outcome, which leaves the file consistent and
    means the NEXT append cannot join onto that record either. The corruption
    is there regardless, one byte repairs it, and the next writer would
    otherwise have to.

    ═══ CONFIRMATION IS READABILITY, NOT WRITE SUCCESS ═══

    ``fh.write`` returning is not evidence that the file now holds a record.
    The merged-line defect above is exactly a successful write that produced no
    readable entry, and boundary recovery is not the only way that can happen.
    So the appended bytes are READ BACK under the same lock, decoded, parsed,
    and compared for entry id AND charge identity AND amount. Only that is
    ``wrote``; anything else is ``uncertain``, which leaves the caller's delta
    PENDING under its frozen id -- and a retry then resolves against whatever
    is really on disk.
    """
    p = resolved_journal_path(path)
    if p is None:
        return APPEND_FAILED
    payload = dict(entry)
    payload.setdefault("schema_version", SCHEMA_VERSION)
    payload.setdefault("recorded_at_utc", _utc_now())
    eid = payload.get("entry_id")
    if not eid:
        JOURNAL_FAULTS["append:no_entry_id"] += 1
        return APPEND_FAILED
    attempted = {"write": False}
    try:
        parent = os.path.dirname(p)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        # BINARY, AND THAT IS FORCED RATHER THAN STYLISTIC. The terminator
        # check is a question about the last BYTE, and the existing content may
        # be cut mid-character -- so a text-mode handle would raise
        # `UnicodeDecodeError` on precisely the file this function exists to
        # repair, out of a function documented NEVER RAISES.
        #
        # "a+b" AND NOT "ab": the read has to happen through the same handle
        # the lock is held on. Opening a second handle to read would be the
        # check-then-act split the lock exists to close.
        with io.open(p, "a+b") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                if _needs_boundary(fh):
                    # ONE BYTE, APPENDED. Not a truncation and not a rewrite.
                    fh.seek(0, os.SEEK_END)
                    fh.write(b"\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                    JOURNAL_FAULTS["append:boundary_recovered"] += 1
                    console.out(
                        f"  [Spend journal] RECOVERED a missing line boundary "
                        f"in {p}: the final line carried no terminator, which "
                        f"a kill mid-write leaves behind. A newline was "
                        f"appended so it stands alone; nothing was truncated "
                        f"or rewritten. If it was an incomplete record it "
                        f"stays as evidence and is counted as unreadable.")
                fh.seek(0)
                before = fh.read()
                for rawline in before.splitlines():
                    if not rawline.strip():
                        continue
                    line = decode_journal_line(rawline)
                    if line is None:
                        continue
                    try:
                        existing = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(existing, dict) \
                            or existing.get("entry_id") != eid:
                        continue
                    if _same_charge(existing, payload):
                        return APPEND_DUPLICATE
                    JOURNAL_FAULTS["append:conflict"] += 1
                    console.out(
                        f"  [Spend journal] CONFLICT at {p}: entry_id {eid} "
                        f"already records a DIFFERENT charge "
                        f"(${existing.get('usd')} for "
                        f"{existing.get('scope')}/{existing.get('unit')}) than "
                        f"the one being appended (${payload.get('usd')} for "
                        f"{payload.get('scope')}/{payload.get('unit')}). "
                        f"Nothing was written and this charge is NOT recorded.")
                    return APPEND_CONFLICT
                blob = _serialize_entry(payload)
                fh.seek(0, os.SEEK_END)
                attempted["write"] = True
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
                # ── THE READ-BACK, UNDER THE SAME LOCK ──────────────────
                #
                # ** IT READS BACK THE LINE, NOT THE BYTES, AND THE DIFFERENCE
                # ** IS THE WHOLE CHECK. **
                #
                # The first version seeked to the pre-write offset and parsed
                # what it found there -- which is exactly the bytes this
                # function just serialized, so it parsed every time. Measured:
                # with boundary recovery disabled, a merged write still
                # reported `wrote`, because the JSON half of
                # `fragment + json` parses perfectly when you start reading at
                # the `{`. The read-back was confirming its own argument.
                #
                # The entry's LINE begins after the last newline in the file as
                # it stood BEFORE this write -- which the duplicate scan has
                # already read, so this costs nothing. On a clean file that is
                # the write offset; on an unrecovered torn tail it is the start
                # of the fragment, and the merged line then fails to parse,
                # which is the honest answer.
                fh.seek(before.rfind(b"\n") + 1)
                if not _reads_back_as(fh.read(), payload, eid, p):
                    return APPEND_UNCERTAIN
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        phase = "uncertain" if attempted["write"] else "failed"
        JOURNAL_FAULTS[f"append:{phase}:{type(exc).__name__}"] += 1
        console.out(f"  [Spend journal] could not record ${payload.get('usd')} "
                    f"to {p}: {type(exc).__name__}: {exc}. "
                    + ("The line MAY have been written; a retry under the same "
                       "entry_id will resolve it."
                       if attempted["write"]
                       else "Nothing was written."))
        return APPEND_UNCERTAIN if attempted["write"] else APPEND_FAILED
    log.info("spend_journal.append", reason=payload.get("kind"))
    return APPEND_WROTE


def _reads_back_as(written, payload, eid, p):
    """Does the LINE the entry landed on parse back as THIS charge? NEVER RAISES.

    ``written`` is the file from the start of that line to EOF -- not the bytes
    this module serialized. The distinction is the check: the JSON half of
    ``fragment + json`` parses perfectly if you start reading at the ``{``, so
    a version that re-read its own bytes confirmed every write including a
    merged one. Measured, by disabling boundary recovery: it reported ``wrote``.

    ``fh.write`` returning is not evidence that the file holds a record --
    the merged-line defect is a successful write that produced none. A
    ``wrote`` this function has not confirmed would advance a caller's total
    for money the file cannot return, which is the whole failure.

    A line that fails here is left ON DISK deliberately, exactly as a fragment
    is: it is what was written, this module did not decide it was wrong, and a
    retry under the frozen id resolves against whatever is really there.
    """
    for rawline in written.splitlines():
        if not rawline.strip():
            continue
        line = decode_journal_line(rawline)
        if line is None:
            break
        try:
            parsed = json.loads(line)
        except ValueError:
            break
        if not isinstance(parsed, dict) or parsed.get("entry_id") != eid:
            break
        if not _same_charge(parsed, payload):
            break
        return True
    JOURNAL_FAULTS["append:readback_unreadable"] += 1
    console.out(
        f"  [Spend journal] WROTE BUT COULD NOT READ BACK ${payload.get('usd')} "
        f"at {p}: the bytes appended for entry_id {eid} do not parse back as "
        f"that charge. This is reported as UNCERTAIN rather than written, so "
        f"the caller keeps the delta pending under the same id and a retry "
        f"resolves it against what is really on disk.")
    return False


def append(entry, path=None):
    """Append one entry unless its ``entry_id`` is already recorded.

    Returns True when a line was written and False otherwise. NEVER RAISES.

    **A THIN WRAPPER OVER ``append_with_outcome``, AND THE BOOL IS UNCHANGED
    FOR EVERY EXISTING CALLER.** ``wrote`` was True and everything else was
    False before this split, and that is still exactly the mapping -- including
    ``conflict``, which this function used to report as a silent duplicate and
    which is now COUNTED. That is new information and no changed return.

    IT IS KEPT RATHER THAN REPLACED because a bool is the right answer for
    ``record_batch``, ``record_run`` and the migration: each records one charge
    once and has nothing to retry. Only a caller that keeps a RUNNING TOTAL
    needs the four-valued answer, and there is exactly one of those.
    """
    return append_with_outcome(entry, path=path) == APPEND_WROTE


def entries_for_scope(budget, source, scope, unit_prefix=None, path=None):
    """Every well-formed entry recorded for one writer. NEVER RAISES.

    **READ BACK FROM THE FILE, WHICH IS THE POINT.** The journal is SHARED --
    other runs, other budgets and eighteen migration entries live in it -- so a
    caller verifying its own spend cannot sum the file and cannot trust its own
    counters either. This is the narrow reading: the entries whose budget,
    source and scope are this writer's, and whose ``unit`` begins with
    ``unit_prefix`` when one is given, which is what makes it THIS INVOCATION
    rather than every invocation that ever wrote to this scope.

    Duplicate ``entry_id``s are yielded ONCE, first occurrence winning, on
    ``total()``'s rule and for its reason: a file that has been hand-edited or
    concatenated is what produces them, and summing both would over-report.
    """
    out, seen = [], set()
    for e in read_entries(path):
        if e.get("budget") != budget or e.get("source") != source \
                or e.get("scope") != scope:
            continue
        if unit_prefix is not None \
                and not str(e.get("unit", "")).startswith(unit_prefix):
            continue
        eid = e.get("entry_id")
        if eid in seen:
            continue
        seen.add(eid)
        out.append(e)
    return out


def confirmed_usd_for_scope(budget, source, scope, unit_prefix=None,
                            path=None):
    """What the FILE says one writer recorded. NEVER RAISES.

    Amounts this build cannot read as money are SKIPPED and counted rather
    than coerced -- ``total()``'s rule. The direction of that skip is
    under-reporting, which for a verification means a residual is reported that
    may not be real; the opposite would be a residual hidden.
    """
    usd = 0.0
    for e in entries_for_scope(budget, source, scope, unit_prefix, path=path):
        amount = e.get("usd")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            JOURNAL_FAULTS[f"verify:bad_amount:{type(amount).__name__}"] += 1
            continue
        if amount != amount or amount < 0:
            JOURNAL_FAULTS[f"verify:bad_amount:{amount!r}"] += 1
            continue
        usd += float(amount)
    return usd


def _covered_batch_ids(entries, budget):
    """``{scope: {batch_id, ...}}`` a migration entry already accounts for."""
    covered = {}
    for e in entries:
        if e.get("budget") != budget or e.get("kind") != ENTRY_KIND_MIGRATION:
            continue
        ids = e.get("covers_batch_ids")
        if isinstance(ids, list):
            covered.setdefault(e.get("scope"), set()).update(
                str(x) for x in ids)
    return covered


def total(budget, path=None, entries=None):
    """``spend.LedgerSeed`` for ``budget``: everything ever recorded for it.

    Duplicate ``entry_id``s are summed ONCE, first occurrence winning, and the
    duplicate is counted -- a file that has been hand-edited or concatenated is
    the case that produces them, and both halves of that (do not double-charge,
    do say it happened) matter.

    ``unpriced`` counts entries whose amount is a FLOOR -- a pre-journal state
    file that recorded batches but no ``spend_usd``, which is every rater run
    from before ``STATE_SPEND_KEY`` existed. ``LedgerSeed.is_floor`` then makes
    ``spend.describe_seed`` print "A FLOOR, NOT A TOTAL", which is the honest
    thing to say about a cap enforced against it.
    """
    entries = read_entries(path) if entries is None else entries
    covered = _covered_batch_ids(entries, budget)
    seen = set()
    usd = 0.0
    rows = unpriced = 0
    scopes = set()
    for e in entries:
        if e.get("budget") != budget:
            continue
        kind = e.get("kind")
        if kind not in ENTRY_KINDS:
            JOURNAL_FAULTS[f"kind:{kind}"] += 1
            continue
        eid = e.get("entry_id")
        if eid in seen:
            JOURNAL_FAULTS["duplicate_entry_id"] += 1
            continue
        seen.add(eid)
        if kind == ENTRY_KIND_BATCH:
            unit = str(e.get("unit"))
            if unit in covered.get(e.get("scope"), ()):  # already migrated
                continue
        amount = e.get("usd")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            JOURNAL_FAULTS[f"bad_amount:{type(amount).__name__}"] += 1
            continue
        if amount != amount or amount < 0:               # NaN and negatives
            JOURNAL_FAULTS[f"bad_amount:{amount!r}"] += 1
            continue
        usd += float(amount)
        rows += 1
        scopes.add(e.get("scope"))
        if e.get("is_floor"):
            unpriced += 1
    if not rows:
        return spend.LedgerSeed()
    return spend.LedgerSeed(
        usd=usd, rows=rows, unpriced=unpriced, runs=len(scopes),
        source=SEED_SOURCE_FOR_BUDGET[budget])


SEED_SOURCE_FOR_BUDGET = {
    spend.SPEND_BUDGET_RATER: spend.SEED_SOURCE_JOURNAL_RATER,
    spend.SPEND_BUDGET_CAMPAIGN: spend.SEED_SOURCE_JOURNAL_CAMPAIGN,
}
"""Which ``spend.SEED_SOURCES`` member a journal reading of each budget is.

TOTAL over ``spend.SPEND_BUDGETS``, guarded at import below rather than by an
``assert``. A budget with no seed source would make ``total()`` raise inside
the one function that must never raise before a cent is spent, and a ``.get``
default would attribute one budget's cumulative history to the other.
"""

if set(SEED_SOURCE_FOR_BUDGET) != set(spend.SPEND_BUDGETS):
    raise RuntimeError(
        f"spend_journal.SEED_SOURCE_FOR_BUDGET must name every budget exactly "
        f"once: budgets={spend.SPEND_BUDGETS}, "
        f"table={tuple(SEED_SOURCE_FOR_BUDGET)}")


def record_batch(budget, source, scope, batch_id, usd, judge_model,
                 path=None):
    """Record one collected batch. Idempotent on ``(scope, batch_id)``."""
    return append({
        "entry_id": entry_id(budget, source, scope, batch_id),
        "kind": ENTRY_KIND_BATCH, "budget": budget, "source": source,
        "scope": scope, "unit": str(batch_id), "usd": float(usd),
        "judge_model": judge_model,
    }, path=path)


def record_run(budget, source, scope, unit, usd, judge_model, path=None):
    """Record one whole invocation. Idempotent on ``(scope, unit)``.

    For a harness that charges per RESPONSE and has no batch to key on. One
    line and one lock per invocation rather than per response: a per-response
    entry would be thousands of locks for a number that is only ever read as a
    total, and an interrupted invocation would still be missing its tail.

    THE COST OF THAT CHOICE IS STATED: an invocation killed before it reaches
    its recording point contributes NOTHING to the next one's cap. It is the
    under-recording direction, it is the same direction ragas' own missing seed
    already failed in, and it is bounded by one invocation.
    """
    return append({
        "entry_id": entry_id(budget, source, scope, unit),
        "kind": ENTRY_KIND_RUN, "budget": budget, "source": source,
        "scope": scope, "unit": str(unit), "usd": float(usd),
        "judge_model": judge_model,
    }, path=path)


RUN_CHECKPOINT_USD = 0.25
"""How much unrecorded spend a run may carry before it checkpoints.

**THE BOUND IS A MONEY BOUND, AND THAT IS THE DESIGN RATHER THAN A DEFAULT.**
What ``RunSpendCheckpointer`` exists to limit is the spend a HARD KILL loses,
which is a dollar quantity -- so the primary threshold is dollars and
``RUN_CHECKPOINT_SECONDS`` is a liveness backstop for a run that is slow and
cheap, never the guarantee.

**UNCALIBRATED, AND LABELLED ONE.** It is a holding value with an arithmetic
behind it rather than a measurement: the reference ragas run spent $9.29 over
414 seconds, so $0.25 is ~37 entries for that run and ~2.7% of it at risk. Both
ends of that matter -- a smaller value writes a line and takes an exclusive
lock per pair, which is exactly what ``record_run``'s docstring declines to do,
and a larger one is more money lost to a kill. Nothing has measured the right
number because nothing has measured what a kill actually costs in practice.
"""

RUN_CHECKPOINT_SECONDS = 60.0
"""How long a run may go without checkpointing, whatever it has spent.

The backstop, not the bound. A run that is judging slowly and cheaply would
otherwise sit under ``RUN_CHECKPOINT_USD`` for its whole length and record
nothing until the end, which is the state this whole mechanism removes. It
cannot make the money bound worse -- an extra checkpoint only ever records
MORE of what has been spent.
"""


class _PendingDelta(object):
    """One issued charge whose write is not CONFIRMED. Frozen, both fields.

    **THE FREEZE IS THE MECHANISM, NOT HOUSEKEEPING.** A retry can only be made
    safe by the duplicate check, and that check compares the entry id AND the
    amount -- so an entry retried under a new id would record the same money
    twice if the first attempt had in fact landed, and an entry retried under
    the same id with a DIFFERENT amount would be reported as a conflict and
    never confirm. New spend arriving while this is pending therefore goes into
    a SEPARATE delta with its own id; it never grows this one.
    """

    __slots__ = ("unit", "usd", "attempts", "last_outcome", "settled")

    def __init__(self, unit, usd):
        self.unit = unit
        self.usd = usd
        self.attempts = 0
        self.last_outcome = None
        # TERMINAL ONCE SET. A delta counted into the confirmed total twice is
        # silent double-counting -- the failure this whole class exists to
        # prevent, arrived at from the other side -- so the flag is what makes
        # `_settle` idempotent rather than an argument about its callers.
        self.settled = None

    def __repr__(self):
        return (f"<pending {self.unit} ${self.usd:.6f} "
                f"attempts={self.attempts} last={self.last_outcome}>")


class RunSpendCheckpointer(object):
    """Segmented ``run`` entries for a harness that has no batch to key on.

    **THE GAP THIS CLOSES IS NAMED IN ``record_run``'S OWN DOCSTRING:** "an
    invocation killed before it reaches its recording point contributes NOTHING
    to the next one's cap". A ``finally`` covers a clean exit, an exception and
    a Ctrl-C, because all three unwind; a SIGKILL, an OOM kill and a power loss
    do not, and a ragas invocation is tens of minutes of billed judging behind
    one entry written at the end of it.

    ═══ THE ENTRIES ARE DELTAS, AND THAT IS FORCED BY ``total()`` ═══

    ``total()`` SUMS every entry it has not already seen. So a checkpoint
    carrying the running TOTAL would be counted again by each later checkpoint
    that also carries it -- a $9 run written as five cumulative checkpoints
    would seed the next session's cap at $27. Each entry therefore records only
    what has been spent SINCE the last one, and the sum over an invocation's
    entries is that invocation's spend by construction.

    The alternative -- cumulative entries plus a ``max()`` in ``total()`` --
    was rejected: it would make the reader's arithmetic depend on the KIND of
    the entry it is summing, and ``total()`` is the one function every budget
    in this project is compared against.

    ═══ A DELTA IS RECORDED ONLY WHEN THE WRITE IS CONFIRMED ═══

    **THIS IS A REPAIR, AND THE DEFECT IT REPLACES WAS SHIPPED WITH AN ARGUMENT
    FOR IT.** The first version advanced the recorded total whether or not the
    line landed, under a comment reasoning that a REFUSED DUPLICATE must
    advance -- otherwise the next checkpoint re-offers the same delta and, if
    that one lands, records it twice. That reasoning is correct about
    duplicates and false about failures, and ``append`` returns ``False`` for
    both. Reproduced: a $0.30 delta whose append failed was never retried, the
    finalize computed its remainder against the already-advanced total and
    wrote **$0.00**, and this class reported ``recorded`` of $0.50 against a
    journal holding $0.20. Nothing raised and no counter moved.

    ``append_with_outcome`` is what makes the distinction expressible, and each
    of its outcomes gets the only treatment that is true of it:

      * ``wrote`` and ``duplicate`` -- CONFIRMED. The money is in the file,
        either because this attempt put it there or because an earlier one did
        and the id, the charge identity and the amount all match. Advance.
      * ``conflict`` -- an entry with this id records something ELSE. This
        charge is not recorded by it and never will be, so the delta is moved
        to ``conflicted`` and is NEVER advanced past. It is a named fault and
        it stays unresolved: retrying cannot fix a collision, and inventing a
        new id would abandon the question of which charge the existing entry
        is.
      * ``failed`` / ``uncertain`` -- PENDING. The delta keeps its id and its
        amount and is retried at the next checkpoint and at finalization.

    ═══ WHY NO DOUBLE COUNT, IN THE FOUR CASES THAT MATTER ═══

    ``entry_id`` is ``sha256(budget, source, scope, unit)``, so two appends
    collide exactly when all four agree. ``unit`` here is
    ``f"{prefix}#{seq}"`` with ``prefix`` an invocation stamp taken once at
    start and ``seq`` a counter, so within an invocation every entry is
    distinct and across invocations every prefix is.

      * **clean completion** -- checkpoints at d1..dk, then ``finalize``
        writes ``measured - issued``. The sum is ``measured``. Each unit is
        written once because each is distinct.
      * **exception abort** -- identical, because ``finalize`` is called from
        the caller's ``finally``. The sum is what was spent before the raise.
      * **hard kill, then a fresh run** -- the killed invocation's entries are
        on disk and sum to its last confirmed checkpoint. The new invocation
        takes a NEW prefix, so no id collides and nothing is re-summed.
      * **hard kill, then ``--resume``** -- the same, plus the resume does not
        re-judge the pairs it carries forward, so the money is not spent twice
        for the ledger to record twice.

    **AND THE ONE COLLISION THAT IS LEFT FAILS SAFE.** Two invocations sharing
    an output directory AND a microsecond-resolution UTC stamp would compute
    the same ids -- unreachable for a harness whose invocations are minutes
    long. It is no longer silently absorbed either: the second attempt's amount
    will not match, so it reports ``conflict`` and is counted rather than being
    read as "already recorded".

    ═══ WHY ``ENTRY_KIND_RUN`` AND NOT A FOURTH KIND ═══

    These are segments of a run rather than whole runs, so a fourth kind is the
    tidy answer and it is the wrong one. ``ENTRY_KINDS`` is CLOSED and
    ``total()`` COUNTS AND SKIPS a kind it does not know -- so a build that
    predates the new kind would silently drop every segment, which is
    under-recording, the unsafe direction. Reusing ``run`` means an older
    reader sums these correctly without knowing they are segments. What makes
    an entry idempotent is the derived ``entry_id``, which is kind-independent.

    ``ENTRY_KIND_BATCH`` is deliberately NOT used even though it is the other
    idempotent kind: ``total()`` gives it special handling -- an entry whose
    unit is inside a migration's ``covers_batch_ids`` for the same scope is
    skipped -- which is machinery for the rater's Batch API and has no meaning
    for a ragas segment.

    ═══ WHAT THE BOUND ACTUALLY IS, WHICH IS NOT WHAT THIS SAID ═══

    **THE EARLIER CLAIM WAS THAT A KILL LOSES AT MOST ONE THRESHOLD PLUS THE
    PAIR IN FLIGHT. THAT IS WRONG IN TWO WAYS AND BOTH WERE MEASURED.**

    ``checkpoint`` IS NOT A TIMER. It runs when the caller calls it, which in
    ``score_all`` is on PAIR COMPLETION inside the event loop. So
    ``RUN_CHECKPOINT_SECONDS`` is not a wall-clock backstop at all: it is
    evaluated only at the next completion, and a run whose pairs are all
    hanging records nothing further no matter how long it waits. The
    unrecorded spend of a stalled run is unbounded IN TIME.

    AND CONCURRENT PAIRS ARE OUTSIDE ANY THRESHOLD. The ledger is charged when
    a RESPONSE arrives, inside the judge's own recording seam; the checkpoint
    runs when a PAIR completes. At ``--max-workers N`` up to N responses can be
    charged between two completions, so the unconfirmed amount at any instant
    is ``min_usd`` plus whatever those in-flight pairs have already been
    charged -- which is proportional to N and to the price of a pair, and is
    NOT bounded by ``RUN_CHECKPOINT_USD``.

    **WHAT IT DOES PROMISE**, which is smaller and true: every completed pair's
    spend is offered to the journal at the first completion after the running
    delta reaches ``RUN_CHECKPOINT_USD``, and once offered it is retried until
    confirmed or until finalization reports it. What a kill loses is the spend
    that was never offered -- the sub-threshold remainder plus the in-flight
    charges -- and ``tests/test_spend_hard_kill_journaling.py`` section 6
    MEASURES that at the configured worker count rather than bounding it by
    argument.

    ═══ WHAT IT COSTS ═══

    One small append under one exclusive ``flock``, at most once per
    ``RUN_CHECKPOINT_USD`` of spend, plus one retry attempt per pending delta.
    Pending deltas only accumulate while writes are FAILING, and each retry is
    one failed ``open`` -- so the O(n^2) that implies is bounded by
    ``run spend / RUN_CHECKPOINT_USD`` attempts and is microseconds. In ragas
    it is called from inside an asyncio coroutine, so it blocks the event loop
    for the length of that write -- the same trade ``ScoreJournal.flush``
    already makes and argues, and strictly rarer than it.
    """

    def __init__(self, budget, source, scope, prefix, judge_model,
                 path=None, min_usd=None, min_seconds=None, clock=None):
        self.budget = budget
        self.source = source
        self.scope = scope
        self.prefix = str(prefix)
        self.judge_model = judge_model
        self.path = path
        self.min_usd = RUN_CHECKPOINT_USD if min_usd is None else float(min_usd)
        self.min_seconds = (RUN_CHECKPOINT_SECONDS if min_seconds is None
                            else float(min_seconds))
        # INJECTABLE SO THE BACKSTOP CAN BE DRIVEN. A test that had to sleep 60
        # seconds to exercise a time threshold would not be run; one that
        # advances a fake clock measures the same branch.
        self._clock = clock or time.monotonic
        # A LOCK ALTHOUGH ITS ONE CALLER IS SINGLE-THREADED, on
        # `fhir/clean.py`'s stated precedent: the accounting below is a
        # read-modify-write over several fields and `spend.SpendLedger` -- the
        # thing this reads -- documents itself thread-safe, so a checkpointer
        # over it that was not would be a trap for whoever calls this from the
        # rater's pool next.
        self._lock = threading.Lock()
        # ── THE THREE TOTALS, AND THEY ARE AMOUNTS RATHER THAN LEVELS ──
        # An amount is order-independent: a pending delta that confirms late
        # adds its own money wherever it lands. A "level" (a measured reading
        # each entry advances to) is not, and would make a late confirmation
        # either skip or re-cover the deltas issued after it.
        self._confirmed_usd = 0.0     # in the file, verified by outcome
        self._issued_usd = 0.0        # every delta ever cut, confirmed or not
        self._pending = []            # _PendingDelta, frozen id and amount
        self._conflicted = []         # _PendingDelta that can never confirm
        self._seq = 0
        self._last = self._clock()
        self.entries = []             # one record per attempt, for the tests
        self.finalized = False
        self.verified_usd = None      # set by finalize, READ BACK from disk
        self.residual_usd = None      # measured - verified_usd, at finalize

    @property
    def recorded(self):
        """The spend CONFIRMED in the journal. Never advanced on a failure."""
        return self._confirmed_usd

    @property
    def issued(self):
        """Every delta cut so far, whether or not its write is confirmed."""
        return self._issued_usd

    @property
    def unconfirmed(self):
        """Money this invocation cut a delta for and cannot prove is recorded.

        Pending plus conflicted. It is the number ``finalize`` reports loudly
        when it is non-zero, and it is deliberately NOT folded into
        ``recorded``: a caller asking what is on disk must not be told about
        money that is not.
        """
        return self._issued_usd - self._confirmed_usd

    @property
    def pending(self):
        """The frozen deltas awaiting confirmation, oldest first."""
        return tuple(self._pending)

    @property
    def conflicted(self):
        """Deltas whose id names a DIFFERENT charge. Never retried."""
        return tuple(self._conflicted)

    def _delta(self, measured):
        """Unrecorded spend not yet cut into a delta, or ``None``, counted.

        **SEPARATE FROM THE THRESHOLD TEST, AND THAT IS A FIX RATHER THAN A
        FACTORING.** An earlier version validated inside the writer and let
        ``checkpoint`` decide "is it due" first -- so a ``None``, a ``NaN`` or
        a ledger that went backwards produced ``due = False``, fell into the
        time gate, and returned without ever reaching the code that counts it.

        IT IS MEASURED AGAINST ``_issued_usd`` AND NOT ``_confirmed_usd``, which
        is what stops a pending delta being cut twice: money already carried by
        a frozen entry has been accounted for even though it is not yet on disk.
        """
        if not isinstance(measured, (int, float)) or isinstance(measured, bool):
            JOURNAL_FAULTS[f"checkpoint:bad_measured:"
                           f"{type(measured).__name__}"] += 1
            return None
        measured = float(measured)
        if measured != measured:                                    # NaN
            JOURNAL_FAULTS["checkpoint:bad_measured:nan"] += 1
            return None
        delta = measured - self._issued_usd
        if delta < 0:
            # ONLY REACHABLE THROUGH `SPEND_LEDGER.reset()` MID-RUN, which no
            # harness does. Counted rather than clamped: a ledger that went
            # backwards under a checkpointer is a defect somewhere else, and
            # writing a negative into a cap would be worse than not writing.
            JOURNAL_FAULTS["checkpoint:negative_delta"] += 1
            return None
        return delta

    def _attempt(self, item):
        """One append attempt for one frozen delta. Returns its outcome.

        Records the attempt in ``entries`` whatever happened -- a test that can
        see only the successful attempts cannot tell a retry from a first try.
        """
        item.attempts += 1
        outcome = append_with_outcome({
            "entry_id": entry_id(self.budget, self.source, self.scope,
                                 item.unit),
            "kind": ENTRY_KIND_RUN, "budget": self.budget,
            "source": self.source, "scope": self.scope, "unit": item.unit,
            "usd": item.usd, "judge_model": self.judge_model,
        }, path=self.path)
        item.last_outcome = outcome
        self._last = self._clock()
        self.entries.append({"unit": item.unit, "usd": item.usd,
                             "outcome": outcome, "attempt": item.attempts})
        return outcome

    def _settle(self, item, outcome):
        """Move ``item`` to confirmed, conflicted or pending. Returns True on
        CONFIRMED, which is the only outcome that advances the total."""
        if item.settled is not None:
            # ALREADY TERMINAL. Not reachable through `_flush_pending` or
            # `_cut`, each of which settles an item once -- and guarded anyway,
            # because the cost of being wrong is a total that silently
            # over-counts, which is the same class of defect as the one this
            # class was repaired for.
            JOURNAL_FAULTS["checkpoint:resettle_ignored"] += 1
            return item.settled == APPEND_WROTE \
                or item.settled == APPEND_DUPLICATE
        if outcome in (APPEND_WROTE, APPEND_DUPLICATE):
            item.settled = outcome
            self._confirmed_usd += item.usd
            if item in self._pending:
                self._pending.remove(item)
            return True
        if outcome == APPEND_CONFLICT:
            item.settled = outcome
            if item in self._pending:
                self._pending.remove(item)
            if item not in self._conflicted:
                self._conflicted.append(item)
            JOURNAL_FAULTS["checkpoint:conflict_unresolved"] += 1
            return False
        if item not in self._pending:
            self._pending.append(item)
        JOURNAL_FAULTS[f"checkpoint:{outcome}"] += 1
        return False

    def _flush_pending(self):
        """Retry every pending delta, oldest first. Returns how many confirmed.

        OLDEST FIRST AND ALL OF THEM. The order is the order the money was
        spent in, which is what a reader of the file expects; and one failing
        delta does not stop the others, because each carries its own id and its
        own money and skipping the rest would lose more than it protects.
        """
        confirmed = 0
        for item in list(self._pending):
            if self._settle(item, self._attempt(item)):
                confirmed += 1
        return confirmed

    def _cut(self, usd):
        """Freeze a new delta under the next sequence number and attempt it."""
        item = _PendingDelta(f"{self.prefix}#{self._seq}", usd)
        self._seq += 1
        self._issued_usd += usd
        self._settle(item, self._attempt(item))
        return item

    def checkpoint(self, measured):
        """Record a delta if enough has accumulated. NEVER RAISES.

        Returns True when this call CONFIRMED anything -- a new delta or a
        pending one. Safe to call as often as the caller likes; the thresholds
        are what make it cheap.

        A PENDING DELTA MAKES THE CALL DUE WHATEVER THE THRESHOLDS SAY. Money
        already cut is money this process is trying to prove it recorded, and
        deferring that behind a spend threshold would leave it unproved for as
        long as the run is cheap.
        """
        with self._lock:
            if self.finalized:
                JOURNAL_FAULTS["checkpoint:after_finalize"] += 1
                return False
            delta = self._delta(measured)
            if delta is None:               # counted in `_delta`
                return False
            due = (bool(self._pending)
                   or (delta > 0 and delta >= self.min_usd)
                   or (delta > 0
                       and (self._clock() - self._last) >= self.min_seconds))
            if not due:
                return False
            confirmed = self._flush_pending()
            if delta > 0:
                item = self._cut(delta)
                if item.last_outcome in (APPEND_WROTE, APPEND_DUPLICATE):
                    confirmed += 1
            return confirmed > 0

    def finalize(self, measured):
        """Flush everything, then VERIFY against the file. NEVER RAISES.

        Idempotent: a second call does nothing, because a caller with a
        ``finally`` inside another ``finally`` must not produce a second
        terminal entry.

        **IT WRITES EVEN A ZERO DELTA**, which keeps this a strict superset of
        the behaviour it replaces: ``record_run`` in a ``finally`` left exactly
        one entry per invocation, including for an invocation that spent
        nothing, so an operator could ask "did this run record itself" and get
        an answer. A finalize that skipped a zero delta would make a $0 run and
        a run whose journaling was never wired up look identical.

        **AND THE VERIFICATION IS READ BACK FROM THE FILE, NOT FROM THESE
        COUNTERS.** Counters are what the defect this class was repaired for
        got wrong; a total that verified itself against its own arithmetic
        would have reported the lost $0.30 as recorded just as confidently. The
        journal is SHARED -- other runs, other budgets and the migration
        entries live in it -- so the reading is narrowed to this budget, this
        source, this scope and this invocation's own ``prefix#`` units.

        A residual is REPORTED, never absorbed: ``recorded`` is set to what the
        file says, so it can go DOWN here, which is the honest direction.
        """
        with self._lock:
            if self.finalized:
                return False
            self._flush_pending()
            delta = self._delta(measured)
            if delta is None:
                # A BAD SHAPE STILL TERMINATES THE RUN. `_delta` has counted
                # it; cutting a $0 delta keeps "this invocation recorded
                # itself" answerable, which is why finalize forces.
                delta = 0.0
            # THE TERMINAL $0 MARKER IS GUARDED ON ``_seq`` AND NOT ON
            # ``entries``, and the difference is real: ``entries`` records
            # ATTEMPTS, so a run whose every write failed has entries and no
            # issued delta would be skipped. ``_seq`` counts deltas CUT, which
            # is the question -- "did this invocation ever offer anything to
            # the journal" -- and it is what keeps "did this run record itself"
            # answerable for a run that spent nothing.
            if delta > 0 or self._seq == 0:
                self._cut(delta)
            wrote = bool(self.entries)

            # ── THE VERIFICATION ──────────────────────────────────────
            verified = confirmed_usd_for_scope(
                self.budget, self.source, self.scope,
                unit_prefix=f"{self.prefix}#", path=self.path)
            self.verified_usd = verified
            self._confirmed_usd = verified
            target = measured if isinstance(measured, (int, float)) \
                and not isinstance(measured, bool) and measured == measured \
                else self._issued_usd
            residual = float(target) - verified
            self.residual_usd = residual
            if residual < -_AMOUNT_EPSILON:
                # THE FILE HOLDS MORE FOR THIS INVOCATION THAN IT SPENT, which
                # is only reachable through a PREFIX COLLISION -- another
                # invocation of the same scope under the same microsecond UTC
                # stamp. Reported rather than passed over: the earlier design
                # note said a collision "fails safe", and it does for
                # DOUBLE-COUNTING (the amounts differ, so the second attempt is
                # a conflict) -- but a reader of this scope's total is being
                # handed two runs' money under one invocation's units, and only
                # this comparison can see it.
                JOURNAL_FAULTS["checkpoint:overrecorded_scope"] += 1
                console.out(
                    f"  [Spend journal] OVER-RECORDED: the journal at "
                    f"{resolved_journal_path(self.path)} holds "
                    f"${verified:.6f} under units {self.prefix}#* for scope "
                    f"{self.scope!r} and this invocation charged only "
                    f"${float(target):.6f}. Another invocation almost "
                    f"certainly shares this prefix; the cap will over-count "
                    f"by ${-residual:.6f}.")
            if residual > _AMOUNT_EPSILON:
                JOURNAL_FAULTS["checkpoint:unconfirmed_residual"] += 1
                console.out(
                    f"  [Spend journal] UNCONFIRMED SPEND: this invocation "
                    f"charged ${float(target):.6f} and the journal at "
                    f"{resolved_journal_path(self.path)} records "
                    f"${verified:.6f} for it -- ${residual:.6f} IS NOT "
                    f"RECORDED and the next session's cap will not see it. "
                    f"{len(self._pending)} delta(s) still pending, "
                    f"{len(self._conflicted)} in conflict. Scope "
                    f"{self.scope!r}, units {self.prefix}#*.")
                log.error("spend_journal.unconfirmed_residual",
                          reason=f"{residual:.6f}")
            self.finalized = True
            return wrote


STATE_BASENAMES = ("rater_state.json", "rater_state_blind.json")
"""The rater state files a migration reads. Both modes, because both spend.

Taken from ``rater.STATE_FILENAME`` / ``STATE_FILENAME_BLIND`` conceptually and
written out here rather than imported, deliberately: ``rater`` imports this
module, so importing it back would be a cycle, and this tuple is checked
against those two constants by ``tests/test_spend_journal.py`` rather than
being trusted.
"""


def find_state_files(root=None):
    """Every rater state file under ``root``, sorted. Reads nothing else.

    NEVER RAISES, for ``resolved_journal_path``'s reason: the default root is
    the same lazy glob, and a machine with no sibling tree has no state files
    rather than a broken migration.
    """
    if root is None:
        try:
            root = paths.testing_evaluation_path
        except Exception as exc:                                # noqa: BLE001
            JOURNAL_FAULTS[f"unresolvable_root:{type(exc).__name__}"] += 1
            return []
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name in STATE_BASENAMES:
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def bootstrap_from_state_files(budget=spend.SPEND_BUDGET_RATER,
                               source=spend.SPEND_SOURCE_RATER,
                               root=None, path=None, out=None):
    """Seed the journal from the state files on disk. Idempotent. NEVER RAISES.

    **SUMMED FROM THE ARTIFACTS, NEVER FROM PROSE.** Every amount here is read
    out of a ``rater_state.json`` on disk; no total from any report is typed
    into this project, because a number in a note is a number that was true
    when it was written.

    IDEMPOTENT TWICE OVER. Each entry's id is derived from its state file's
    path, so re-running appends nothing; and each entry records the batch ids
    that file already covers, so a session that later resumes one of those
    batches does not charge it a second time.

    Returns ``(appended, skipped, usd)``.
    """
    emit = out or console.out
    written = skipped = 0
    usd = 0.0
    for state_path in find_state_files(root):
        try:
            with io.open(state_path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError) as exc:
            JOURNAL_FAULTS[f"migrate:{type(exc).__name__}"] += 1
            emit(f"  [Spend journal] could not read {state_path}: "
                 f"{type(exc).__name__}")
            continue
        if not isinstance(state, dict):
            JOURNAL_FAULTS["migrate:not_an_object"] += 1
            continue
        amount = state.get("spend_usd")
        batches = state.get("batches")
        batch_ids = [str(b.get("id")) for b in batches
                     if isinstance(b, dict) and b.get("id")] \
            if isinstance(batches, list) else []
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) \
                or amount != amount or amount < 0:
            amount = 0.0
        # A FLOOR when the file records batches and no money. Those are runs
        # from before `STATE_SPEND_KEY` existed: they DID spend, the artifact
        # cannot say how much, and recording 0 without saying it is a floor
        # would present an unknown as a measurement.
        is_floor = bool(batch_ids) and not amount
        ok = append({
            "entry_id": entry_id(budget, source, state_path, "migration"),
            "kind": ENTRY_KIND_MIGRATION, "budget": budget, "source": source,
            "scope": state_path, "unit": "migration",
            "usd": float(amount), "judge_model": state.get("model"),
            "state_file": state_path,
            "state_file_mtime_utc": _file_mtime_utc(state_path),
            "covers_batch_ids": batch_ids,
            "is_floor": is_floor,
        }, path=path)
        if ok:
            written += 1
            usd += float(amount)
        else:
            skipped += 1
    emit(f"  [Spend journal] migration: {written} state file(s) recorded, "
         f"{skipped} already present, ${usd:.4f} added.")
    return written, skipped, usd


def _file_mtime_utc(p):
    try:
        return datetime.fromtimestamp(os.path.getmtime(p),
                                      timezone.utc).isoformat()
    except OSError:
        return None


def describe(budget, path=None):
    """One banner line about what this budget has spent, ever."""
    seed = total(budget, path=path)
    cap = spend.budget_cap(budget)
    where = resolved_journal_path(path) or "<unresolvable>"
    if cap is None:
        return (f"[Spend journal] {budget}: ${seed.usd:.4f} recorded across "
                f"{seed.runs} session(s); NO CAP is in force. {where}")
    return (f"[Spend journal] {budget}: ${seed.usd:.4f} recorded across "
            f"{seed.runs} session(s), ${max(cap - seed.usd, 0.0):.4f} of "
            f"${cap:.2f} remaining. {where}")
