"""Database-lifetime ablation billing identity, using the campaign engine.

Keep the database and its .billing-identity.json witness together. Unknown
bounded attempts retain their full liability; a later invocation can purchase
the work again under a NEW attempt id if budget remains. This is accounting,
not exactly-once provider execution. Complete deletion/coordinated rollback of
all evidence cannot be detected. Upgrade with old writers stopped.
"""

import fcntl
import json
import os
from pathlib import Path
import threading
import uuid

from oncotriage import spend
from oncotriage.storage import database_logger as storage


class BillingRefusal(RuntimeError):
    """Accounting could not be established; no new paid work is permitted."""


_OWNER = threading.Lock()


def witness_path(database):
    return Path(str(Path(database).resolve()) + ".billing-identity.json")


def _sync_parent(path):
    fd = os.open(str(Path(path).parent), os.O_RDONLY)
    try:
        storage._sync_fd(fd)
    finally:
        os.close(fd)


def _read_identity(database):
    try:
        value = json.loads(witness_path(database).read_text())
        if (not isinstance(value, dict) or value.get("version") != 1
                or not isinstance(value.get("campaign_id"), str)
                or len(value["campaign_id"]) != 32
                or uuid.UUID(hex=value["campaign_id"]).hex != value["campaign_id"]):
            raise ValueError("invalid billing identity")
        return value["campaign_id"]
    except Exception as exc:
        raise BillingRefusal(f"Unreadable ablation billing witness: {exc}") from exc


def _connect(database):
    # EXTRA also makes rollback-journal commits safe if ordinary study schema
    # initialization subsequently selects DELETE mode. The shared engine uses
    # FULL; the owner requires WAL throughout paid work before installing it.
    conn = storage._open_billing_connection(str(database))
    try:
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        conn.execute("PRAGMA synchronous=EXTRA")
        if mode.lower() != "wal" or conn.execute("PRAGMA synchronous").fetchone()[0] != 3:
            raise BillingRefusal("Ablation identity requires verified WAL/EXTRA")
        return conn
    except BaseException:
        conn.close()
        raise


def _validate(conn, campaign):
    rows = conn.execute("SELECT version, campaign_id FROM ablation_billing_identity").fetchall()
    if rows != [(1, campaign)]:
        raise BillingRefusal("Ablation database and billing witness disagree")
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='billing_attempts'").fetchone()
    normalize = lambda sql: "".join(sql.lower().split()).replace("ifnotexists", "")
    if (not ddl or normalize(ddl[0]) != normalize(
            storage._billing_table_sql("ablation_billing_invocations"))):
        raise BillingRefusal("Ablation billing schema/run owner is invalid")
    # A partial/missing table must not be recreated by ordinary schema init.
    conn.execute("SELECT id FROM ablation_billing_invocations LIMIT 0")
    conn.execute("SELECT attempt_id FROM billing_attempts LIMIT 0")
    if conn.execute("SELECT 1 FROM billing_attempts WHERE campaign_id <> ? LIMIT 1",
                    (campaign,)).fetchone():
        raise BillingRefusal("Unexpected second ablation billing identity")
    if conn.execute("SELECT 1 FROM billing_attempts b LEFT JOIN "
                    "ablation_billing_invocations i ON b.run_id=i.id "
                    "WHERE i.id IS NULL LIMIT 1").fetchone():
        raise BillingRefusal("Ablation billing invocation history is incomplete")


def _markers(database, campaign):
    directory = Path(str(database) + ".billing-discrepancies")
    if not os.path.lexists(directory):
        return []
    markers = []
    conn = storage._open_connection(str(database), read_only=True)
    try:
        for path in sorted(directory.iterdir()):
            if path.name.startswith("."):
                continue  # Shared writer's unfinished temp file, never published.
            payload = json.loads(path.read_text())
            if (not isinstance(payload, dict)
                    or payload.get("campaign_id") != campaign
                    or Path(str(payload.get("db_path"))).resolve() != database
                    or payload.get("version") != storage.DISCREPANCY_MARKER_VERSION
                    or path.name != str(payload.get("attempt_id")) + storage._DISCREPANCY_MARKER_SUFFIX
                    or isinstance(payload.get("run_id"), bool)
                    or not isinstance(payload.get("run_id"), int)
                    or not conn.execute("SELECT id FROM ablation_billing_invocations WHERE id=?",
                                        (payload["run_id"],)).fetchone()):
                raise BillingRefusal("Ablation discrepancy marker identity is invalid")
            markers.append(payload)
    finally:
        conn.close()
    return markers


def _require_markers_covered(database, campaign):
    # Read-only callers must not omit pending liabilities. Committed markers
    # which could not be unlinked are safe only after matching their exact row.
    conn = storage._open_connection(str(database), read_only=True)
    try:
        for payload in _markers(database, campaign):
            row = conn.execute("SELECT campaign_id, run_id, kind, state, settled_usd, note "
                               "FROM billing_attempts WHERE attempt_id=?",
                               (payload["attempt_id"] + storage.DISCREPANCY_ID_SUFFIX,)).fetchone()
            expected = (campaign, payload["run_id"], storage.BILLING_ATTEMPT_KIND_DISCREPANCY,
                        storage.BILLING_ATTEMPT_STATE_SETTLED, payload["shortfall_usd"],
                        storage._discrepancy_note(payload["result"], payload["live_usd"],
                            payload["durable_usd"], payload["shortfall_usd"], payload["attempt_id"]))
            if row != expected:
                raise BillingRefusal("Unreconciled ablation billing discrepancy; resume through main")
    finally:
        conn.close()


