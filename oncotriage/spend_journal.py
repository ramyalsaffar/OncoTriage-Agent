"""The CROSS-PROCESS spend record: one append-only file, one owner.

``oncotriage/spend.py``'s ledger is PROCESS-LOCAL by design and says so. That
is right for the campaign budget, which has a cross-process record of its own --
``database_logger.campaign_billing_total`` reads the batch campaign's cumulative
``billing_attempts`` record -- and it was WRONG for the judge, which has
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

**AND THE MIGRATION ENTRY LISTS THE BATCH IDS OF ITS STATE FILE.** A state
file that existed before this journal did has its whole spend in ONE migration
entry; if that session is later resumed and re-collects one of its old batches,
the per-batch entry would be new and could double-count against the migration
entry. ``covers_batch_ids`` is read out of ``state["batches"]`` at migration
time.

**A LISTED BATCH IS NOT A REPRESENTED ONE, AND ``total`` USED TO READ IT AS
ONE.** The listing is every batch the state file named, including one that was
submitted and not yet collected when the migration ran -- whose money is NOT in
the migrated amount. Skipping every listed batch's later entry meant that
money never reached the cap. Coverage is now PER BATCH and decided by
``migration_coverage``: a batch is represented only when the migrated amount
provably includes it, is countable when the amount provably does not, and makes
the budget UNVERIFIED when neither can be established. See that function.

**AND A STATE FILE IS IDENTIFIED, NOT SPELLED.** The rater names its state file
as ``abspath(--output-dir)``; the migration names it by walking the
evaluation-runs root. The two strings differ whenever a symlink, a ``..`` or a
doubled separator is involved, and every comparison here used to be string
equality. ``scope_identity`` is the one resolution rule, applied at READ time
to every batch and migration entry, so historical entries are matched without
being rewritten.
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


# ── WHICH STATE FILE AN ENTRY IS ABOUT, WHATEVER IT WAS SPELLED AS ─────────

IDENTITY_KINDS = (ENTRY_KIND_MIGRATION, ENTRY_KIND_BATCH)
"""The entry kinds whose ``scope`` is a STATE FILE and is therefore matched by
``scope_identity`` rather than by string. ``run`` is not among them: its scope
is an output directory, its units carry a per-invocation prefix, and two
spellings of one run scope cannot collide on a unit."""


def _scope_identity_uncached(scope):
    if not isinstance(scope, str) or not scope or "\x00" in scope:
        return None
    expanded = os.path.expanduser(scope)
    # A RELATIVE scope resolves against whatever the reader's working directory
    # happens to be, which is a different file for every caller. It is not a
    # spelling of anything, so it is not identified.
    if not os.path.isabs(expanded):
        return None
    try:
        real = os.path.realpath(expanded)
    except (OSError, ValueError):
        return None
    parent, base = os.path.split(real)
    if not base:
        return None
    try:
        st = os.stat(parent)
    except (FileNotFoundError, NotADirectoryError):
        # Nothing on disk to consult: the normalized, symlink-resolved string
        # is the identity. Two spellings of a directory that does not exist
        # still meet here whenever they differ only by `..`, `.`, `//` or a
        # symlink above the missing part.
        return ("path", real)
    except (OSError, ValueError):
        return None
    # AN EXISTING PARENT DIRECTORY IS IDENTIFIED BY (device, inode). That
    # also unifies spellings `realpath` cannot -- a case variant on a
    # case-insensitive filesystem, a Unicode-normalization variant, a bind
    # mount -- and it is the DIRECTORY'S inode, not the file's, because the
    # rater replaces its state file atomically and a file inode changes on
    # every write. The basename is kept verbatim: both writers use the rater's
    # own two filename constants.
    return ("dir", st.st_dev, st.st_ino, base)


def scope_identity(scope, memo=None):
    """The identity of the state file ``scope`` names, or None. NEVER RAISES.

    **THE ONE RESOLUTION RULE**, applied everywhere two spellings of a state
    file meet: ``total``, ``recorded_batch_ids``, the duplicate check inside
    ``append_with_outcome``, and the journal-era recovery. It is applied at READ
    time to what is already on disk; nothing historical is rewritten.

    None means the identity cannot be established -- not a string, a RELATIVE
    path, or a parent that exists and cannot be examined -- and every caller
    treats that as unverified rather than guessing a match.

    ``memo`` is a per-call ``{scope: identity}`` so a journal of many entries
    naming a few files stats each file once. It is deliberately not a module
    cache: a directory created between two calls must be seen.
    """
    if memo is not None and isinstance(scope, str):
        if scope not in memo:
            memo[scope] = _scope_identity_uncached(scope)
        return memo[scope]
    return _scope_identity_uncached(scope)


def _describe_scope(scope):
    """``scope`` resolved for a message; the raw value when it cannot be."""
    try:
        return os.path.realpath(os.path.expanduser(scope))
    except Exception:                                           # noqa: BLE001
        return repr(scope)


def _valid_amount(amount):
    return (not isinstance(amount, bool) and isinstance(amount, (int, float))
            and amount == amount and amount >= 0)


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

    ``read_entries_report(path)[0]``. A caller that computes a BUDGET from the
    result must use ``read_entries_report`` instead, because this form throws
    away the one fact that decides whether that budget can be trusted: what
    was skipped.
    """
    return read_entries_report(path)[0]


