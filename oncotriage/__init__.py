"""OncoTriage — the importable foundation of the pipeline.

Item 20c, pass 1. Before this pass every project file was a numbered,
space-containing script that could only be ``exec()``'d into a caller's
globals. Files 01, 02 and 03 are now real modules underneath, and the numbered
files survive as shims that re-export them so nothing else had to change.

Module layout and the ONE allowed import direction
--------------------------------------------------
::

    settings   <-- paths <-- config <-- utils
        ^                       ^          ^
        +-----------------------+          |
                                           |
    constants <---------------------- fhir.parser
    registries.cancer_code_registry <- storage.database_logger
                       ^                   |
                       +-------------------+

    settings   env-var names, path resolution
    paths      IS_DOCKER, _glob_one, load_env_keys() and every path variable,
               all of them resolved LAZILY (pass 20c-2b)
    constants  the two coding-system sentinels (imports nothing)
    config     every tunable, plus LAZY client/keys accessors
    utils      cost, retry, partial dates, exec_chain, CaffeinateSession

    registries   clinical terminology: cancer codes, MeSH C04 ancestry
    extraction   rule-based stage and histology extraction from criteria text
    fhir         parse_fhir_bundle and the per-resource parsers (File 07)
    storage      the SQLite schema and log_inference (File 14)

Arrows point at what a module may import. There is no arrow back:

* ``config`` must never import ``utils``. That edge is the cycle this pass
  removed — ``02- Utility Functions.py`` read ``PRICING_CONFIG`` and
  ``qdrant_client`` out of File 03 while File 03 called ``load_env_keys()``
  out of File 02. Under ``exec()`` both landed in one namespace and resolved
  at runtime; as modules it is an ``ImportError``. ``load_env_keys`` moving
  into ``settings`` is what broke it.
* ``constants`` imports nothing at all, from anywhere.

Importing any module in this package is side-effect free, with no exception
left: no network client is constructed, no local model is downloaded or loaded,
no database is opened, and — since pass 20c-2b — no directory is resolved and no
file is read.

``paths`` used to be the exception. Every path was a module-level assignment, so
importing it globbed the whole sibling directory tree and RAISED when the tree
was not there. ``config`` imports ``paths``, so ``import oncotriage.config``
inherited that, and a wheel installed into a fresh environment could not read
``MAX_WORKERS`` without the project's data directories beside it. Resolution is
lazy now, through a PEP 562 module ``__getattr__``: the first READ of a path
resolves it and caches it, and importing resolves nothing. ``47- Package Split
Test.py`` check 2b imports ``config`` with the root pointed at a directory that
does not exist and requires the import to succeed and the first read to raise.

This ``__init__`` deliberately imports NO submodule. ``import oncotriage`` must
stay free; the caller names the module it wants.
"""

__all__ = ["settings", "paths", "constants", "config", "utils",
           "registries", "extraction", "fhir", "storage"]


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  5 2026

@author: ramyalsaffar
"""
