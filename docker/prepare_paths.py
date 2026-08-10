# Container path preparation
###########################

"""Make every path in ``oncotriage.paths._DOCKER_PATHS`` exist, and say so.

Item 21. Run from ``docker/entrypoint.sh`` on every container start, before the
service does.

THE DEFECT THIS CLOSES
----------------------
``oncotriage/paths.py`` fixes fourteen absolute paths for the container (it
was fourteen until pass 20f-3 dropped the never-read ``requirements_path``). The
Dockerfile created three of them; ``docker-compose.yml`` then mounted the host
code directory over the whole of ``/app``, hiding those three, and declared no
volume for data or results at all. The consequences were not symmetrical — one
of them was silent:

  * ``inferences_path`` is ``/app/data/inferences.db`` and its parent did not
    exist. ``sqlite3.connect`` on a path whose directory is missing raises
    ``OperationalError``, and ``log_inference`` catches ``sqlite3.Error`` by
    design so that a logging fault cannot kill the pipeline. So ``POST /match``
    ran the whole pipeline, spent real money on a Stage 5 call, returned 200,
    printed one "Database logging failed (non-critical)" line, and stored
    nothing.
  * ``checkpoint_path`` and ``results_path`` did not exist either, and their
    writers raise, so those failed loudly.

THE LIST IS DERIVED, NOT RETYPED
--------------------------------
This module reads ``_DOCKER_PATHS`` out of ``oncotriage.paths``. A path added to
that table is created here with no edit, and — the reason that matters — a path
added there CANNOT be forgotten here. A hand-maintained copy would drift, and
the failure mode of the drift is the silent one above.

DIRECTORY OR FILE is decided by the table's own convention: every value that is
a directory ends with ``/`` and every value that is a file does not. That is
checked rather than assumed — ``_classify`` raises on a value that is neither
clearly one nor the other, because guessing wrong means either creating a
DIRECTORY named ``inferences.db`` (after which every ``sqlite3.connect`` on it
fails with a message about the file being unopenable) or failing to create a
parent that something is about to write into.

WHAT IT REPORTS. One line per path: the variable name, the resolved location,
and which of ``exists`` / ``created`` / ``parent-created`` happened. A path that
was created on a start other than the first is a fact worth seeing in the log —
it means the volume was empty, which on a restart means the volume was replaced.

WHY IT DOES NOT SIMPLY TRUST THE VOLUMES. A directory under ``/app`` that no
volume covers is created inside the BIND MOUNT, i.e. in the developer's checked
out repository. There are none today: every writable path in the table is
covered by a named volume, and ``/app/`` already exists in the code tree. The report is what would make a future one visible
instead of leaving a mystery directory in someone's git status.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile


# The entrypoint runs this before the service, so an import failure here is the
# first thing anyone sees. Make it say what is wrong rather than showing a
# traceback about a package name.
try:
    from oncotriage import paths
except ImportError as exc:  # pragma: no cover - container bring-up only
    print(
        "[prepare-paths] FATAL: cannot import the oncotriage package.\n"
        f"                {type(exc).__name__}: {exc}\n"
        "                The image installs it with `pip install --editable /app`,\n"
        "                so this means /app does not contain the package -- most\n"
        "                likely a bind mount pointing somewhere unexpected.",
        file=sys.stderr,
    )
    raise


#------------------------------------------------------------------------------


def _classify(value):
    """Return ('dir', value) or ('file', parent-of-value).

    The table's convention is that a directory value ends with a separator and a
    file value does not. Anything else raises: see the module docstring for what
    guessing costs in each direction.
    """
    if value.endswith(os.sep) or value.endswith("/"):
        return "dir", value
    if os.path.splitext(value)[1]:
        return "file", os.path.dirname(value)
    raise RuntimeError(
        f"cannot tell whether {value!r} names a directory or a file: it neither "
        f"ends with a separator nor has a file extension. Fix the convention in "
        f"oncotriage/paths.py:_DOCKER_PATHS or teach _classify about it -- do "
        f"not guess, because creating a directory where a database belongs "
        f"fails every later connect."
    )


def prepare(table=None):
    """Create every directory the table implies. Returns a list of report rows.

    Raises on the first path it cannot create. That is deliberate: the service
    about to start needs these, and a container that dies here is far cheaper to
    diagnose than one that runs and drops rows.
    """
    if table is None:
        table = paths._DOCKER_PATHS

    rows = []
    for name in sorted(table):
        value = table[name]
        kind, directory = _classify(value)

        existed = os.path.isdir(directory)
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"could not create {directory!r} for path variable {name!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        if existed:
            status = "exists"
        elif kind == "file":
            status = "parent-created"
        else:
            status = "created"

        rows.append((name, value, status))

    return rows


#------------------------------------------------------------------------------


# ===========================================================================
# SEEDING THE MeSH CORE LOOKUPS
# ===========================================================================
#
# THE DEFECT THIS CLOSES. `/app/data/mesh/` is inside the `app_data` named
# volume, and `docker compose down -v` destroys it. On the next bring-up
# `load_mesh_filter()` raises DegradedDependencyError -- correctly, that is item
# 11a working -- so every one of the six containers reports healthy and the
# first POST /match dies in Stage 1. Until this function, the only fix was a
# `docker compose cp` written down in DOCKER CLEAN BRING-UP.md and in nobody's
# muscle memory.
#
# The two files below are the two `load_mesh_filter()` REQUIRES. They are 105 KB
# together, they are derived from the public-domain NLM MeSH descriptor file,
# and they are vendored into the build context at docker/mesh-core/ -- see
# PROVENANCE.md there for the measurement that overturned
# `DOCKER CLEAN BRING-UP.md` §3's claim that this was impossible, and for why
# the three UMLS-derived OPTIONAL lookups are deliberately not vendored with
# them.
#
# WHY HERE AND NOT A `COPY` INTO /app/data IN THE Dockerfile. Docker initialises
# a named volume from the image content at the mount path the first time that
# volume is mounted, and pass 20g emptied those mount points precisely because
# five containers doing that copy concurrently FAILS:
#
#     failed to mkdir .../app-results/_data/fhir_exploration: file exists
#
# Baking files into /app/data would put that copy -- and that race -- straight
# back. Seeding from an image-only directory in this script instead keeps the
# mount points empty, and this script's copy is safe under concurrency by
# construction: os.makedirs(exist_ok=True), then write-to-temp + os.replace,
# which is atomic on POSIX. Five containers racing produce the same bytes and
# the last rename wins.
#
# IT NEVER OVERWRITES. A file already in the volume is left exactly as it is and
# reported as `present`. That is what keeps the documented `docker compose cp`
# route working for anyone who wants a NEWER lookup than the vendored one, and
# it is why the hash check below applies to the SOURCE and not to the
# destination.

_MESH_CORE_DIR = "/usr/local/lib/oncotriage-docker/mesh-core"
"""Where the Dockerfile puts the vendored lookups.