def read_entries_report(path=None):
    """``(entries, unreadable)``: every well-formed entry, and what was not.

    ``unreadable`` is a list of short reason strings, one per item that could
    not be read -- an undecodable, unparseable or unknown-schema LINE, an
    unresolvable path, a file that exists and could not be opened. An ABSENT
    file is not in it: nothing has been recorded yet, which is a reading and
    not a failure to read. A LINE-level item cannot be attributed to a budget
    (its budget field is exactly what could not be read), so a caller must
    treat it as bearing on every budget.

    NEVER RAISES. Every item is also counted into ``JOURNAL_FAULTS`` exactly as
    before this report existed; the list adds names, it does not move counts.

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
    unreadable = []
    p = resolved_journal_path(path)
    if p is None:
        # COUNTED by `resolved_journal_path`. Named here because "no journal"
        # and "a journal nobody could locate" are the same empty list, and only
        # one of them is a record that may hold money.
        unreadable.append(f"the journal path could not be resolved (set "
                          f"{settings.ENV_SPEND_JOURNAL} to the journal file, "
                          f"or restore the evaluation-runs directory)")
        return [], unreadable
    try:
        with io.open(p, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return [], unreadable
    except OSError as exc:
        JOURNAL_FAULTS[f"read:{type(exc).__name__}"] += 1
        console.out(f"  [Spend journal] could not read {p}: "
                    f"{type(exc).__name__}: {exc}")
        unreadable.append(f"journal file {p} could not be opened "
                          f"({type(exc).__name__})")
        return [], unreadable
    out = []
    for lineno, rawline in enumerate(raw.splitlines(), 1):
        if not rawline.strip():
            continue
        line = decode_journal_line(rawline)
        if line is None:
            unreadable.append(f"journal line {lineno}: not UTF-8")
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            JOURNAL_FAULTS["parse:not_json"] += 1
            unreadable.append(f"journal line {lineno}: not JSON")
            continue
        if not isinstance(entry, dict):
            JOURNAL_FAULTS[f"parse:{type(entry).__name__}"] += 1
            unreadable.append(f"journal line {lineno}: a JSON "
                              f"{type(entry).__name__}, not an entry")
            continue
        version = entry.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool):
            JOURNAL_FAULTS["schema:absent"] += 1
            unreadable.append(f"journal line {lineno}: no schema_version")
            continue
        if version > SCHEMA_VERSION:
            JOURNAL_FAULTS[f"schema:from_the_future:{version}"] += 1
            unreadable.append(f"journal line {lineno}: schema_version "
                              f"{version} is newer than this build's "
                              f"{SCHEMA_VERSION}")
            continue
        entry["_lineno"] = lineno
        out.append(entry)
    return out, unreadable


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


def _same_state_file(existing, payload, memo=None):
    """Do two entries name the same state file? String first, then identity.

    Identity is consulted only for ``IDENTITY_KINDS`` of one kind, and only
    when BOTH identities are established: an unidentifiable scope matches
    nothing but its own exact string.
    """
    if existing.get("scope") == payload.get("scope"):
        return True
    kind = payload.get("kind")
    if kind not in IDENTITY_KINDS or existing.get("kind") != kind:
        return False
    a = scope_identity(existing.get("scope"), memo)
    return a is not None and a == scope_identity(payload.get("scope"), memo)


def _equivalent_charge(existing, payload, memo=None):
    """Is ``existing`` the SAME CHARGE IDENTITY as ``payload`` under another
    spelling of its state file? Amount not compared -- see ``_same_charge``."""
    for field in ("budget", "source", "unit", "kind"):
        if existing.get(field) != payload.get(field):
            return False
    return (existing.get("scope") != payload.get("scope")
            and _same_state_file(existing, payload, memo))


def _same_charge(existing, payload, memo=None):
    """Does an entry already in the file record the SAME charge as ``payload``?

    Compares the four fields ``entry_id`` is DERIVED from plus the amount --
    "charge identity AND amount". It deliberately does NOT compare the whole
    dict: ``recorded_at_utc`` differs on every attempt, so a whole-dict
    comparison would report every retry as a conflict, which is the one thing
    that must not happen to a retry.

    THE SCOPE IS COMPARED AS A STATE FILE, NOT AS A STRING, for the kinds whose
    scope is one (``_same_state_file``). Two spellings of one state file are
    one charge identity; comparing their strings is what let a recovery under
    the migration's spelling of a file re-record a batch the rater had already
    recorded under its own.
    """
    for field in ("budget", "source", "unit"):
        if existing.get(field) != payload.get(field):
            return False
    if not _same_state_file(existing, payload, memo):
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
                # THE SAME CHARGE UNDER ANOTHER SPELLING OF ITS STATE FILE IS
                # THE SAME CHARGE. Its entry_id differs (the id is derived from
                # the spelling), so an id-only scan would append it a second
                # time; this is what keeps a recovery run under either spelling
                # idempotent. A same-amount match anywhere wins over a
                # different-amount one: the money is recorded.
                memo = {}
                matches = []
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
                    if not isinstance(existing, dict):
                        continue
                    if existing.get("entry_id") != eid \
                            and not _equivalent_charge(existing, payload, memo):
                        continue
                    if _same_charge(existing, payload, memo):
                        return APPEND_DUPLICATE
                    matches.append(existing)
                for existing in matches[:1]:
                    JOURNAL_FAULTS["append:conflict"] += 1
                    console.out(
                        f"  [Spend journal] CONFLICT at {p}: entry_id "
                        f"{existing.get('entry_id')} already records a "
                        f"DIFFERENT amount for this charge "
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


COVERAGE_ZERO_AMOUNT = "zero_amount"
COVERAGE_SINGLE_BATCH = "single_batch"
COVERAGE_SEVERAL_BATCHES = "several_batches"
COVERAGE_NO_USABLE_LISTING = "no_usable_listing"

COVERAGE_RULES = (COVERAGE_ZERO_AMOUNT, COVERAGE_SINGLE_BATCH,
                  COVERAGE_SEVERAL_BATCHES, COVERAGE_NO_USABLE_LISTING)
"""Which of a migration entry's listed batches its AMOUNT includes. CLOSED.

A migration entry is ``usd`` (the state file's ``spend_usd``, summed at
collection time over the batches it had COLLECTED) beside ``covers_batch_ids``
(every batch the state file LISTED, collected or not). Those are two different
sets, and the entry records only the first one's SUM. So:

``zero_amount``        the amount is 0 -- nothing collected, or a pre-key FLOOR.
                       NO listed batch is represented: a later collection of
                       any of them is money the migration never held, and it
                       counts, once.
``single_batch``       a positive amount beside exactly ONE listed batch. Only
                       that batch can have produced it, so it IS represented and
                       its later entry is not counted again.
``several_batches``    a positive amount beside two or more listed batches. The
                       entry cannot say which of them the amount includes -- a
                       retry batch submitted and never collected is exactly this
                       shape. COVERAGE IS NOT ESTABLISHED: a later entry for any
                       of them is NOT counted and marks the budget UNVERIFIED,
                       which refuses new paid work on it.
``no_usable_listing``  a positive amount and no readable listing (absent, not a
                       list, empty, or holding a non-string id). Any later batch
                       entry for that state file is treated as ``several``.

