"""Durable patient admissions, independent of inference/result persistence.

One sidecar per database and campaign. START is admission after cancellation
checks, before parsing; it is not proof of model execution. An unmatched START
is unknown and is never replaced by a later attempt. The exclusive file lock
is held for the entire writer lifetime; readers acquire that same lock while
taking their database snapshot. History is never removed by checkpoint cleanup.

Each update replaces a checksummed complete document after syncing its bytes,
then syncs the directory, using the runner's fsync/F_FULLFSYNC convention.
After any uncertain write the writer stops admissions. It may still record
completions for already admitted workers. No existing history is regenerated.
"""

import contextlib
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import uuid

VERSION = 1


class HistoryRefusal(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f"{code}: {message}")


def bundle_key(path):
    """Same stable stem identity as the cohort, without name-bearing text."""
    return hashlib.sha256(("bundle-stem/v1\0" + Path(path).stem).encode()).hexdigest()


def history_path(database, campaign_id):
    base = os.path.realpath(database) + ".attempt-history"
    return Path(base) / (hashlib.sha256(campaign_id.encode()).hexdigest() + ".json")


def _sync(fd):
    os.fsync(fd)
    if sys.platform == "darwin" and hasattr(fcntl, "F_FULLFSYNC"):
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)


def _sync_directory(path):
    fd = os.open(str(path), os.O_RDONLY)
    try:
        _sync(fd)
    finally:
        os.close(fd)


def document_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _write(path, body):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"body": body, "sha256": document_digest(body)}, fh,
                  sort_keys=True, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        _sync(fh.fileno())
    os.replace(tmp, path)
    _sync_directory(path.parent)


def _validate(body):
    try:
        if not (body["version"] == VERSION):
            raise ValueError("invalid history field")
        if not (isinstance(body["campaign_id"], str) and body["campaign_id"]):
            raise ValueError("invalid history field")
        if not (isinstance(body["cohort_digest"], str) and body["cohort_digest"]):
            raise ValueError("invalid history field")
        keys = body["bundles"]
        if not (isinstance(keys, list) and keys == sorted(set(keys)) and keys):
            raise ValueError("invalid history field")
        if not (all(isinstance(k, str) and len(k) == 64 for k in keys)):
            raise ValueError("invalid history field")
        runs = body["runs"]
        if not (isinstance(runs, list) and runs):
            raise ValueError("invalid history field")
        identities = [(r["id"], r["started_at"]) for r in runs]
        if not (len(set(identities)) == len(identities)):
            raise ValueError("invalid history field")
        if not (len({r[0] for r in identities}) == len(identities)):
            raise ValueError("invalid history field")
        if not (all(type(i) is int and isinstance(s, str) and s for i, s in identities)):
            raise ValueError("invalid history field")
        attempts = body["attempts"]
        if not (isinstance(attempts, list)):
            raise ValueError("invalid history field")
        ids, inference_ids = set(), set()
        for seq, a in enumerate(attempts, 1):
            if not (a["sequence"] == seq and type(a["sequence"]) is int):
                raise ValueError("invalid history field")
            if not (isinstance(a["id"], str) and a["id"] and a["id"] not in ids):
                raise ValueError("invalid history field")
            ids.add(a["id"])
            if not ((a["run_id"], a["run_started_at"]) in identities):
                raise ValueError("invalid history field")
            if not (a["bundle"] in keys and a["kind"] in ("main", "resample")):
                raise ValueError("invalid history field")
            c = a["completion"]
            if c is None:
                continue
            if not (c["outcome"] in ("success", "failure", "exception")):
                raise ValueError("invalid history field")
            if not (c["patient_id"] is None or isinstance(c["patient_id"], str)):
                raise ValueError("invalid history field")
            if not (c["write_ok"] is None or type(c["write_ok"]) is bool):
                raise ValueError("invalid history field")
            iid = c["inference_id"]
            if not (iid is None or (type(iid) is int and iid > 0)):
                raise ValueError("invalid history field")
            if not (c["write_ok"] is not True or iid is not None):
                raise ValueError("invalid history field")
            if iid is not None:
                if not (iid not in inference_ids and c["write_ok"] is True):
                    raise ValueError("invalid history field")
                inference_ids.add(iid)
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        raise HistoryRefusal("attempt_history_corrupt", "invalid history structure") from exc


def read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            envelope = json.load(fh)
        body = envelope["body"]
        if envelope["sha256"] != document_digest(body):
            raise ValueError("checksum mismatch")
    except FileNotFoundError as exc:
        raise HistoryRefusal("attempt_history_missing", "history is absent; historical coverage cannot be invented") from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HistoryRefusal("attempt_history_corrupt", "history cannot be verified") from exc
    _validate(body)
    return body


@contextlib.contextmanager
def _locked(path, create=False):
    try:
        if create:
            if not path.parent.exists():
                path.parent.mkdir()
                _sync_directory(path.parent.parent)
            fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        else:
            fd = os.open(str(path) + ".lock", os.O_RDONLY)
    except OSError as exc:
        raise HistoryRefusal("attempt_history_missing", "history lock is unavailable") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise HistoryRefusal("attempt_history_busy", "another campaign writer or exporter holds the history") from exc
        yield
    finally:
        os.close(fd)


