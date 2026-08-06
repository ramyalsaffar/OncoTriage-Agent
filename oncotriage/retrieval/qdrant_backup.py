# Download Qdrant Data
######################

"""Download every Qdrant collection -- payloads AND vectors -- to JSON on disk.

Moved out of "29- Download Qdrant Data.py" by item 20c, pass 3c-2. That file is
now a thin entry point with a ``--output-dir`` flag and a ``__main__`` guard.

FILE 29 WAS THE LAST UNGUARDED FILE IN THE REPOSITORY
------------------------------------------------------
Every statement in it was at module level. No function, no ``__main__`` guard,
no bootstrap. Loading it -- by any means -- created a directory, listed every
collection in the Qdrant account, scrolled every point of every collection with
payloads and vectors over the network, wrote one JSON file per collection and
printed a summary. Item 20b guarded Files 15, 16, 17, 22 and 24 and did not
reach this one, because nothing loads it: the only documented way to run it was
to paste it into a Spyder session.

That is exactly why it survived. A file nobody imports accrues no pressure to be
importable, right up until someone imports it.

THREE THINGS CHANGED, AND ALL THREE ARE DELIBERATE BEHAVIOUR CHANGES
---------------------------------------------------------------------
1. THE BODY IS A FUNCTION, ``download_all_collections(output_dir, client=None)``,
   and the entry point calls it under a ``__main__`` guard. Importing this
   module lists nothing, scrolls nothing, creates nothing and writes nothing.

2. ``output_dir`` IS REQUIRED AND HAS NO DEFAULT. The same reasoning as
   ``storage.maintenance.empty_database``: ``download_all_collections()`` typed
   at a prompt while exploring the module must not start a full download of a
   cloud database into the project's data tree. ``default_output_dir()`` is
   there for the caller who wants the historical destination, and it is a
   separate call because asking for the path must not be the thing that
   downloads. It also RESOLVES ONLY WHEN CALLED -- ``data_path`` is a lazy path.

3. THE DOCSTRING'S DOCUMENTED INVOCATION IS GONE, so the docstring changed.
   File 29's said::

       exec(open(code_path + "29- Download Qdrant Data.py").read())

   That worked precisely because the file was one long script; behind a guard it
   would exec cleanly and download nothing, which is worse than failing. The
   entry point documents ``python "29- Download Qdrant Data.py"`` instead.

   Its header comment ALSO claimed the file "Uses qdrant_client and results_path
   from exec chain". It never used ``results_path`` -- the symtable measurement
   for this pass reports exactly two free names, ``data_path`` and
   ``qdrant_client``. The comment named the wrong variable and had done so since
   the file was written. Corrected here rather than carried across.

THE CLIENT COMES FROM ``oncotriage.config``, NOT ``agent.deps``
----------------------------------------------------------------
Same rule as ``retrieval.indexer`` and for the same reason: a stub installed for
an agent test must not silently redirect what a BACKUP reads. A backup that
quietly dumped a fixture's stub responses instead of the live account would be
indistinguishable from a real one until the day it was restored from. The
``client`` argument exists so a caller can pass its own explicitly -- which is
different from a seam another module can reach into.

THE SILENT ``except Exception: pass`` IS NOW LOGGED
----------------------------------------------------
File 29 wrapped ``get_aliases()`` in a bare ``except Exception: pass``. The
alias listing is genuinely optional -- the download does not use it -- so
continuing is right, and ``Exception and Fallback Audit.md`` line 272 rules it
acceptable on that basis. What was not right is that it was SILENT: this
project's standing rule is that no exception is caught without being re-raised
or recorded, and that a fallback logs which path it took. The recovery is
unchanged; the exception type and message are printed now, and the count is
returned in the summary as ``aliases_error``. This is the one change in this
module that is not a path accessor, a client accessor or a guard, and it is
called out rather than folded in.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Imports ``json``, ``time``, ``pathlib`` and ``oncotriage.config``. It builds no
client (``config.get_qdrant_client()`` is lazy and is called inside the
function), resolves no path, creates no directory and opens no socket.
"""

import json
import time
from pathlib import Path

from oncotriage import config
from oncotriage import paths


#------------------------------------------------------------------------------


DEFAULT_OUTPUT_SUBDIR = "06- Qdrant Downloaded Data for Latest Full Run/"
"""Where File 29 wrote, relative to ``data_path``.

A named constant rather than a literal inside the accessor because it is the
one thing about the historical destination a reader might need to check against
what is actually on disk.
"""

# The scroll page size (100) and the inter-page pause (0.05s) are left as
# LITERALS at their call sites below, exactly where File 29 had them. Naming
# them would read better and it is not this pass's change to make: a relocation
# whose diff includes renamings stops being a relocation you can check by
# reading the diff. They are not tunables of the pipeline either -- this
# script's politeness to the Qdrant endpoint does not belong in
# oncotriage/config.py -- so promoting them is a judgement call, recorded as a
# follow-up rather than taken here.


