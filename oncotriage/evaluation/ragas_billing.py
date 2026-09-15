"""Local durable Ragas liabilities. No provider calls or patient database access.

The immutable identity marker precedes database creation. An interrupted first
initialization may therefore require repair, but can never look like a fresh
budget. Keep the database, marker and lock on one persistent local filesystem.
Old binaries must be stopped before cutover; they do not honor this lock.
"""
import fcntl
import hashlib
import json
import os
import sqlite3
import sys
import threading
import uuid
from pathlib import Path

from oncotriage import spend


class RecoveryRefusal(RuntimeError):
    """Accounting cannot safely authorize another paid request."""


class AdmissionDeclined(RecoveryRefusal):
    """Sink protocol refusal; BillingRecord translates it to the shared gate."""

    admission_declined = True

    def __init__(self, reason, committed, held):
        super().__init__(reason)
        self.reason = reason
        self.committed_usd = committed
        self.held_usd = held


def locations(journal):
    base = os.path.realpath(os.path.expanduser(os.fspath(journal)))
    return base, base + ".ragas.sqlite3", base + ".ragas.identity"


def exists(journal):
    _, database, marker = locations(journal)
    return os.path.lexists(database) or os.path.lexists(marker)


def _sync_directory(path):
    fd = os.open(os.fspath(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(path, value):
    # Exclusive publication: partial/empty content fails closed on recovery.
    with open(path, "x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True))
        stream.flush()
        os.fsync(stream.fileno())
        if sys.platform == "darwin":
            fcntl.fcntl(stream.fileno(), 51)  # Darwin F_FULLFSYNC
    _sync_directory(Path(path).parent)


def _connect(database, writable=False, create=False):
    mode = "rwc" if create else "rw" if writable else "ro"
    conn = sqlite3.connect(Path(database).as_uri() + "?mode=" + mode,
                           uri=True, timeout=5)
    try:
        if writable:
            # DELETE avoids WAL sidecar lifecycle requirements. EXTRA also
            # syncs the directory after rollback-journal unlink at commit.
            if conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise RecoveryRefusal("SQLite journal mode could not be verified")
            conn.execute("PRAGMA synchronous=EXTRA")
            if conn.execute("PRAGMA synchronous").fetchone()[0] != 3:
                raise RecoveryRefusal("SQLite synchronization could not be verified")
            if sys.platform == "darwin":
                conn.execute("PRAGMA fullfsync=ON")
                if conn.execute("PRAGMA fullfsync").fetchone()[0] != 1:
                    raise RecoveryRefusal("SQLite fullfsync could not be verified")
        conn.row_factory = sqlite3.Row
        return conn
    except BaseException:
        conn.close()
        raise


def _legacy(journal):
    from oncotriage import spend_journal
    entries, unread = spend_journal.read_entries_report(journal)
    seed = spend_journal.legacy_total(spend.SPEND_BUDGET_CAMPAIGN,
                                      entries=entries, unreadable=unread)
    if seed.has_unreadable() or seed.is_floor:
        raise RecoveryRefusal("legacy spend is incomplete and UNVERIFIED: " +
                              repr(seed.unreadable_reasons))
    selected = [{k: v for k, v in entry.items() if not k.startswith("_")}
                for entry in entries
                if entry.get("budget") == spend.SPEND_BUDGET_CAMPAIGN]
    digest = hashlib.sha256(json.dumps(selected, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    return seed, digest


def _journal_stamp(journal):
    try:
        stat = os.stat(journal)
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
    except FileNotFoundError:
        return None


def _read(conn, marker, journal):
    if os.path.lexists(marker + ".fault"):
        raise RecoveryRefusal("Ragas integrity fault marker: " + marker + ".fault")
    identity = json.loads(Path(marker).read_text(encoding="utf-8"))
    metadata = dict(conn.execute("SELECT key, value FROM metadata"))
    if identity != {"version": 1, "store_id": metadata.get("store_id")}:
        raise RecoveryRefusal("Ragas store identity mismatch")
    baseline, fingerprint = _legacy(journal)
    if (metadata.get("legacy_fingerprint") != fingerprint or
            metadata.get("baseline") != json.dumps(baseline._asdict(), sort_keys=True)):
        raise RecoveryRefusal("legacy campaign journal changed after Ragas cutover")
    rows = [dict(row) for row in conn.execute("SELECT * FROM attempts ORDER BY attempt_id")]
    for row in rows:
        reservation = json.loads(row["reservation"])
        if (reservation.get("attempt_id") != row["attempt_id"] or
                reservation.get("reserved_usd") != row["reserved_usd"] or
                reservation.get("source") != row["source"] or
                reservation.get("model") != row["model"] or
                not row["invocation"]):
            raise RecoveryRefusal("inconsistent attempt identity: " + str(row["attempt_id"]))
        if (not spend._valid_usd(row["reserved_usd"]) or
                row["reserved_usd"] <= 0 or
                row["state"] not in ("reserved", "settled") or
                (row["state"] == "reserved" and
                 (row["settled_usd"] is not None or row["outcome"] is not None)) or
                (row["state"] == "settled" and
                 (not spend._valid_usd(row["settled_usd"]) or
                  row["outcome"] not in spend.BILLING_OUTCOMES))):
            raise RecoveryRefusal("invalid attempt row: " + str(row["attempt_id"]))
    faults = [dict(row) for row in conn.execute("SELECT * FROM faults")]
    if faults:
        raise RecoveryRefusal("Ragas settlement integrity fault: " + repr(faults))
    total = baseline.usd + sum(row["reserved_usd"] if row["state"] == "reserved"
                               else row["settled_usd"] for row in rows)
    if not spend._valid_usd(total):
        raise RecoveryRefusal("invalid cumulative liability")
    unresolved = [row for row in rows if row["state"] == "reserved"]
    seed = baseline._replace(usd=total, rows=baseline.rows + len(rows),
                             runs=baseline.runs + len({r["invocation"] for r in rows}),
                             unresolved=len(unresolved),
                             source=spend.SEED_SOURCE_JOURNAL_CAMPAIGN)
    return {"seed": seed, "unresolved": unresolved, "attempts": rows,
            "store_id": metadata["store_id"]}


def status(journal):
    """Read-only, including when another process owns the store. Raises on fault."""
    journal, database, marker = locations(journal)
    if not exists(journal):
        seed, _ = _legacy(journal)
        return {"seed": seed, "unresolved": [], "attempts": [], "store_id": None}
    try:
        conn = _connect(database)
        try:
            conn.execute("BEGIN")
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RecoveryRefusal("Ragas SQLite integrity check failed")
            return _read(conn, marker, journal)
        finally:
            conn.close()
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error) as exc:
        raise RecoveryRefusal("Ragas record unavailable: " + str(exc)) from exc


class Store:
    """Lifetime single-process ownership, short transactions across worker threads."""

    supports_admission = True

    def __init__(self, journal):
        self.journal, self.database, self.marker = locations(journal)
        self.invocation = uuid.uuid4().hex
        self._owner = None
        self._mutex = threading.RLock()
        self._legacy_stamp = object()

    def __enter__(self):
        try:
            # Parent must already exist: never invent a new budget location.
            self._owner = open(self.database + ".lock", "a+b")
            fcntl.flock(self._owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not exists(self.journal):
                baseline, fingerprint = _legacy(self.journal)
                identity = {"version": 1, "store_id": uuid.uuid4().hex}
                _publish(self.marker, identity)
                conn = _connect(self.database, writable=True, create=True)
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                    conn.execute("CREATE TABLE attempts (attempt_id TEXT PRIMARY KEY, "
                                 "invocation TEXT NOT NULL, source TEXT NOT NULL, model TEXT, "
                                 "reservation TEXT NOT NULL, reserved_usd REAL NOT NULL, "
                                 "state TEXT NOT NULL, settled_usd REAL, outcome TEXT, "
                                 "input_tokens INTEGER, output_tokens INTEGER)")
                    conn.execute("CREATE TABLE faults (attempt_id TEXT PRIMARY KEY, detail TEXT NOT NULL)")
                    conn.executemany("INSERT INTO metadata VALUES (?, ?)", [
                        ("store_id", identity["store_id"]),
                        ("legacy_fingerprint", fingerprint),
                        ("baseline", json.dumps(baseline._asdict(), sort_keys=True))])
                    conn.commit()
                finally:
                    conn.close()
                _sync_directory(Path(self.database).parent)
            self.initial = status(self.journal)
            conn = _connect(self.database)
            try:
                self._metadata = dict(conn.execute("SELECT key, value FROM metadata"))
                self._baseline = json.loads(self._metadata["baseline"])
            finally:
                conn.close()
            if self.initial["unresolved"]:
                details = [(r["attempt_id"], r["reserved_usd"])
                           for r in self.initial["unresolved"]]
                raise RecoveryRefusal("unresolved Ragas attempts; no automatic resend/refund: " + repr(details))
            return self
        except (OSError, sqlite3.Error, ValueError) as exc:
            self.close()
            raise RecoveryRefusal("Ragas store could not be opened: " + str(exc)) from exc
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._owner is not None:
            self._owner.close()
            self._owner = None

    def __exit__(self, *exc):
        self.close()

    def _transaction(self):
        if self._owner is None:
            raise RecoveryRefusal("Ragas store has no owner")
        conn = _connect(self.database, writable=True)
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._verify(conn)
            return conn
        except BaseException:
            conn.close()
            raise

    def _verify(self, conn):
        """Bounded identity checks; reparse history only when its file changes.

This is change detection for cooperating local writers, not protection against
an administrator replacing files while forging filesystem metadata.
        """
        if os.path.lexists(self.marker + ".fault"):
            raise RecoveryRefusal("Ragas settlement integrity fault")
        metadata = dict(conn.execute("SELECT key, value FROM metadata"))
        if metadata != self._metadata:
            raise RecoveryRefusal("Ragas store metadata changed")
        identity = json.loads(Path(self.marker).read_text(encoding="utf-8"))
        if identity != {"version": 1, "store_id": self.initial["store_id"]} or metadata.get("store_id") != identity["store_id"]:
            raise RecoveryRefusal("Ragas store identity changed")
        if conn.execute("SELECT 1 FROM faults LIMIT 1").fetchone():
            raise RecoveryRefusal("Ragas settlement integrity fault")
        stamp = _journal_stamp(self.journal)
        if stamp != self._legacy_stamp:
            seed, fingerprint = _legacy(self.journal)
            if (stamp != _journal_stamp(self.journal) or
                    metadata.get("legacy_fingerprint") != fingerprint or
                    metadata.get("baseline") != json.dumps(seed._asdict(), sort_keys=True)):
                raise RecoveryRefusal("legacy campaign journal changed after Ragas cutover")
            self._legacy_stamp = stamp
        # Baseline is immutable after startup, and must not change silently.
        baseline = json.loads(metadata["baseline"])
        if baseline != self._baseline:
            raise RecoveryRefusal("Ragas historical baseline changed")

    def reserve(self, **fields):
        fields = dict(fields)
        cap = fields.pop("admission_cap", None)
        key = fields["attempt_id"]
        amount = fields["reserved_usd"]
        if not spend._valid_usd(amount) or amount <= 0:
            raise RecoveryRefusal("invalid Ragas reservation")
        payload = json.dumps(fields, sort_keys=True)
        with self._mutex:
            conn = self._transaction()
            try:
                prior = conn.execute("SELECT reservation, state FROM attempts WHERE attempt_id=?", (key,)).fetchone()
                if prior is not None:
                    if prior[0] != payload or prior[1] != "reserved":
                        raise RecoveryRefusal("conflicting reservation replay")
                    return
                amounts = conn.execute("SELECT COALESCE(SUM(CASE WHEN state='reserved' THEN reserved_usd ELSE 0 END),0), "
                                       "COALESCE(SUM(CASE WHEN state='settled' THEN settled_usd ELSE 0 END),0) FROM attempts").fetchone()
                held, committed = amounts[0], amounts[1] + self._baseline["usd"]
                if cap is not None and committed + held + amount > cap + 1e-9:
                    reason = (spend.ADMISSION_DECLINE_EXHAUSTED
                              if committed + amount > cap + 1e-9 else spend.ADMISSION_DECLINE_HELD)
                    raise AdmissionDeclined(reason, committed, held)
                conn.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, 'reserved', NULL, NULL, NULL, NULL)",
                             (key, self.invocation, fields["source"], fields["model"], payload, amount))
                conn.commit()
            finally:
                conn.close()
            if self.stored_state(key) != {"state": "reserved", "reserved_usd": amount,
                                           "settled_usd": None, "outcome": None}:
                raise RecoveryRefusal("reservation readback failed")

    def settle(self, attempt_id, *, outcome, settled_usd, input_tokens=None, output_tokens=None):
        if outcome not in spend.BILLING_OUTCOMES or not spend._valid_usd(settled_usd):
            raise RecoveryRefusal("invalid settlement")
        with self._mutex:
            conn = self._transaction()
            try:
                row = conn.execute("SELECT state, settled_usd, outcome FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
                if row is None:
                    return "missing"
                if row[0] == "settled":
                    return "duplicate" if (row[1] == settled_usd and row[2] == outcome) else "conflict"
                conn.execute("UPDATE attempts SET state='settled', settled_usd=?, outcome=?, input_tokens=?, output_tokens=? WHERE attempt_id=? AND state='reserved'",
                             (settled_usd, outcome, input_tokens, output_tokens, attempt_id))
                conn.commit()
            finally:
                conn.close()
            row = self.stored_state(attempt_id)
            return "settled" if row["settled_usd"] == settled_usd and row["outcome"] == outcome else "failed"

    def stored_state(self, attempt_id):
        with self._mutex:
            conn = _connect(self.database)
            try:
                conn.execute("BEGIN")
                self._verify(conn)
                row = conn.execute("SELECT state, reserved_usd, settled_usd, outcome FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
                return dict(row) if row else {"state": "absent"}
            finally:
                conn.close()

    def settled_usd(self, attempt_id):
        return self.stored_state(attempt_id).get("settled_usd")

    def record_discrepancy(self, **fields):
        # A conflict/missing row invalidates accounting, rather than inventing
        # an automatic refund or hiding damage behind a compensating charge.
        with self._mutex:
            if not os.path.lexists(self.marker + ".fault"):
                _publish(self.marker + ".fault", fields)
            conn = _connect(self.database, writable=True)
            try:
                conn.execute("INSERT OR IGNORE INTO faults VALUES (?, ?)",
                             (fields["attempt_id"], json.dumps(fields, sort_keys=True)))
                conn.commit()
            finally:
                conn.close()
        return spend.DISCREPANCY_DEFERRED
