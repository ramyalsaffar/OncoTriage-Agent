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


def read_entries(path=None):
    """Every well-formed entry, in file order. NEVER RAISES.

    An absent file is an empty list, which is the correct reading: nothing has
    been recorded yet. A line that will not parse, or that carries a schema
    version this build does not know, is COUNTED into ``JOURNAL_FAULTS`` and
    SKIPPED -- summing a line whose shape is unknown would put an unverified
    number into a cap.

    THE UNDER-READING DIRECTION IS THE UNSAFE ONE HERE AND IT IS NOT HIDDEN:
    a skipped entry is money the next session will not be charged for, so every
    skip is a counted degradation that reaches the run-end report.
    """
    p = resolved_journal_path(path)
    if p is None:
        return []
    try:
        with io.open(p, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return []
    except OSError as exc:
        JOURNAL_FAULTS[f"read:{type(exc).__name__}"] += 1
        console.out(f"  [Spend journal] could not read {p}: "
                    f"{type(exc).__name__}: {exc}")
        return []
    out = []
    for lineno, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
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


def append(entry, path=None):
    """Append one entry unless its ``entry_id`` is already recorded.

    Returns True when a line was written and False when the id was already
    there. NEVER RAISES: it is called after money has already been spent, and
    a record that could not be written must not discard the collection it is
    about. Every failure is counted.

    THE READ AND THE WRITE ARE UNDER ONE EXCLUSIVE ``flock``. The decision to
    write depends on what the file already holds, so releasing the lock between
    the two would let a second process append the same id in the window.
    """
    p = resolved_journal_path(path)
    if p is None:
        return False
    payload = dict(entry)
    payload.setdefault("schema_version", SCHEMA_VERSION)
    payload.setdefault("recorded_at_utc", _utc_now())
    eid = payload.get("entry_id")
    if not eid:
        JOURNAL_FAULTS["append:no_entry_id"] += 1
        return False
    try:
        parent = os.path.dirname(p)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        # "a+" AND NOT "a": the read has to happen through the same handle the
        # lock is held on. Opening a second handle to read would be the
        # check-then-act split the lock exists to close.
        with io.open(p, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.seek(0)
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        existing = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(existing, dict) \
                            and existing.get("entry_id") == eid:
                        return False
                fh.seek(0, os.SEEK_END)
                fh.write(json.dumps(payload, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        JOURNAL_FAULTS[f"append:{type(exc).__name__}"] += 1
        console.out(f"  [Spend journal] could not record ${payload.get('usd')} "
                    f"to {p}: {type(exc).__name__}: {exc}. The next session's "
                    f"cap will not see it.")
        return False
    log.info("spend_journal.append", reason=payload.get("kind"))
    return True


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
