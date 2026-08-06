# Container DAG generation
#########################

"""Put the generated ``trial_refresh_weekly`` DAG where the scheduler will parse it.

Item 21, defect 7. Run once from the ``airflow-webserver`` command, before
``airflow api-server`` starts and therefore before the scheduler is allowed to
start (the scheduler waits on the webserver's healthcheck).

THE DEFECT
----------
Nothing in the container ever ran ``23- Airflow DAG.py``. The webserver's command
was ``mkdir -p /app/airflow_home/dags && airflow db migrate && airflow
api-server``; the scheduler's was ``sleep 30 && airflow scheduler``. So the DAG
folder was created EMPTY and the scheduler parsed an empty directory forever. The
UI came up, the stack looked healthy, and the one thing Airflow is in this
project to do never happened. CLAUDE.md already recorded that ``DAGS_FOLDER``
itself is correct and that this generation step is the real gap; this closes it.

WHY THIS IS NOT JUST ``python "23- Airflow DAG.py"``
---------------------------------------------------
Because of what ``write_dag_file`` does when the file is already there and
DIFFERS from the generator: it prints a warning and REFUSES TO OVERWRITE, on the
reasoning that the file may have been edited in place by a human. That is right
on a developer's machine. In the container it is wrong, and quietly so:

  * the dags directory is a named volume, so it survives ``docker compose
    down`` and every restart;
  * nobody edits a file inside a named volume by hand — the only writer is this
    script;
  * so the ONLY way the on-disk text can differ from the generator is that the
    generator changed, i.e. somebody edited
    ``oncotriage/orchestration/dag_generator.py`` and rebuilt;
  * and the refusal means the scheduler keeps running the OLD DAG while the
    operator has every reason to believe they deployed the new one. The
    warning is printed at container start, thousands of lines above wherever
    they are looking.

So this script REPLACES a differing file, and says loudly that it did, with both
sha256s. It does not change ``write_dag_file`` — that function's behaviour is
correct for the host and is relied on there. It removes the stale file first and
then calls the normal writer, so the file is still written by exactly one piece
of code.

WHAT HAPPENS ON EACH ``docker compose up``
------------------------------------------
  * first up, empty volume  -> file absent  -> written.   "created"
  * second up, nothing else changed -> text matches -> nothing written. "current"
  * up after the generator changed  -> text differs -> old file removed, new
    one written, both hashes printed. "replaced"

All three are idempotent in the sense that matters: run it twice in a row and
the second run is a no-op that reports "current".

IT VERIFIES AFTERWARDS. Every path above ends by reading the file back off disk
and comparing it with the generated text, and exits non-zero if they differ. A
generation step that reports success without checking its own output is how the
empty-dags-folder defect survived: the compose file's ``mkdir -p`` also
"succeeded" every single time.
"""

import hashlib
import sys
from pathlib import Path


try:
    from oncotriage.orchestration.dag_generator import (
        build_dag_content,
        write_dag_file,
    )
    from oncotriage.orchestration.home import resolve_airflow_home
except ImportError as exc:  # pragma: no cover - container bring-up only
    print(
        "[generate-dag] FATAL: cannot import the oncotriage orchestration "
        f"modules.\n               {type(exc).__name__}: {exc}",
        file=sys.stderr,
    )
    raise


#------------------------------------------------------------------------------


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sync_dag(airflow_home=None):
    """Write the DAG, replacing a stale one. Returns (path, action).

    action is one of "created", "current", "replaced".
    """
    if airflow_home is None:
        airflow_home = resolve_airflow_home()

    content = build_dag_content()
    dag_file = Path(airflow_home) / "dags" / "trial_refresh_weekly.py"

    action = "created"
    if dag_file.exists():
        on_disk = dag_file.read_text()
        if on_disk == content:
            action = "current"
        else:
            action = "replaced"
            print("[generate-dag] " + "!" * 62)
            print(f"[generate-dag] The DAG on disk DIFFERS from this generator.")
            print(f"[generate-dag]   file:      {dag_file}")
            print(f"[generate-dag]   on disk:   sha256 {_sha256(on_disk)}")
            print(f"[generate-dag]   generated: sha256 {_sha256(content)}")
            print("[generate-dag] Replacing it. In a container the dags directory")
            print("[generate-dag] is a named volume that only this script writes,")
            print("[generate-dag] so a difference means the generator changed --")
            print("[generate-dag] not that somebody edited the DAG by hand.")
            print("[generate-dag] " + "!" * 62)
            dag_file.unlink()

    if action != "current":
        # The normal writer, so the file is produced by one piece of code on
        # every path. With the stale file removed it takes its "create" branch.
        write_dag_file(airflow_home, content=content)

    # Verify. See the module docstring: the step this replaces reported success
    # on every run while producing nothing.
    if not dag_file.exists():
        print(f"[generate-dag] FATAL: {dag_file} does not exist after writing it.",
              file=sys.stderr)
        raise RuntimeError(f"DAG file missing after write: {dag_file}")

    written = dag_file.read_text()
    if written != content:
        print(
            f"[generate-dag] FATAL: {dag_file} does not match the generator after "
            f"writing it.\n"
            f"               on disk:   sha256 {_sha256(written)}\n"
            f"               generated: sha256 {_sha256(content)}",
            file=sys.stderr,
        )
        raise RuntimeError(f"DAG file content mismatch after write: {dag_file}")

    return dag_file, action


def main():
    dag_file, action = sync_dag()
    print(f"[generate-dag] {action}: {dag_file} "
          f"({dag_file.stat().st_size} bytes, sha256 "
          f"{_sha256(dag_file.read_text())})")
    print("[generate-dag] verified against the generator.")
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
