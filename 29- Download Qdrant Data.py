# Download Qdrant Data
######################

"""
Download ALL data from Qdrant Cloud before account deletion.

Run from terminal:
    python "29- Download Qdrant Data.py"                       # default location
    python "29- Download Qdrant Data.py" --output-dir <scratch>

THIS FILE'S DOCUMENTED INVOCATION CHANGED, AND SO DID ITS BEHAVIOUR
--------------------------------------------------------------------
It used to say::

    Run from Spyder (after running 01, 02, 03):
        exec(open(code_path + "29- Download Qdrant Data.py").read())

That worked because EVERY STATEMENT IN THE FILE WAS AT MODULE LEVEL. It was the
last unguarded file in the repository: reading it into any namespace, for any
reason, created a directory, listed every collection in the Qdrant account,
scrolled every point of every collection over the network with payloads AND
vectors, wrote one JSON per collection and printed a summary. Item 20b guarded
Files 15, 16, 17, 22 and 24 and never reached this one, because nothing loads it.

Item 20c pass 3c-2 moved the body to
``oncotriage/retrieval/qdrant_backup.py:download_all_collections(output_dir)``
and put the call behind the ``__main__`` guard below. The old exec() line would
now load cleanly and download NOTHING, which is worse than failing, so it is
gone from this docstring rather than left as a trap.

The header comment this replaced also said the file "Uses qdrant_client and
results_path from exec chain". It never used ``results_path``: a scope-aware
symtable pass over the original reports exactly two free names, ``data_path``
and ``qdrant_client``, and the output directory was built from ``data_path``.
The comment named the wrong variable from the day it was written.

THE DESTINATION IS AN ARGUMENT NOW. ``download_all_collections`` requires it and
has no default -- the same reasoning as ``empty_database(db_path, flag)``: a
plausible thing to type while exploring a module must not start a full download
of a cloud database. ``--output-dir`` overrides; with no flag this entry point
passes ``qdrant_backup.default_output_dir()``, which is the historical
destination, ``{data_path}/06- Qdrant Downloaded Data for Latest Full Run/``.

NO RE-EXPORT SHIM. Nothing in the repository reads this file's namespace -- all
27 top-level names it leaked were grepped against every .py, .md, .toml and .yml
in the tree; the ones with hits (``Path``, ``json``, ``time``, ``c``, ``f``,
``name``, ``info``, ``points``, ``offset``, ``size``, ...) are third-party
imports and coincidental same-named locals in other files, and the distinctive
ones (``all_points``, ``point_data``, ``scroll_result``, ``serialized_vectors``,
``backup_files``, ``file_size_mb``, ``total_size``, ``vec_name``, ``vec_val``,
``collection_info``) have no hit anywhere outside it.
"""

import argparse
import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py" and "16- Database Query.py".
try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals()
         else None, "__file__"),
        (os.getcwd(), "cwd"),
    ):
        if _candidate and os.path.isdir(os.path.join(_candidate, "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise
    del _candidate, _how

from oncotriage.retrieval.qdrant_backup import (
    default_output_dir,
    download_all_collections,
)


#------------------------------------------------------------------------------


def _parse_args(argv=None):
    """--output-dir only. Defaults to None so that the historical destination is
    resolved INSIDE the guard, not while building the parser -- ``data_path`` is
    a lazy glob and `--help` must not fire it."""
    parser = argparse.ArgumentParser(
        description="Download every Qdrant collection (payloads and vectors) "
                    "to one JSON file each."
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Destination directory. Default: "
             "{data_path}/06- Qdrant Downloaded Data for Latest Full Run/",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    _args = _parse_args()
    _destination = _args.output_dir if _args.output_dir else default_output_dir()
    download_all_collections(_destination)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Apr 12 13:17:38 2026

@author: ramyalsaffar
"""