def require_coverage(body, campaign_id, runs, cohort_digest=None, bundles=None):
    if body["campaign_id"] != campaign_id:
        raise HistoryRefusal("attempt_history_mismatch", "campaign identity differs")
    if cohort_digest is not None and body["cohort_digest"] != cohort_digest:
        raise HistoryRefusal("attempt_history_mismatch", "cohort digest differs")
    if bundles is not None and body["bundles"] != sorted(bundles):
        raise HistoryRefusal("attempt_history_mismatch", "cohort bundle identities differ")
    known = {(r["id"], r["started_at"]) for r in body["runs"]}
    wanted = {(r["id"], r["started_at"]) for r in runs}
    if not wanted <= known:
        raise HistoryRefusal("attempt_history_uncovered", "campaign contains a run without admission coverage")
    # Initialization can die after RUN is journaled but before the run joins
    # the campaign in SQLite. Such an orphan must have admitted no work.
    if any((a["run_id"], a["run_started_at"]) not in wanted for a in body["attempts"]):
        raise HistoryRefusal("attempt_history_mismatch", "an admitted run is absent from the database campaign")


@contextlib.contextmanager
def snapshot_lock(database, campaign_id):
    path = history_path(database, campaign_id)
    with _locked(path):
        yield read(path)


class Writer:
    def __init__(self, path, body, run):
        self.path, self.body, self.run = path, body, run
        self._mutex = threading.Lock()
        self.failed = False

    def _commit(self, new):
        try:
            _validate(new)
            _write(self.path, new)
        except Exception as exc:
            self.failed = True
            # Replacement may have landed before its acknowledgement failed.
            # Re-read without inventing success. Existing admissions remain
            # available for completion; no new admission is allowed.
            try:
                self.body = read(self.path)
            except HistoryRefusal:
                pass
            raise HistoryRefusal("attempt_history_write_failed", "durable history update failed; further admissions stopped") from exc
        self.body = new

    def start(self, path, kind, run_id):
        with self._mutex:
            if self.failed:
                raise HistoryRefusal("attempt_history_write_failed", "admissions stopped after a history failure")
            key = bundle_key(path)
            if key not in self.body["bundles"] or run_id != self.run["id"] or kind not in ("main", "resample"):
                self.failed = True
                raise HistoryRefusal("attempt_history_mismatch", "worker is outside the recorded run/cohort")
            new = copy.deepcopy(self.body)
            aid = uuid.uuid4().hex
            new["attempts"].append({"id": aid, "sequence": len(new["attempts"]) + 1,
                                    "run_id": run_id, "run_started_at": self.run["started_at"],
                                    "bundle": key, "kind": kind, "completion": None})
            self._commit(new)
            return aid

    def complete(self, aid, *, outcome, patient_id=None, write_ok=None, inference_id=None):
        with self._mutex:
            new = copy.deepcopy(self.body)
            found = [a for a in new["attempts"] if a["id"] == aid]
            if len(found) != 1 or found[0]["completion"] is not None:
                self.failed = True
                raise HistoryRefusal("attempt_history_mismatch", "completion does not name one unfinished admission")
            found[0]["completion"] = {"outcome": outcome, "patient_id": patient_id,
                                      "write_ok": write_ok, "inference_id": inference_id}
            self._commit(new)


@contextlib.contextmanager
def open_writer(database, campaign_id, run_id, cohort_digest, files, *, new_campaign=False):
    """Called BEFORE campaign identity/membership publication and all work.

    Only a newly allocated campaign may create history. Existing campaigns,
    including recovered campaigns with no checkpoint, must already have it.
    """
    path = history_path(database, campaign_id)
    with _locked(path, create=new_campaign):
        uri = Path(os.path.realpath(database)).as_uri() + "?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
            rows = conn.execute("SELECT id, started_at FROM runs WHERE billing_campaign_id = ? OR id = ? ORDER BY id",
                                (campaign_id, run_id)).fetchall()
        runs = [{"id": i, "started_at": s} for i, s in rows]
        current = [r for r in runs if r["id"] == run_id]
        if len(current) != 1:
            raise HistoryRefusal("attempt_history_mismatch", "current run does not exist")
        keys = sorted(bundle_key(p) for p in files)
        if len(set(keys)) != len(keys) or not keys:
            raise HistoryRefusal("attempt_history_mismatch", "cohort is empty or has duplicate bundle identities")
        if new_campaign:
            if path.exists() or len(runs) != 1:
                raise HistoryRefusal("attempt_history_mismatch", "new campaign already has history or prior runs")
            body = {"version": VERSION, "campaign_id": campaign_id,
                    "cohort_digest": cohort_digest, "bundles": keys,
                    "runs": current, "attempts": []}
            writer = Writer(path, body, current[0])
            writer._commit(body)
        else:
            body = read(path)
            prior = [r for r in runs if r["id"] != run_id]
            require_coverage(body, campaign_id, prior, cohort_digest, keys)
            if any(r["id"] == run_id for r in body["runs"]):
                raise HistoryRefusal("attempt_history_mismatch", "a run cannot be opened twice")
            writer = Writer(path, body, current[0])
            new = copy.deepcopy(body)
            new["runs"].append(current[0])
            writer._commit(new)
        yield writer