NOTHING IS GUESSED. The two rules that decide are consequences of how the
amount was accumulated, not assumptions about which batches were collected;
the third case is where they stop, and it is reported rather than resolved.
"""


def migration_coverage(entry):
    """``{"rule", "represented", "ambiguous", "all_ambiguous"}`` for one
    migration entry whose amount has already been validated. NEVER RAISES."""
    amount = entry.get("usd") if isinstance(entry, dict) else None
    if not _valid_amount(amount) or float(amount) <= _AMOUNT_EPSILON:
        return {"rule": COVERAGE_ZERO_AMOUNT, "represented": frozenset(),
                "ambiguous": frozenset(), "all_ambiguous": False}
    ids = entry.get("covers_batch_ids")
    if not isinstance(ids, list) or not ids \
            or not all(isinstance(x, str) and x for x in ids):
        return {"rule": COVERAGE_NO_USABLE_LISTING, "represented": frozenset(),
                "ambiguous": frozenset(), "all_ambiguous": True}
    listed = frozenset(ids)
    if len(listed) == 1:
        return {"rule": COVERAGE_SINGLE_BATCH, "represented": listed,
                "ambiguous": frozenset(), "all_ambiguous": False}
    return {"rule": COVERAGE_SEVERAL_BATCHES, "represented": frozenset(),
            "ambiguous": listed, "all_ambiguous": False}


def _migration_shape(entry):
    ids = entry.get("covers_batch_ids")
    listing = (tuple(sorted(set(ids))) if isinstance(ids, list)
               and all(isinstance(x, str) for x in ids)
               else ("<unusable listing>", type(ids).__name__))
    return float(entry["usd"]), listing, bool(entry.get("is_floor"))


def _same_shape(a, b):
    return abs(a[0] - b[0]) <= _AMOUNT_EPSILON and a[1:] == b[1:]


def _lines(group):
    return ", ".join(str(e.get("_lineno")) for e in group)


def _analyse_budget(entries, budget, memo=None):
    """ONE READING OF ONE BUDGET, shared by ``total``, ``recorded_batch_ids``,
    ``pending_coverage_reasons`` and the recovery. NEVER RAISES.

    Returns a dict:

      ``counted``          ``[(entry, usd, session_key)]`` -- what the cap sums
      ``unread``           reasons, one per item that is NOT counted and that
                           the budget's record cannot vouch for
      ``coverage``         ``{identity: migration_coverage(...) + "entry"}``
      ``migrated_usd``     ``{identity: the migration's own amount}`` -- what
                           the migration RECORDS; a represented batch recorded
                           at a different amount lowers what is COUNTED for
                           the charge (``counted``), never this
      ``counted_batches``  ``{(source, identity, unit)}`` actually counted
      ``batch_units``      ``{(source, identity, unit)}`` present at all

    THE COUNTED TOTAL IS A FLOOR WHEREVER SOMETHING IS UNVERIFIED, and that is
    chosen rather than incidental: ``spend.unverified_record_refusal`` tells an
    operator the remainder is "potentially OVERSTATED" because unverified items
    "may carry spend it does not count". Every rule below keeps that sentence
    true -- an ambiguous batch is not counted, and of two conflicting amounts
    for one charge the smaller is -- and an unverified budget refuses new paid
    work whichever way the number leans.
    """
    memo = {} if memo is None else memo
    out = {"counted": [], "unread": [], "coverage": {}, "migrated_usd": {},
           "counted_batches": set(), "batch_units": set()}
    seen = set()
    migrations, batches = {}, {}
    counted_at = {}
    for e in entries or ():
        if not isinstance(e, dict) or e.get("budget") != budget:
            continue
        kind = e.get("kind")
        if kind not in ENTRY_KINDS:
            JOURNAL_FAULTS[f"kind:{kind}"] += 1
            out["unread"].append(f"journal line {e.get('_lineno')}: unknown "
                                 f"kind {kind!r}")
            continue
        eid = e.get("entry_id")
        if eid in seen:
            JOURNAL_FAULTS["duplicate_entry_id"] += 1
            continue
        seen.add(eid)
        amount = e.get("usd")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            JOURNAL_FAULTS[f"bad_amount:{type(amount).__name__}"] += 1
            out["unread"].append(f"journal line {e.get('_lineno')}: amount is "
                                 f"a {type(amount).__name__}")
            continue
        if amount != amount or amount < 0:               # NaN and negatives
            JOURNAL_FAULTS[f"bad_amount:{amount!r}"] += 1
            out["unread"].append(f"journal line {e.get('_lineno')}: amount "
                                 f"{amount!r}")
            continue
        if kind == ENTRY_KIND_RUN:
            out["counted"].append((e, float(amount), ("run", e.get("scope"))))
            continue
        ident = scope_identity(e.get("scope"), memo)
        if ident is None:
            JOURNAL_FAULTS[f"identity:unestablished:{kind}"] += 1
            out["unread"].append(
                f"journal line {e.get('_lineno')}: the {kind} entry's state "
                f"file {e.get('scope')!r} cannot be identified (not an "
                f"absolute path, or its directory cannot be examined), so it "
                f"cannot be matched against other spellings of that file; its "
                f"${float(amount):.4f} is NOT counted")
            continue
        if kind == ENTRY_KIND_MIGRATION:
            migrations.setdefault(ident, []).append(e)
        else:
            key = (e.get("source"), ident, str(e.get("unit")))
            batches.setdefault(key, []).append(e)
            out["batch_units"].add(key)

    for ident, group in migrations.items():
        first = group[0]
        shapes = [_migration_shape(e) for e in group]
        if all(_same_shape(shapes[0], sh) for sh in shapes[1:]):
            if len(group) > 1:
                JOURNAL_FAULTS["duplicate_equivalent_scope"] += len(group) - 1
            cov = dict(migration_coverage(first))
            usd = shapes[0][0]
        else:
            usd = min(sh[0] for sh in shapes)
            JOURNAL_FAULTS["equivalent_scope_conflict"] += 1
            out["unread"].append(
                f"journal lines {_lines(group)}: {len(group)} migration "
                f"entries name the same state file "
                f"{_describe_scope(first.get('scope'))} under different "
                f"spellings and DISAGREE (amounts "
                f"{sorted(round(sh[0], 6) for sh in shapes)}, or their batch "
                f"listings); the smaller amount ${usd:.4f} is counted and no "
                f"listed batch is treated as represented")
            cov = {"rule": COVERAGE_NO_USABLE_LISTING,
                   "represented": frozenset(), "ambiguous": frozenset(),
                   "all_ambiguous": True}
        cov["entry"] = first
        out["coverage"][ident] = cov
        out["migrated_usd"][ident] = usd
        counted_at[ident] = len(out["counted"])
        out["counted"].append((first, usd, ident))

    for key, group in batches.items():
        source, ident, unit = key
        first = group[0]
        amounts = [float(e["usd"]) for e in group]
        usd = min(amounts)
        if len(group) > 1:
            if max(amounts) - usd <= _AMOUNT_EPSILON:
                JOURNAL_FAULTS["duplicate_equivalent_scope"] += len(group) - 1
            else:
                JOURNAL_FAULTS["equivalent_scope_conflict"] += 1
                out["unread"].append(
                    f"journal lines {_lines(group)}: batch {unit} is recorded "
                    f"{len(group)} times for the same state file "
                    f"{_describe_scope(first.get('scope'))} under different "
                    f"spellings, with DIFFERENT amounts "
                    f"{sorted(round(a, 6) for a in amounts)}; the smaller "
                    f"${usd:.4f} is counted")
        cov = out["coverage"].get(ident)
        if cov is not None and unit in cov["represented"]:
            # ONE CHARGE, TWO RECORDS. Its money is the migration's amount, so
            # the batch entry is not counted a second time -- and when the two
            # records DISAGREE about that amount, the rule that governs every
            # other pair of records for one charge applies: the smaller is
            # counted and the budget is unverified. This branch used to skip
            # the entry whatever it held, so such a conflict was invisible to
            # every reading of the cap.
            #
            # RECOVERY_UNIT_TOLERANCE_USD, NOT _AMOUNT_EPSILON: a migration's
            # amount is a state file's spend_usd, ROUNDED to six decimals, and
            # a batch entry is the unrounded priced amount. Measured on the
            # production tree: the probe session's state file records
            # $0.056607 beside a journal batch entry of $0.05660715 -- an
            # honest pair 1.5e-7 apart, which a 1e-9 comparison would report
            # as a conflict and refuse the next paid run on.
            m_usd = out["migrated_usd"][ident]
            if abs(usd - m_usd) > RECOVERY_UNIT_TOLERANCE_USD:
                JOURNAL_FAULTS["coverage:represented_conflict"] += 1
                i = counted_at[ident]
                m_entry, m_counted, m_session = out["counted"][i]
                smaller = min(m_counted, usd)
                out["counted"][i] = (m_entry, smaller, m_session)
                out["unread"].append(
                    f"journal line {first.get('_lineno')}: batch {unit} is "
                    f"recorded at ${usd:.6f} for state file "
                    f"{_describe_scope(first.get('scope'))}, whose migration "
                    f"entry (line {cov['entry'].get('_lineno')}) represents "
                    f"that batch at ${m_usd:.6f} -- ONE charge, two records, "
                    f"DIFFERENT amounts; the smaller ${smaller:.6f} is counted")
            continue
        if cov is not None and (cov["all_ambiguous"]
                                or unit in cov["ambiguous"]):
            m = cov["entry"]
            JOURNAL_FAULTS["coverage:unestablished"] += 1
            out["unread"].append(
                f"journal line {first.get('_lineno')}: batch {unit} "
                f"(${usd:.4f}) was collected for state file "
                f"{_describe_scope(first.get('scope'))}, whose migration "
                f"entry (line {m.get('_lineno')}) records "
                f"${out['migrated_usd'][ident]:.4f} across its listed batches "
                f"and cannot say whether that includes this one "
                f"({cov['rule']}); the batch is NOT counted")
            continue
        out["counted"].append((first, usd, ident))
        out["counted_batches"].add(key)
    return out


def total(budget, path=None, entries=None, unreadable=None):
    """Authoritative total, including durable Ragas attempts after cutover.

Explicit pre-read entries retain the legacy analysis API; durable readers use
the path API so database identity and historical coverage can be verified.
    """
    if budget == spend.SPEND_BUDGET_CAMPAIGN and entries is None:
        from oncotriage.evaluation import ragas_billing
        resolved = resolved_journal_path(path)
        if resolved is not None and ragas_billing.exists(resolved):
            try:
                return ragas_billing.status(resolved)["seed"]
            except (ragas_billing.RecoveryRefusal, OSError, ValueError) as exc:
                return spend.LedgerSeed(
                    source=SEED_SOURCE_FOR_BUDGET[budget], unreadable=1,
                    unreadable_reasons=(str(exc),))
    return legacy_total(budget, path, entries, unreadable)


def legacy_total(budget, path=None, entries=None, unreadable=None):
    """``spend.LedgerSeed`` for ``budget``: everything ever recorded for it.

    Duplicate ``entry_id``s are summed ONCE, first occurrence winning, and the
    duplicate is counted -- a file that has been hand-edited or concatenated is
    the case that produces them, and both halves of that (do not double-charge,
    do say it happened) matter. The SAME CHARGE under two spellings of its state
    file is summed once too, and two spellings that disagree about its amount
    make the budget unverified -- see ``_analyse_budget``.

    A MIGRATION EXCLUDES A LATER BATCH ENTRY ONLY WHEN ITS AMOUNT PROVABLY
    INCLUDES THAT BATCH (``migration_coverage``). Where it cannot say, the batch
    is not counted and the budget is UNVERIFIED -- never a silent skip.

    ``unpriced`` counts entries whose amount is a FLOOR -- a pre-journal state
    file that recorded batches but no ``spend_usd``, which is every rater run
    from before ``STATE_SPEND_KEY`` existed. ``LedgerSeed.is_floor`` then makes
    ``spend.describe_seed`` print "A FLOOR, NOT A TOTAL", which is the honest
    thing to say about a cap enforced against it.
    """
    # WHAT COULD NOT BE READ TRAVELS WITH THE TOTAL. A caller handing in
    # `entries` it read itself hands in `unreadable` with them, or asserts by
    # omission that there was nothing -- which is why the default reading goes
    # through the reporting reader rather than the plain one.
    if entries is None:
        entries, unreadable = read_entries_report(path)
    unread = list(unreadable or [])
    analysis = _analyse_budget(entries, budget)
    unread.extend(analysis["unread"])
    usd = 0.0
    rows = unpriced = 0
    sessions = set()
    for e, amount, session in analysis["counted"]:
        usd += amount
        rows += 1
        sessions.add(session)
        if e.get("is_floor"):
            unpriced += 1
    if not rows and not unread:
        return spend.LedgerSeed()
    # A READING WITH NO READABLE ROW AND AN UNREADABLE ONE IS STILL A READING
    # OF THIS BUDGET'S JOURNAL, and it is attributed to it: returning the
    # anonymous fresh seed would let `rater_spend_before` fall back to a state
    # file as though the journal had simply been empty.
    return spend.LedgerSeed(
        usd=usd, rows=rows, unpriced=unpriced, runs=len(sessions),
        source=SEED_SOURCE_FOR_BUDGET[budget], unreadable=len(unread),
        unreadable_reasons=tuple(unread[:spend.UNREADABLE_REASONS_KEPT]))


def recorded_batch_ids(budget, source, scope, entries):
    """The batch ids whose money ``total`` COUNTS for one state file, as a set.

    **WHAT A RESUMED COLLECTION ASKS BEFORE IT CHARGES ANYTHING.** A seed read
    from this journal already contains every batch ``total`` counted, so
    re-charging one of them to the process ledger counts it twice against the
    cap. Two ways a batch's money is in the seed, and both are in the set:

      * a batch entry for this state file -- under ANY spelling of it
        (``scope_identity``) -- that ``total`` counts;
      * a batch a migration entry REPRESENTS (``migration_coverage``), whose
        money is the migration's amount whether or not a batch entry exists.

    A batch whose coverage is NOT established is not in the set: ``total`` did
    not count it, so the collection charges it, and the budget is already
    unverified (or is made so by ``pending_coverage_reasons``).

    NEVER RAISES. An unidentifiable ``scope`` matches nothing.
    """
    memo = {}
    ident = scope_identity(scope, memo)
    if ident is None:
        return set()
    analysis = _analyse_budget(entries, budget, memo)
    found = {unit for (src, i, unit) in analysis["counted_batches"]
             if src == source and i == ident}
    cov = analysis["coverage"].get(ident)
    if cov is not None:
        found |= set(cov["represented"])
    return found


def pending_coverage_reasons(budget, source, scope, entries, batch_ids):
    """Reasons a collection this session is ABOUT to make cannot be counted
    exactly once. NEVER RAISES.

    ``total`` marks an ambiguous batch only once its entry exists. A session
    resuming such a batch would charge it and record it in the same breath, and
    the budget would stay verified for the rest of that session -- long enough
    to buy a retry pass on a figure whose coverage nobody established. So a
    caller about to collect names its batches here first, and each one a
    migration lists without representing -- and that has no entry yet -- is a
    reason to mark the budget unverified BEFORE any new paid work.
    """
    batch_ids = [str(b) for b in dict.fromkeys(batch_ids or ())]
    if not batch_ids:
        return []
    memo = {}
    ident = scope_identity(scope, memo)
    if ident is None:
        return [f"this session's state file {scope!r} cannot be identified, so "
                f"whether the journal already holds the batches it is about to "
                f"collect cannot be established"]
    analysis = _analyse_budget(entries, budget, memo)
    cov = analysis["coverage"].get(ident)
    if cov is None:
        return []
    m = cov["entry"]
    reasons = []
    for bid in batch_ids:
        if (source, ident, bid) in analysis["batch_units"]:
            continue
        if cov["all_ambiguous"] or bid in cov["ambiguous"]:
            JOURNAL_FAULTS["coverage:pending"] += 1
            reasons.append(
                f"batch {bid}, about to be collected for state file "
                f"{_describe_scope(scope)}, is listed by that file's migration "
                f"entry (line {m.get('_lineno')}), which records "
                f"${analysis['migrated_usd'][ident]:.4f} and cannot say "
                f"whether that includes it ({cov['rule']})")
    return reasons


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
    """Record one collected batch. Idempotent on ``(scope, batch_id)``.

    **A BOOL, AND THEREFORE NOT WHAT A MONEY PATH MAY CONSUME.** ``True`` is
    ``wrote``; ``False`` is duplicate, conflict, failed and uncertain at once,
    and two of those mean the money IS recorded while two mean it is NOT. It is
    kept for back-compatibility and has no production caller:
    ``oncotriage/evaluation/rater.py`` records batches through
    ``record_batch_with_outcome``, which keeps the distinction and retries the
    two unconfirmed outcomes.
    """
    return append(_batch_entry(budget, source, scope, batch_id, usd,
                               judge_model), path=path)


def _batch_entry(budget, source, scope, batch_id, usd, judge_model):
    """The one entry shape for a collected batch, shared by both writers.

    ONE BUILDER, because the duplicate check that makes a retry safe compares
    the charge identity AND the amount -- two builders that drifted by one field
    would turn every retry of a landed write into a conflict.
    """
    return {
        "entry_id": entry_id(budget, source, scope, batch_id),
        "kind": ENTRY_KIND_BATCH, "budget": budget, "source": source,
        "scope": scope, "unit": str(batch_id), "usd": float(usd),
        "judge_model": judge_model,
    }


BATCH_RECORD_CONFIRMED = "confirmed"
BATCH_RECORD_DUPLICATE = "duplicate"
BATCH_RECORD_CONFLICTED = "conflicted"
BATCH_RECORD_UNCONFIRMED = "unconfirmed"

BATCH_RECORD_OUTCOMES = (BATCH_RECORD_CONFIRMED, BATCH_RECORD_DUPLICATE,
                         BATCH_RECORD_CONFLICTED, BATCH_RECORD_UNCONFIRMED)
"""What recording one collected batch finally did, after its bounded retries.
CLOSED.

``confirmed``    this call's append WROTE the line and it read back as this
                 charge.
``duplicate``    an entry with this id, this charge identity AND this amount was
                 already in the file -- an earlier attempt or an earlier session
                 (``--resume`` re-collects a collected batch on purpose). The
                 money IS recorded.
``conflicted``   an entry with this id records a DIFFERENT charge. This batch's
                 money is NOT recorded by it and retrying cannot change that, so
                 it is never retried.
``unconfirmed``  every attempt ended ``failed`` or ``uncertain``. The money MAY
                 be on disk (an uncertain write) or may not; a caller must treat
                 it as NOT recorded and may offer it again under the same id,
                 which the duplicate check resolves safely.

The first two are ``BATCH_RECORD_SETTLED``; the last two are unresolved
accounting, and the rater refuses new paid submissions while any exists.
"""

BATCH_RECORD_SETTLED = (BATCH_RECORD_CONFIRMED, BATCH_RECORD_DUPLICATE)
"""The outcomes under which the batch's money IS in the journal."""

BATCH_RECORD_MAX_ATTEMPTS = 3
"""Append attempts per offer of one batch. Bounded, and deliberately small.

A transient failure -- a lock held across a slow filesystem, an ``EINTR`` -- is
what a retry can fix, and it fixes it on the second attempt or not at all. A
persistent one (a read-only directory, a full disk) cannot be fixed by retrying
in a loop, and a loop that retried until it worked would hold a collected
batch's results hostage to the journal. UNCALIBRATED: nobody has measured a
transient journal failure in this project, which is also why the attempts are
reported per batch rather than summarised away."""

BATCH_RECORD_RETRY_SECONDS = 0.05
"""Base pause between attempts; doubled each time (0.05 s, then 0.1 s).
Sub-second on purpose: it runs on the collection path, after money is spent."""

_APPEND_TO_BATCH_OUTCOME = {
    APPEND_WROTE: BATCH_RECORD_CONFIRMED,
    APPEND_DUPLICATE: BATCH_RECORD_DUPLICATE,
    APPEND_CONFLICT: BATCH_RECORD_CONFLICTED,
    APPEND_FAILED: BATCH_RECORD_UNCONFIRMED,
    APPEND_UNCERTAIN: BATCH_RECORD_UNCONFIRMED,
}

# TOTAL OVER THE WRITER'S VOCABULARY, guarded at import rather than by an
# `assert` (`python -O` deletes those). An append outcome with no mapping would
# fall to `.get`'s default below, and the default is UNCONFIRMED -- safe, but a
# new outcome that meant "recorded" would then refuse every retry pass.
if set(_APPEND_TO_BATCH_OUTCOME) != set(APPEND_OUTCOMES) \
        or set(_APPEND_TO_BATCH_OUTCOME.values()) != set(BATCH_RECORD_OUTCOMES):
    raise RuntimeError(
        f"spend_journal._APPEND_TO_BATCH_OUTCOME must map every "
        f"APPEND_OUTCOMES member onto BATCH_RECORD_OUTCOMES: "
        f"{_APPEND_TO_BATCH_OUTCOME!r}")


def record_batch_with_outcome(budget, source, scope, batch_id, usd,
                              judge_model, path=None, max_attempts=None,
                              sleep=None):
    """Record one collected batch and say WHAT HAPPENED. NEVER RAISES.

    Returns ``{"outcome", "attempts", "append_outcomes"}`` where ``outcome`` is
    a ``BATCH_RECORD_OUTCOMES`` member and ``append_outcomes`` is every
    ``append_with_outcome`` answer in order -- the evidence, not a summary of it.

    IDEMPOTENT ON ``(scope, batch_id)`` exactly as ``record_batch`` is: the
    entry is built ONCE and every attempt offers the SAME id and the SAME
    amount, so an attempt that follows an ``uncertain`` write that did land is
    answered ``duplicate`` rather than recording the money twice.

    RETRIES ONLY THE TWO UNCONFIRMED WRITER OUTCOMES, at most ``max_attempts``
    times in all. ``conflict`` is returned at once: a collision is a fact about
    the file, not a transient.
    """
    allowed = BATCH_RECORD_MAX_ATTEMPTS if max_attempts is None else max_attempts
    if isinstance(allowed, bool) or not isinstance(allowed, int) or allowed < 1:
        JOURNAL_FAULTS[f"batch:bad_max_attempts:{allowed!r}"] += 1
        allowed = 1
    pause = time.sleep if sleep is None else sleep
    seen = []
    try:
        entry = _batch_entry(budget, source, scope, batch_id, usd, judge_model)
    except Exception as exc:                                    # noqa: BLE001
        # `float(usd)` on a non-number. Nothing was offered, so nothing can be
        # on disk -- unconfirmed, counted, and the amount is the caller's to
        # report because this function never learned it.
        JOURNAL_FAULTS[f"batch:unbuildable:{type(exc).__name__}"] += 1
        return {"outcome": BATCH_RECORD_UNCONFIRMED, "attempts": 0,
                "append_outcomes": seen}
    outcome = BATCH_RECORD_UNCONFIRMED
    for n in range(allowed):
        try:
            got = append_with_outcome(entry, path=path)
        except Exception as exc:                                # noqa: BLE001
            # DOCUMENTED NEVER RAISES, GUARDED ANYWAY: a raise escaping here
            # would take a collected batch's results with it. It is read as
            # UNCERTAIN, not failed, because nothing says where it raised.
            JOURNAL_FAULTS[f"batch:append_raised:{type(exc).__name__}"] += 1
            got = APPEND_UNCERTAIN
        seen.append(got)
        outcome = _APPEND_TO_BATCH_OUTCOME.get(got, BATCH_RECORD_UNCONFIRMED)
        if outcome != BATCH_RECORD_UNCONFIRMED:
            break
        if n + 1 < allowed:
            try:
                pause(BATCH_RECORD_RETRY_SECONDS * (2 ** n))
            except Exception:                                   # noqa: BLE001
                JOURNAL_FAULTS["batch:retry_pause_raised"] += 1
    if outcome == BATCH_RECORD_UNCONFIRMED:
        JOURNAL_FAULTS["batch:unconfirmed"] += 1
    elif outcome == BATCH_RECORD_CONFLICTED:
        JOURNAL_FAULTS["batch:conflicted"] += 1
    return {"outcome": outcome, "attempts": len(seen), "append_outcomes": seen}


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
"""How long a run may go without offering its spend, whatever it has spent.

The backstop, not the bound. A run that is judging slowly and cheaply would
otherwise sit under ``RUN_CHECKPOINT_USD`` for its whole length and record
nothing until the end, which is the state this whole mechanism removes. It
cannot make the money bound worse -- an extra checkpoint only ever records
MORE of what has been spent.

**IT IS A WALL-CLOCK BOUND ONLY WHERE A CALLER STARTS THE BACKSTOP.**
``checkpoint`` evaluates this threshold when it is CALLED, and its caller calls
it on pair completion -- so on its own a run whose pairs all hang never reaches
it. ``RunSpendCheckpointer.start_backstop`` runs a thread that asks the
checkpointer every ``min_seconds / BACKSTOP_TICKS_PER_THRESHOLD`` whether this
threshold has passed, with or without a completion. **WHAT IT BOUNDS IS
UNRECORDED TIME, NOT STORAGE FAILURE**: a delta is OFFERED on schedule, and a
journal that refuses the write keeps it pending exactly as before.
"""

BACKSTOP_TICKS_PER_THRESHOLD = 4
"""How many times per ``min_seconds`` the backstop thread asks. At the default
60 s that is every 15 s, so spend that has been unoffered for ``min_seconds``
is offered within one further tick. Four rather than one: a single tick per
threshold makes the worst case twice the threshold."""

BACKSTOP_MIN_INTERVAL_SECONDS = 0.01
"""A floor under the tick, so a tiny or zero ``min_seconds`` cannot turn the
backstop into a busy loop holding the checkpointer's lock."""

BACKSTOP_JOIN_SECONDS = 10.0
"""How long ``finalize`` waits for the backstop thread to exit. Bounded because
the thread may be inside an append blocked on another process's ``flock``; the
thread is a daemon, so a timeout is reported and never hangs the interpreter."""


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
    confirmed or until finalization reports it.

    ═══ THE BACKSTOP: A TIMER BESIDE ``checkpoint``, NOT INSIDE IT ═══

    ``start_backstop(measure)`` starts a daemon thread that, every
    ``min_seconds / BACKSTOP_TICKS_PER_THRESHOLD``, reads ``measure()`` and runs
    the SAME threshold decision ``checkpoint`` runs, under the SAME lock. So a
    run whose scoring loop is stalled -- every pair hanging, or the event loop
    itself blocked -- still OFFERS its spend once ``min_seconds`` have passed
    since the last journal attempt, and still retries its pending deltas.
    ``measure`` is the ledger's running total, which is charged per RESPONSE, so
    the backstop also offers the in-flight charges that no completion has
    reached.

    **WHAT IT BOUNDS IS UNRECORDED TIME, NOT STORAGE FAILURE.** A backstop
    offer that the journal refuses is exactly as pending as a completion's, and
    a hard kill still loses whatever was charged since the last CONFIRMED
    write. It changes nothing about double counting: the tick and a
    completion's ``checkpoint`` serialise on one lock, both measure against
    ``_issued_usd``, and a delta is frozen once cut. ``finalize`` signals the
    thread before it takes the lock and joins it after, so no tick can cut a
    delta after the terminal one. What a kill loses is the spend
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
                 path=None, min_usd=None, min_seconds=None, clock=None,
                 accounting_basis=None):
        self.budget = budget
        self.source = source
        self.scope = scope
        self.prefix = str(prefix)
        self.accounting_basis = accounting_basis
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
        # ── THE BACKSTOP ──
        # Plain attributes, not properties: `start_backstop` sets them under the
        # lock and `finalize` reads the event without it (setting an Event is
        # thread-safe and must not wait behind a tick that holds the lock).
        self._backstop = None
        self._backstop_stop = None
        self.backstop_interval = None
        self.backstop_ticks = 0       # ticks that ran the threshold decision
        self.backstop_stopped = None  # True once finalize joined the thread

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
            **({"accounting_basis": self.accounting_basis}
               if self.accounting_basis is not None else {}),
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
            return self._checkpoint_locked(measured)

    def _checkpoint_locked(self, measured):
        """The threshold decision and the writes. CALLER HOLDS ``_lock``.

        ONE BODY FOR BOTH CALLERS -- a completion's ``checkpoint`` and the
        backstop's tick -- so the two cannot disagree about when a delta is due
        or how it is cut. A second copy of this decision in the thread is the
        shape that would double count.
        """
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

    def start_backstop(self, measure, interval=None):
        """Offer spend on a wall clock, whether or not anything completes.

        ``measure`` is a zero-argument callable returning the ledger's running
        total -- ``lambda: spend.SPEND_LEDGER.measured`` in the ragas harness.
        Returns True when a thread was started. NEVER RAISES.

        Refused and counted after ``finalize`` (a tick could otherwise cut a
        delta after the terminal one) and when already started (two threads
        would be two ticks per interval over one lock, harmless but a sign the
        caller wired it twice).
        """
        with self._lock:
            if self.finalized:
                JOURNAL_FAULTS["backstop:start_after_finalize"] += 1
                return False
            if self._backstop is not None:
                JOURNAL_FAULTS["backstop:already_started"] += 1
                return False
            if interval is None:
                interval = self.min_seconds / BACKSTOP_TICKS_PER_THRESHOLD
            if isinstance(interval, bool) \
                    or not isinstance(interval, (int, float)) \
                    or interval != interval:
                JOURNAL_FAULTS[f"backstop:bad_interval:"
                               f"{type(interval).__name__}"] += 1
                interval = BACKSTOP_MIN_INTERVAL_SECONDS
            interval = max(float(interval), BACKSTOP_MIN_INTERVAL_SECONDS)
            stop = threading.Event()
            thread = threading.Thread(
                target=self._backstop_loop, args=(measure, interval, stop),
                name=f"spend-journal-backstop:{self.prefix}", daemon=True)
            try:
                # STARTED INSIDE THE LOCK. Outside it, a `finalize` landing
                # between the assignment and `start()` would join a thread that
                # was never started, which raises.
                thread.start()
            except Exception as exc:                            # noqa: BLE001
                JOURNAL_FAULTS[f"backstop:start_failed:"
                               f"{type(exc).__name__}"] += 1
                return False
            self._backstop_stop = stop
            self._backstop = thread
            self.backstop_interval = interval
            return True

    def _backstop_loop(self, measure, interval, stop):
        """The thread body. Every fault is counted; nothing escapes."""
        while not stop.wait(interval):
            try:
                value = measure()
            except Exception as exc:                            # noqa: BLE001
                JOURNAL_FAULTS[f"backstop:measure:{type(exc).__name__}"] += 1
                continue
            try:
                with self._lock:
                    # A TICK THAT LOST THE RACE TO `finalize` IS NOT A FAULT.
                    # It is shutdown working, so it returns silently rather
                    # than counting `checkpoint:after_finalize` the way a late
                    # completion does.
                    if self.finalized or stop.is_set():
                        return
                    self.backstop_ticks += 1
                    self._checkpoint_locked(value)
            except Exception as exc:                            # noqa: BLE001
                JOURNAL_FAULTS[f"backstop:tick:{type(exc).__name__}"] += 1

    def _stop_backstop(self):
        """Signal the thread and join it, OUTSIDE the lock. NEVER RAISES."""
        stop, thread = self._backstop_stop, self._backstop
        if stop is None or thread is None:
            return None
        stop.set()
        if thread is threading.current_thread():
            return True
        try:
            thread.join(BACKSTOP_JOIN_SECONDS)
        except Exception as exc:                                # noqa: BLE001
            JOURNAL_FAULTS[f"backstop:join:{type(exc).__name__}"] += 1
            return False
        if thread.is_alive():
            JOURNAL_FAULTS["backstop:join_timeout"] += 1
            console.out(
                f"  [Spend journal] the backstop thread for units "
                f"{self.prefix}#* did not exit within {BACKSTOP_JOIN_SECONDS}s "
                f"(it is most likely inside an append waiting on the journal "
                f"lock). It is a daemon and cannot hold the process open, and "
                f"it cannot cut a delta after finalization.")
            return False
        return True

    def finalize(self, measured):
        """Flush, verify against the file, and stop the backstop. NEVER RAISES.

        THE ORDER IS THE SHUTDOWN GUARANTEE. The backstop's stop event is set
        BEFORE the lock is taken, so a tick waiting on the lock sees it and
        returns; the body below runs under the lock; the thread is JOINED after
        the lock is released, because joining while holding it would deadlock
        against a tick blocked on it. See ``_finalize_body`` for the rest.
        """
        stop = self._backstop_stop
        if stop is not None:
            stop.set()
        try:
            return self._finalize_body(measured)
        finally:
            self.backstop_stopped = self._stop_backstop()

    def _finalize_body(self, measured):
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


