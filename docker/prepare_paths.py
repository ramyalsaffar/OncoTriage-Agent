# Container path preparation
###########################

"""Make every path in ``oncotriage.paths._DOCKER_PATHS`` exist, and say so.

Item 21. Run from ``docker/entrypoint.sh`` on every container start, before the
service does.

THE DEFECT THIS CLOSES
----------------------
``oncotriage/paths.py`` fixes thirteen absolute paths for the container (it
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

import os
import sys


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