An IMAGE-ONLY path, outside /app, for the same reason the entrypoint and this
script are: docker-compose.yml no longer bind-mounts the host tree over /app,
but anyone re-enabling development mode will, and a seed source living under
/app would then be whatever is in a working tree rather than what was built.
"""

_MESH_PROVENANCE = "PROVENANCE.json"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def seed_mesh_core(source_dir=None, dest_dir=None):
    """Copy the vendored MeSH core lookups into the data volume. Returns rows.

    Args:
        source_dir: image-only directory holding the vendored files and their
                    PROVENANCE.json. Defaults to ``_MESH_CORE_DIR``.
        dest_dir:   where ``load_mesh_filter()`` looks. Defaults to
                    ``paths.data_MeSH_path``, READ HERE rather than at import
                    because every path in that module resolves lazily and this
                    module must import on a machine that has no data tree.

    Returns:
        A list of ``(filename, status)`` rows: ``present`` (already there,
        untouched), ``seeded`` (copied), or ``source-missing``.

    Raises:
        RuntimeError: a vendored file's sha256 does not match PROVENANCE.json,
            or PROVENANCE.json is unreadable or malformed.

    WHY A HASH CHECK AND NOT JUST A COPY. A truncated or half-written lookup
    parses as valid JSON far more often than it should -- both files are flat
    objects, so any prefix ending at a complete entry plus a brace is loadable --
    and a `mesh_c04_lookup.json` missing its tail is a Stage 4 filter that
    silently stops recognising the descriptors in it. That is the same class of
    fault item 11a raised for: a detection layer PRESENT but not doing its job,
    with nothing to distinguish it from one that is. A container that refuses to
    start is the loud version.

    NOT RAISING when the source directory is absent is deliberate and is the one
    asymmetry here. This module runs in every container built from this image,
    including any built from a context where docker/mesh-core/ was excluded;
    reporting `source-missing` leaves the pre-existing behaviour exactly as it
    was -- load_mesh_filter() raises its own, better message at the first
    request -- while a raise here would turn a soft, well-diagnosed failure into
    a container that will not boot.
    """
    if source_dir is None:
        source_dir = _MESH_CORE_DIR
    if dest_dir is None:
        dest_dir = paths.data_MeSH_path

    manifest_path = os.path.join(source_dir, _MESH_PROVENANCE)
    if not os.path.isdir(source_dir) or not os.path.isfile(manifest_path):
        return [("(docker/mesh-core)", "source-missing")]

    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        expected = manifest["files"]
    except (OSError, ValueError, KeyError) as exc:
        raise RuntimeError(
            f"cannot read the MeSH provenance manifest {manifest_path!r}: "
            f"{type(exc).__name__}: {exc}\n"
            f"  It must be JSON with a 'files' object mapping each vendored "
            f"filename to {{'sha256': ...}}. Without it the seeded lookups "
            f"cannot be verified, and an unverified lookup is the failure this "
            f"check exists to prevent."
        ) from exc

    os.makedirs(dest_dir, exist_ok=True)

    rows = []
    for filename in sorted(expected):
        src = os.path.join(source_dir, filename)
        dst = os.path.join(dest_dir, filename)

        if not os.path.isfile(src):
            rows.append((filename, "source-missing"))
            continue

        actual = _sha256(src)
        if actual != expected[filename]["sha256"]:
            raise RuntimeError(
                f"vendored MeSH lookup {src!r} does not match "
                f"{_MESH_PROVENANCE}:\n"
                f"    expected sha256 {expected[filename]['sha256']}\n"
                f"    actual   sha256 {actual}\n"
                f"  Refusing to seed it. A partial or edited lookup still "
                f"parses as JSON and still loads, and the only symptom is a "
                f"Stage 4 site filter that quietly recognises fewer "
                f"descriptors. Re-copy the file from the MeSH build and update "
                f"docker/mesh-core/{_MESH_PROVENANCE}; see PROVENANCE.md."
            )

        if os.path.exists(dst):
            rows.append((filename, "present"))
            continue

        # Write-then-rename: atomic, so five containers seeding the same fresh
        # volume at once cannot leave a half-copied file behind for the sixth to
        # read. The temp file is in the DESTINATION directory because os.replace
        # is only atomic within one filesystem, and /app/data is a volume.
        fd, tmp = tempfile.mkstemp(prefix=f".{filename}.", dir=dest_dir)
        os.close(fd)
        try:
            shutil.copyfile(src, tmp)
            # mkstemp creates 0600 BY DESIGN -- it is meant for secrets. These
            # are public-domain lookup tables read by every service in the
            # stack, and a 0600 file owned by whoever seeded first is
            # unreadable to any container that runs as a different user. All six
            # run as appuser today, so this is latent rather than live; it was
            # found by looking at the seeded files' mode in the volume, not by
            # reading. 0644 is what a `docker compose cp` of the same file
            # produces, so the two provisioning routes now agree.
            os.chmod(tmp, 0o644)
            os.replace(tmp, dst)
        except OSError:
            # Recorded by the re-raise: the caller (the entrypoint) treats any
            # exception here as fatal, and the cleanup must not mask the cause.
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        rows.append((filename, "seeded"))

    return rows


#------------------------------------------------------------------------------


def main():
    if not paths.IS_DOCKER:
        # Refuses rather than proceeds. Run outside a container this would
        # create /app on the host, and the whole point of the module is that a
        # directory appearing where nobody expects one is a defect.
        print(
            "[prepare-paths] FATAL: oncotriage.paths.IS_DOCKER is False, so the "
            "Docker path table is not the one in force. Refusing to create "
            "container paths on a non-container filesystem.",
            file=sys.stderr,
        )
        return 1

    rows = prepare()

    width = max(len(name) for name, _, _ in rows)
    print(f"[prepare-paths] {len(rows)} container paths from "
          f"oncotriage.paths._DOCKER_PATHS:")
    for name, value, status in rows:
        print(f"[prepare-paths]   {name:<{width}}  {value:<34} {status}")

    created = [name for name, _, status in rows if status != "exists"]
    if created:
        print(f"[prepare-paths] created storage for: {', '.join(created)}")
    print("[prepare-paths] all container paths present.")

    # The MeSH core lookups. AFTER the directories, because it writes into one
    # of them. Failure is fatal for the same reason prepare() is: a lookup that
    # could not be verified must not reach a filter that classifies trials.
    mesh_rows = seed_mesh_core()
    for filename, status in mesh_rows:
        print(f"[prepare-paths]   mesh-core  {filename:<26} {status}")
    if any(status == "source-missing" for _, status in mesh_rows):
        print("[prepare-paths] NOTE: some MeSH core lookups were not in the "
              "image. load_mesh_filter() will raise DegradedDependencyError "
              "naming them at the first request, and GET /health reports 503 "
              "before that.")

    return 0


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