def find_state_files(root=None, errors=None):
    """Every rater state file under ``root``, sorted. Reads nothing else.

    NEVER RAISES, for ``resolved_journal_path``'s reason: the default root is
    the same lazy glob, and a machine with no sibling tree has no state files
    rather than a broken migration.

    ``errors``, when a list, receives one reason per directory that could NOT
    be walked and for an unresolvable root. ``os.walk`` skips an unreadable
    directory silently by default, and a skipped directory is state files --
    and therefore spend -- this migration never saw.
    """
    if root is None:
        try:
            root = paths.testing_evaluation_path
        except Exception as exc:                                # noqa: BLE001
            JOURNAL_FAULTS[f"unresolvable_root:{type(exc).__name__}"] += 1
            if errors is not None:
                errors.append(f"the state-file root could not be resolved "
                              f"({type(exc).__name__})")
            return []

    def _walk_error(exc):
        JOURNAL_FAULTS[f"migrate:walk:{type(exc).__name__}"] += 1
        if errors is not None:
            errors.append(f"directory {getattr(exc, 'filename', '?')} could "
                          f"not be walked ({type(exc).__name__})")

    found = []
    for dirpath, _dirnames, filenames in os.walk(root, onerror=_walk_error):
        for name in filenames:
            if name in STATE_BASENAMES:
                found.append(os.path.join(dirpath, name))
    return sorted(found)