def read_seed(database):
    """Read verified durable history; never turn unreadable history into zero."""
    try:
        return _read_seed(database)
    except BillingRefusal:
        raise
    except Exception as exc:
        raise BillingRefusal(f"Cannot read ablation accounting: {exc}") from exc


def _read_seed(database):
    """Read verified durable history; never turn unreadable history into zero."""
    database = Path(database).resolve()
    campaign = _read_identity(database)
    conn = storage._open_connection(str(database), read_only=True)
    try:
        _validate(conn, campaign)
    finally:
        conn.close()
    _require_markers_covered(database, campaign)
    total = storage.campaign_billing_total(campaign, db_path=str(database))
    unproven = storage.stage5_unproven_liabilities(campaign, db_path=str(database))
    if unproven:
        raise BillingRefusal("Ablation has Stage 5 liabilities without proven upper bounds")
    return spend.LedgerSeed(usd=total.usd, rows=total.attempts,
                            runs=len(total.run_ids), unresolved=total.unresolved,
                            source=spend.SEED_SOURCE_BILLING_RECORD)


class Session:
    """Canonical owner, identity and one invocation across all variants.

    Process-global exclusion is necessary because spend's installed sink is
    process-global. The file lock also excludes another process or path alias.
    Restore a caller's prior sink only after the study has drained its workers.
    """

    def __init__(self, database, checkpoint):
        self.database = Path(database).resolve()
        self.checkpoint = Path(checkpoint)
        self.lock = None
        self.prior_sink = None
        self.owned = False
        self.campaign = None
        self.run_id = None

    def __enter__(self):
        if not _OWNER.acquire(blocking=False):
            raise BillingRefusal("Another ablation invocation owns this process's billing sink")
        self.owned = True
        self.prior_sink = spend.BILLING_RECORD.installed_sink()
        try:
            self.lock = open(str(self.database) + ".billing.lock", "a+")
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if spend.policy() != spend.SPEND_POLICY_CAMPAIGN:
                raise BillingRefusal("Ablation requires the campaign spend policy")
            marker = witness_path(self.database)
            if not marker.exists():
                if (os.path.lexists(self.database) or self.checkpoint.exists()
                        or Path(str(self.database) + ".billing-discrepancies").exists()):
                    raise BillingRefusal("Unverified legacy/partial ablation history; "
                                         "--fresh-start does not reset its budget")
                self.campaign = uuid.uuid4().hex
                # Publish the witness BEFORE creating the DB. A crash at any
                # later initialization boundary leaves evidence that refuses.
                with marker.open("x") as stream:
                    json.dump({"version": 1, "campaign_id": self.campaign}, stream)
                    stream.flush()
                    storage._sync_fd(stream.fileno())
                _sync_parent(marker)
                conn = _connect(self.database)
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute("CREATE TABLE ablation_billing_identity "
                                 "(version INTEGER NOT NULL, campaign_id TEXT NOT NULL)")
                    conn.execute("INSERT INTO ablation_billing_identity VALUES (1, ?)",
                                 (self.campaign,))
                    conn.execute("CREATE TABLE ablation_billing_invocations "
                                 "(id INTEGER PRIMARY KEY AUTOINCREMENT)")
                    storage.initialize_billing_table(
                        conn.cursor(), run_owner="ablation_billing_invocations")
                    conn.commit()
                finally:
                    conn.close()
                _sync_parent(self.database)
            else:
                if not self.database.is_file():
                    raise BillingRefusal("Ablation billing database is missing; witness survives")
                self.campaign = _read_identity(self.database)
            conn = storage._open_connection(str(self.database), read_only=True)
            try:
                _validate(conn, self.campaign)
            finally:
                conn.close()
            # Validate marker ownership before reconciliation can write anything.
            _markers(self.database, self.campaign)
            self.discrepancies = Path(str(self.database) + ".billing-discrepancies")
            recon = storage.reconcile_discrepancy_markers(
                str(self.discrepancies), db_path=str(self.database))
            if recon.unreconciled:
                raise BillingRefusal("Ablation billing discrepancies could not be reconciled")
            read_seed(self.database)
            conn = _connect(self.database)
            try:
                _validate(conn, self.campaign)
                self.run_id = conn.execute("INSERT INTO ablation_billing_invocations "
                                           "DEFAULT VALUES").lastrowid
                conn.commit()
            finally:
                conn.close()
            spend.BILLING_RECORD.clear()
            spend.BILLING_RECORD.reset_liability()
            return self
        except BaseException as exc:
            self.__exit__(None, None, None)
            if isinstance(exc, Exception) and not isinstance(exc, BillingRefusal):
                raise BillingRefusal(f"Cannot establish ablation accounting: {exc}") from exc
            raise

    def install(self):
        # Ordinary schema initialization may have requested another journal
        # mode. Re-establish/verify WAL before shared FULL billing commits.
        try:
            conn = _connect(self.database)
            conn.close()
            spend.BILLING_RECORD.install(storage.BillingRecordSink(
                str(self.database), self.campaign, self.run_id,
                discrepancy_dir=str(self.discrepancies), scope="ablation"))
        except Exception as exc:
            raise BillingRefusal(f"Cannot install ablation accounting: {exc}") from exc

    def __exit__(self, *_exc):
        if self.owned:
            spend.BILLING_RECORD.install(self.prior_sink)
            try:
                if self.lock is not None:
                    self.lock.close()
            finally:
                self.owned = False
                _OWNER.release()
