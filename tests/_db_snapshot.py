# Verified Database Snapshots For Tests
#######################################

"""A test reads the production database only through a VERIFIED BYTE COPY.

WHY THIS FILE EXISTS (the P4b recovery)
---------------------------------------
Three test files opened the RECORDED production ``inferences.db`` directly: two
through ``mode=ro`` URIs and one through ``mode=ro&immutable=1``. One more
reached it read-write through a dashboard loader the test had not redirected.
Each is wrong in its own way, and all three were measured:

  * a ``mode=ro`` open of a WAL database CREATES its ``-wal`` and ``-shm`` side
    files, so a "read-only" guard wrote into the production directory;
  * a plain read-write connect CLOSES by checkpointing, so on a database with
    uncheckpointed frames it rewrites the production file;
  * ``immutable=1`` turns off locking AND change detection, and a WAL database's
    committed frames live in its ``-wal``. So it is valid only on a copy that
    has been checkpointed and is known not to change, never on a live file.

WHAT THIS PROVIDES
------------------
``snapshot`` copies the database and its ``-wal`` as BYTES. It opens nothing
through sqlite. It then proves the copy is a consistent point: the source's
digests are equal before and after the copy, and the copy's digests equal them.
``frozen_copy`` checkpoints that COPY and leaves it with no side files, which is
the only thing ``immutable=1`` may be pointed at. ``ProductionConnectGuard``
refuses, before the file is opened, any ``sqlite3.connect`` that resolves to a
path it was given, and records the attempt so a test can assert there were none.

No ``test_`` prefix: every runner selects on that prefix, and a file of no
checks would be counted as a file that ran. It imports nothing from the project.
"""

import hashlib
import os
import shutil
import sqlite3
import urllib.parse

SNAPSHOT_ATTEMPTS = 3


class SnapshotInconsistent(RuntimeError):
    """The source changed while it was being copied, on every attempt."""


def _sha(path):
    if not os.path.exists(path):
        return "absent"
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_digests(db_path):
    """``(main file sha256, -wal sha256)``; ``"absent"`` for a missing file.

    The ``-shm`` is deliberately not included: it is a shared-memory index that
    any reader may rewrite, and it holds no committed data.
    """
    return (_sha(db_path), _sha(db_path + "-wal"))


def snapshot(db_path, dest_dir, *, name=None, attempts=SNAPSHOT_ATTEMPTS,
             _between_copies=None):
    """A verified consistent byte copy of ``db_path`` in ``dest_dir``, or None.

    None when ``db_path`` is not a file. RAISES ``SnapshotInconsistent`` when
    the source changed during the copy on every one of ``attempts`` tries.
    ``_between_copies`` is a test seam, called after the bytes are copied and
    before the source is re-read.
    """
    if not os.path.isfile(db_path):
        return None
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, name or os.path.basename(db_path))
    for _ in range(attempts):
        for side in ("", "-wal", "-shm"):
            if os.path.exists(dest + side):
                os.remove(dest + side)
        before = file_digests(db_path)
        shutil.copyfile(db_path, dest)
        if before[1] != "absent" and os.path.exists(db_path + "-wal"):
            shutil.copyfile(db_path + "-wal", dest + "-wal")
        if _between_copies is not None:
            _between_copies()
        after = file_digests(db_path)
        if before == after and file_digests(dest) == before:
            return dest
    raise SnapshotInconsistent(
        f"{db_path} changed while it was being copied, on each of {attempts} "
        f"attempts; no consistent snapshot could be taken")


def frozen_copy(db_path, dest_dir, *, name=None):
    """A snapshot checkpointed into its main file with no side files, or None.

    The checkpoint is done on the COPY, read-write, which is the only database
    this function ever connects to. Afterwards the copy is a single file that
    nothing writes, so ``immutable=1`` is valid on it. The returned
    ``(path, sha256)`` lets a test show at the end that it did not change.
    """
    dest = snapshot(db_path, dest_dir, name=name)
    if dest is None:
        return None
    conn = sqlite3.connect(dest)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        conn.execute("PRAGMA journal_mode = DELETE").fetchall()
    finally:
        conn.close()
    for side in ("-wal", "-shm"):
        if os.path.exists(dest + side):
            os.remove(dest + side)
    return dest, _sha(dest)


def connect_target(database):
    """The filesystem path a ``sqlite3.connect`` argument names, or None."""
    text = (os.fsdecode(database) if isinstance(database, (bytes, os.PathLike))
            else str(database))
    if text.startswith("file:"):
        text = urllib.parse.unquote(text[5:].split("?", 1)[0])
    if text in ("", ":memory:"):
        return None
    return os.path.realpath(text)


class ProductionConnectGuard:
    """Refuse and record every ``sqlite3.connect`` to the given paths.

    Installed on the ``sqlite3`` module's ``connect`` attribute, so every module
    that calls ``sqlite3.connect(...)`` at call time is covered, including the
    dashboard loaders and the storage layer. ``attempts`` lists each refused
    target. ``uninstall`` restores the connect that was there at ``install``.
    """

    def __init__(self, *paths):
        self.targets = {os.path.realpath(p) for p in paths if p}
        self.attempts = []
        self._previous = None

    def _guard(self, database, *args, **kwargs):
        if connect_target(database) in self.targets:
            self.attempts.append(str(database))
            raise sqlite3.OperationalError(
                f"refused by the test's production guard: {database} is the "
                f"recorded production database; read a verified snapshot")
        return self._previous(database, *args, **kwargs)

    def install(self):
        self._previous = sqlite3.connect
        sqlite3.connect = self._guard
        return self

    def uninstall(self):
        if self._previous is not None and sqlite3.connect == self._guard:
            sqlite3.connect = self._previous
        restored = sqlite3.connect is self._previous
        return restored