JOURNAL_ERA_STATE_KEY = "spend_by_batch"
"""A state-file key that only a journal-era rater writes: ``{batch id: usd}``.

**ITS PRESENCE IS WHAT KEEPS A STATE FILE OUT OF THE MIGRATION.** The migration
exists for PRE-journal artifacts, whose spend is in no batch entry. A
journal-era session offers every collected batch to this journal itself, and
migrating its state file too was a defect rather than a redundancy: the
migration entry lists EVERY batch in ``state["batches"]`` as covered, including
a batch that was submitted and not yet collected when the migration ran, and
``total`` then SKIPS that batch's own entry when a later ``--resume`` collects
it -- so its money never reaches the cap. Owned here because this module reads
it; ``oncotriage/evaluation/rater.py`` writes it under this name.

**AND EXCLUSION FROM THE MIGRATION USED TO MEAN EXCLUSION FROM EVERYTHING.**
The rater writes a batch's amount here and THEN offers it to the journal; if
that offer never landed and nobody resumed the session, the charge existed
only in this map. ``recover_state_file_charges`` reads it back into the journal
under the same charge identity, idempotently."""


RECOVERY_UNIT_TOLERANCE_USD = 1e-6
"""Per recorded amount, how far ``spend_usd`` may exceed what the journal holds
for its state file before the difference is reported as UNATTRIBUTED. The rater
rounds ``spend_usd`` to six decimals and keeps ``spend_by_batch`` unrounded, so
an honest file differs by up to half a micro-dollar per batch; this bound is two
orders of magnitude below the cheapest batch this project has priced.

**AND IT IS THE BOUND ON "THE SAME AMOUNT" WHEREVER A ROUNDED RECORD MEETS AN
UNROUNDED ONE FOR ONE CHARGE**: a migration's amount (a rounded ``spend_usd``)
against the ``spend_by_batch`` value or the journal batch entry for the batch
that migration represents. ``_AMOUNT_EPSILON`` stays the bound between two
UNROUNDED records."""