def default_output_dir():
    """The directory File 29 wrote to: ``data_path`` + DEFAULT_OUTPUT_SUBDIR.

    RESOLVES ON THE CALL and CREATES NOTHING. Both halves matter:

      * resolving lazily is the package rule -- ``data_path`` is a glob over the
        sibling tree and importing a module must not fire it;
      * creating nothing is the lesson pass 20c-3b applied to
        ``fhir/explore.py``'s ``output_dir()``. A function that answers "where
        would this write" must not be the thing that makes the directory, or a
        caller who only wanted to PRINT the destination has already changed the
        filesystem.

    The mkdir happens in ``download_all_collections``, once, before the first
    write, on every call path -- which is where File 29 had it.
    """
    return paths.data_path + DEFAULT_OUTPUT_SUBDIR


def download_all_collections(output_dir, client=None):
    """Download every collection to one JSON file each under output_dir.

    Args:
        output_dir: Destination directory. REQUIRED, with no default -- see the
            module docstring. Created if absent, before the first write.
        client:     A Qdrant client. None calls
            ``oncotriage.config.get_qdrant_client()``, which builds once and
            caches. Deliberately NOT ``agent.deps``.

    Returns:
        A summary dict: the directory, the collection names seen, the ones
        skipped as empty, the point count written per collection, the total
        bytes, and ``aliases_error`` -- None when the alias listing succeeded,
        otherwise the repr of what it raised.

    File 29 returned nothing and printed everything. It still prints everything,
    identically; the return value is added so a caller -- including the test that
    proves this writes only into the directory it is given -- can assert on the
    outcome rather than scrape stdout.
    """
    if client is None:
        client = config.get_qdrant_client()

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    summary = {
        "output_dir": str(output_dir),
        "collections": [],
        "skipped_empty": [],
        "points_written": {},
        "aliases_error": None,
    }

    # =====================================================================
    # LIST ALL COLLECTIONS
    # =====================================================================

    collections = client.get_collections().collections
    print(f"Found {len(collections)} collections:")
    for c in collections:
        print(f"  - {c.name}")
    summary["collections"] = [c.name for c in collections]

    # File 29 swallowed this with a bare `pass`. Continuing is right -- nothing
    # below uses the aliases -- but going on in silence is not. The path taken
    # is printed and the failure is recorded in the summary.
    try:
        all_aliases = client.get_aliases()
        print(f"\nAliases: {all_aliases}")
    except Exception as e:
        summary["aliases_error"] = repr(e)
        print(f"\nAliases: unavailable ({type(e).__name__}: {e})")
        print("  Continuing -- the download does not use them.")

    print()

    # =====================================================================
    # DOWNLOAD EACH COLLECTION
    # =====================================================================

    for collection_info in collections:
        name = collection_info.name

        info = client.get_collection(collection_name=name)
        point_count = info.points_count

        print(f"{'='*60}")
        print(f"Collection: {name}")
        print(f"  Points: {point_count}")

        if point_count == 0:
            print(f"  Empty. Skipping.")
            summary["skipped_empty"].append(name)
            continue

        print(f"  Downloading...")

        all_points = []
        offset = None

        while True:
            scroll_result = client.scroll(
                collection_name=name,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )

            points, next_offset = scroll_result

            for point in points:
                point_data = {
                    "id": point.id,
                    "payload": point.payload,
                }

                if isinstance(point.vector, dict):
                    serialized_vectors = {}
                    for vec_name, vec_val in point.vector.items():
                        if hasattr(vec_val, 'indices'):
                            serialized_vectors[vec_name] = {
                                "type": "sparse",
                                "indices": list(vec_val.indices),
                                "values": list(vec_val.values),
                            }
                        else:
                            serialized_vectors[vec_name] = {
                                "type": "dense",
                                "values": list(vec_val) if not isinstance(vec_val, list) else vec_val,
                            }
                    point_data["vectors"] = serialized_vectors
                elif point.vector is not None:
                    point_data["vectors"] = {
                        "default": {
                            "type": "dense",
                            "values": list(point.vector),
                        }
                    }

                all_points.append(point_data)

            if next_offset is None:
                break
            offset = next_offset

            print(f"    {len(all_points)}/{point_count}...", end="\r")
            time.sleep(0.05)

        print(f"    {len(all_points)}/{point_count} done.    ")

        output_file = Path(output_dir) / f"{name}.json"

        with open(output_file, "w") as f:
            json.dump(
                {
                    "collection_name": name,
                    "point_count": len(all_points),
                    "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "points": all_points,
                },
                f,
                indent=2,
            )

        file_size_mb = output_file.stat().st_size / (1024 * 1024)
        print(f"  Saved: {output_file.name} ({file_size_mb:.1f} MB)")
        print()
        summary["points_written"][name] = len(all_points)

    # =====================================================================
    # SUMMARY
    # =====================================================================

    print("=" * 60)
    print("DOWNLOAD COMPLETE")
    print("=" * 60)

    backup_files = list(Path(output_dir).glob("*.json"))
    total_size = sum(f.stat().st_size for f in backup_files) / (1024 * 1024)

    print(f"  Directory: {output_dir}")
    print(f"  Collections: {len(backup_files)}")
    print(f"  Total size: {total_size:.1f} MB")

    for f in sorted(backup_files):
        size = f.stat().st_size / (1024 * 1024)
        print(f"    {f.name}: {size:.1f} MB")

    print(f"\nQdrant data is safe.")

    summary["total_size_mb"] = total_size
    return summary


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Apr 12 13:17:38 2026

@author: ramyalsaffar
"""