def recover_state_file_charges(state_path, state,
                               budget=spend.SPEND_BUDGET_RATER,
                               source=spend.SPEND_SOURCE_RATER,
                               path=None, entries=None, out=None):
    """Put a JOURNAL-ERA state file's recorded charges into the journal.
    Idempotent. NEVER RAISES.

    Returns ``{"recovered", "recovered_usd", "already_recorded",
    "unverified"}``. ``unverified`` is reasons, each a charge this budget's
    record may be missing; a caller about to spend hands them to
    ``spend.SPEND_LEDGER.mark_unverified``.

    **EVIDENCE-BACKED, NOT INFERRED.** ``spend_by_batch`` is the amount the
    rater charged each batch, written by the rater before it offered the same
    amount to this journal. Each is re-offered through the SAME builder under
    the SAME charge identity, so:

      * a batch the journal already holds -- under this spelling of the state
        file or any other -- is answered ``duplicate`` and nothing is written;
      * a batch it does not hold is written, once, and counted by ``total``;
      * a batch it holds at a DIFFERENT amount is a ``conflict``: nothing is
        written, and that is reported, never merged;
      * a write that cannot be confirmed is reported.

    Running it twice, or under two spellings of one file, writes nothing the
    second time.

    **A BATCH THE MIGRATION REPRESENTS IS NOT OFFERED.** Its money is already
    in the journal, as the migration's amount (``migration_coverage``). It is
    compared instead: equal within ``RECOVERY_UNIT_TOLERANCE_USD`` is
    ``already_recorded``; different is ONE charge with two amounts, reported
    unverified, the smaller counted. Offering it used to write a second record
    of a charge the journal already held and print that it had been RECOVERED.

    **AND ONE CHARGE IS NOT IN ANY BATCH.** ``spend_usd`` above what the journal
    can account for is spend this map does not attribute to a batch: typically
    a pre-journal total the migration never recorded because the file had
    already become journal-era. It cannot be recovered per batch, so it is
    REPORTED.

    **EACH CHARGE IS COUNTED ONCE IN THAT COMPARISON**, however many records
    name it. The migration's amount and the map's amount for the batch the
    migration represents are one charge, so what is explained is the migration
    (the smaller of the two when they differ) plus every OTHER batch in the
    map. Subtracting both used to cancel spend nobody could attribute: a
    migration of $1.00 for batch A, a map repeating A's $1.00 and a
    ``spend_usd`` of $1.70 explained $2.00 and hid the $0.70.
    """
    emit = out or console.out
    rep = {"recovered": 0, "recovered_usd": 0.0, "already_recorded": 0,
           "unverified": []}
    try:
        if not isinstance(state, dict) or JOURNAL_ERA_STATE_KEY not in state:
            return rep
        by_batch = state.get(JOURNAL_ERA_STATE_KEY)
        if not isinstance(by_batch, dict):
            JOURNAL_FAULTS["recovery:map_not_an_object"] += 1
            rep["unverified"].append(
                f"state file {state_path} records {JOURNAL_ERA_STATE_KEY} as a "
                f"{type(by_batch).__name__}, so its per-batch charges cannot "
                f"be read")
            return rep
        ident = scope_identity(state_path)
        if ident is None:
            JOURNAL_FAULTS["recovery:identity_unestablished"] += 1
            rep["unverified"].append(
                f"state file {state_path!r} cannot be identified, so whether "
                f"the journal holds its recorded charges cannot be established")
            return rep
        model = state.get("model")
        # ONE READING, BEFORE ANY OFFER. The migration's coverage decides which
        # recorded charge IS the migration's amount, and nothing this function
        # writes is a migration entry, so its own offers cannot change it.
        if entries is None:
            entries = read_entries(path)
        analysis = _analyse_budget(entries, budget)
        cov = analysis["coverage"].get(ident)
        represented = cov["represented"] if cov is not None else frozenset()
        migrated = analysis["migrated_usd"].get(ident, 0.0)
        valid = {}
        for bid in sorted(by_batch, key=str):
            amount = by_batch[bid]
            if not isinstance(bid, str) or not bid or not _valid_amount(amount):
                JOURNAL_FAULTS["recovery:bad_amount"] += 1
                rep["unverified"].append(
                    f"state file {state_path} records batch {bid!r} at "
                    f"{amount!r}, which is not an amount")
                continue
            valid[bid] = float(amount)
        for bid, amount in valid.items():
            if bid in represented:
                if abs(amount - migrated) <= RECOVERY_UNIT_TOLERANCE_USD:
                    rep["already_recorded"] += 1
                else:
                    JOURNAL_FAULTS["recovery:represented_conflict"] += 1
                    rep["unverified"].append(
                        f"state file {state_path} records batch {bid} at "
                        f"${amount:.6f} and its migration entry (line "
                        f"{cov['entry'].get('_lineno')}) represents that batch "
                        f"at ${migrated:.6f} -- ONE charge, two DIFFERENT "
                        f"amounts; the smaller ${min(amount, migrated):.6f} "
                        f"is counted")
                continue
            got = record_batch_with_outcome(budget, source, state_path, bid,
                                            amount, model, path=path)
            outcome = got.get("outcome")
            if outcome == BATCH_RECORD_CONFIRMED:
                rep["recovered"] += 1
                rep["recovered_usd"] += amount
                JOURNAL_FAULTS["recovery:recovered"] += 1
                emit(f"  [Spend journal] RECOVERED ${amount:.6f} for "
                     f"batch {bid}: state file {state_path} records that "
                     f"charge and the journal did not. It is recorded now, "
                     f"once, under that file's charge identity.")
            elif outcome == BATCH_RECORD_DUPLICATE:
                rep["already_recorded"] += 1
            else:
                JOURNAL_FAULTS[f"recovery:{outcome}"] += 1
                rep["unverified"].append(
                    f"state file {state_path} records batch {bid} at "
                    f"${amount:.6f} and the journal "
                    + ("holds a DIFFERENT amount for that charge"
                       if outcome == BATCH_RECORD_CONFLICTED
                       else "could not confirm recording it")
                    + f" ({outcome})")
        spent = state.get("spend_usd")
        if "spend_usd" in state and not _valid_amount(spent):
            rep["unverified"].append(
                f"state file {state_path} records spend_usd {spent!r}, which "
                f"is not an amount")
        elif _valid_amount(spent):
            # A file whose migration coverage is itself unestablished has its
            # own reason already; its spend_usd cannot be split either way.
            if cov is None or not (cov["all_ambiguous"] or cov["ambiguous"]):
                # EACH CHARGE ONCE: the batch the migration represents is the
                # migration's charge, counted at the smaller of its two
                # amounts, and every other valid batch in the map beside it.
                shared = [valid[b] for b in represented if b in valid]
                explained = ((min(migrated, sum(shared)) if shared
                              else migrated)
                             + sum(a for b, a in valid.items()
                                   if b not in represented))
                unattributed = float(spent) - explained
                if unattributed > RECOVERY_UNIT_TOLERANCE_USD * (
                        len(by_batch) + 1):
                    JOURNAL_FAULTS["recovery:unattributed_spend"] += 1
                    rep["unverified"].append(
                        f"state file {state_path} records spend_usd "
                        f"${float(spent):.6f}, ${unattributed:.6f} more than "
                        f"its migration entry and its per-batch charges "
                        f"account for, each charge counted once; that money "
                        f"is in no journal entry and cannot be attributed to "
                        f"a batch")
    except Exception as exc:                                    # noqa: BLE001
        JOURNAL_FAULTS[f"recovery:raised:{type(exc).__name__}"] += 1
        rep["unverified"].append(
            f"recovery of state file {state_path} failed "
            f"({type(exc).__name__})")
    return rep


def bootstrap_from_state_files(budget=spend.SPEND_BUDGET_RATER,
                               source=spend.SPEND_SOURCE_RATER,
                               root=None, path=None, out=None):
    """Seed the journal from the state files on disk. Idempotent. NEVER RAISES.

    ``bootstrap_report``'s counts as ``(appended, skipped, usd)``, kept for
    back-compatibility. ``skipped`` is every offer that did not write -- a
    duplicate AND a write that failed -- so a caller that has to tell those
    apart, which is every caller that goes on to spend, uses the report.
    """
    rep = bootstrap_report(budget=budget, source=source, root=root, path=path,
                           out=out)
    return rep["written"], rep["skipped"], rep["usd"]


def bootstrap_report(budget=spend.SPEND_BUDGET_RATER,
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

    **AND A JOURNAL-ERA FILE IS NOT MIGRATED, IT IS RECOVERED.** Each one is
    handed to ``recover_state_file_charges``, which offers its recorded
    per-batch charges to the journal under their own charge identity -- so a
    batch whose journal write never landed reaches the cap, once, without a
    resume.

    Returns ``{"written", "duplicate", "skipped", "journal_era", "usd",
    "recovered", "recovered_usd", "journal_era_identities", "unreadable"}``.
    ``journal_era_identities`` is the ``scope_identity`` of every journal-era
    file this call recovered, so a caller about to recover its own state file
    can tell it has already been done. ``unreadable`` names every state file that could not be
    read, every directory that could not be walked, every recorded spend that
    is present and not a usable amount, and every migration offer the journal
    did not CONFIRM (failed, uncertain or conflicted) -- each of which is spend
    this budget's record may be missing. It is what a caller about to spend
    hands to ``spend.SPEND_LEDGER.mark_unverified``.
    """
    emit = out or console.out
    written = duplicate = journal_era = recovered = 0
    usd = recovered_usd = 0.0
    unreadable = []
    era_files = []
    era_identities = set()
    for state_path in find_state_files(root, errors=unreadable):
        try:
            with io.open(state_path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError) as exc:
            JOURNAL_FAULTS[f"migrate:{type(exc).__name__}"] += 1
            emit(f"  [Spend journal] could not read {state_path}: "
                 f"{type(exc).__name__}")
            unreadable.append(f"state file {state_path} could not be read "
                              f"({type(exc).__name__})")
            continue
        if not isinstance(state, dict):
            JOURNAL_FAULTS["migrate:not_an_object"] += 1
            unreadable.append(f"state file {state_path} is a JSON "
                              f"{type(state).__name__}, not an object")
            continue
        if JOURNAL_ERA_STATE_KEY in state:
            journal_era += 1
            era_files.append((state_path, state))
            continue
        amount = state.get("spend_usd")
        batches = state.get("batches")
        batch_ids = [str(b.get("id")) for b in batches
                     if isinstance(b, dict) and b.get("id")] \
            if isinstance(batches, list) else []
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) \
                or amount != amount or amount < 0:
            if "spend_usd" in state:
                # PRESENT AND UNUSABLE is not ABSENT. Absent is a pre-key run
                # and is the floor below; present-and-garbage is a number that
                # was recorded and cannot be read.
                JOURNAL_FAULTS["migrate:bad_spend_usd"] += 1
                unreadable.append(f"state file {state_path} records spend_usd "
                                  f"{amount!r}, which is not an amount")
            amount = 0.0
        # A FLOOR when the file records batches and no money. Those are runs
        # from before `STATE_SPEND_KEY` existed: they DID spend, the artifact
        # cannot say how much, and recording 0 without saying it is a floor
        # would present an unknown as a measurement.
        is_floor = bool(batch_ids) and not amount
        got = append_with_outcome({
            "entry_id": entry_id(budget, source, state_path, "migration"),
            "kind": ENTRY_KIND_MIGRATION, "budget": budget, "source": source,
            "scope": state_path, "unit": "migration",
            "usd": float(amount), "judge_model": state.get("model"),
            "state_file": state_path,
            "state_file_mtime_utc": _file_mtime_utc(state_path),
            "covers_batch_ids": batch_ids,
            "is_floor": is_floor,
        }, path=path)
        if got == APPEND_WROTE:
            written += 1
            usd += float(amount)
        elif got == APPEND_DUPLICATE:
            duplicate += 1
        else:
            # NOT "already present", which is what this branch used to report
            # for every non-write: a FAILED or UNCERTAIN write is money that may
            # not be recorded, and a CONFLICT is an entry under this file's id
            # that records a different charge.
            unreadable.append(f"migration of state file {state_path} was not "
                              f"confirmed in the journal ({got})")
    skipped = duplicate + (len([u for u in unreadable
                                if u.startswith("migration of ")]))
    # ONE READING, AFTER THE MIGRATIONS: the recovery's unattributed-spend check
    # asks what the migration entries hold, and its own appends go through the
    # locked duplicate check rather than through this snapshot.
    if era_files:
        snapshot = read_entries(path)
        for state_path, state in era_files:
            got = recover_state_file_charges(state_path, state, budget=budget,
                                             source=source, path=path,
                                             entries=snapshot, out=emit)
            recovered += got["recovered"]
            recovered_usd += got["recovered_usd"]
            unreadable.extend(got["unverified"])
            ident = scope_identity(state_path)
            if ident is not None:
                era_identities.add(ident)
    emit(f"  [Spend journal] migration: {written} state file(s) recorded, "
         f"{duplicate} already present, {journal_era} journal-era file(s) "
         f"not migrated, ${usd:.4f} added; {recovered} journal-era batch "
         f"charge(s) recovered (${recovered_usd:.4f})"
         + (f"; {len(unreadable)} item(s) COULD NOT BE READ OR CONFIRMED."
            if unreadable else "."))
    return {"written": written, "duplicate": duplicate, "skipped": skipped,
            "journal_era": journal_era, "usd": usd, "recovered": recovered,
            "recovered_usd": recovered_usd,
            "journal_era_identities": era_identities,
            "unreadable": unreadable}


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
    # THE LEDGER'S COUNT WHEN IT HAS ONE, because it holds what the seed read
    # AND what the migration could not; a standalone call has only the seed.
    n_unread = spend.SPEND_LEDGER.unverified(budget)[0] or seed.unreadable
    unverified = (f" -- UNVERIFIED, potentially OVERSTATED: {n_unread} "
                  f"item(s) on this budget's record could not be read"
                  if n_unread else "")
    if cap is None:
        return (f"[Spend journal] {budget}: ${seed.usd:.4f} recorded across "
                f"{seed.runs} session(s); NO CAP is in force{unverified}. "
                f"{where}")
    return (f"[Spend journal] {budget}: ${seed.usd:.4f} recorded across "
            f"{seed.runs} session(s), ${max(cap - seed.usd, 0.0):.4f} of "
            f"${cap:.2f} remaining{unverified}. {where}")
