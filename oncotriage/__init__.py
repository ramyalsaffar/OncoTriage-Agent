"""OncoTriage — the importable foundation of the pipeline.

Item 20c, pass 1 started this: before it, every project file was a numbered,
space-containing script that could only be ``exec()``'d into a caller's globals.
Passes 20c-1 through 20d-2 moved the content here module by module, leaving the
numbered files behind as re-export shims so nothing else had to change at once.

PASS 20e ENDED THE EXEC CHAIN. Every numbered file that survives is either a
thin entry point — a ``__main__`` guard, the imports it needs, and nothing else
— or a runnable service; none of them re-exports a name for anybody, none of
them ``exec()``s another file, and ``exec_chain`` itself is deleted. The
consequence to know about: the numbered sequence now says WHAT YOU CAN RUN, not
what the pipeline does, and it has gaps where a shim used to be. The reading
order lives in ``PIPELINE SEQUENCE.md`` at the code directory, which lists 01 to
29 in order with the package module that holds each stage.

THE ONE THING THE CHAIN PROVIDED THAT NOTHING REPLACES: ``01- Imports.py`` bound
``np``, ``pd``, ``Path``, ``OpenAI``, ``torch`` and eighty more third-party
names into one shared namespace, and only an ``exec()``'d file can do that. Its
other two jobs do have replacements — every entry point carries the same
six-line ``try: import oncotriage`` / ``sys.path.insert`` block it used to
provide, and every path resolves lazily on first read instead of eagerly at
chain load.

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
    utils      cost, retry, partial dates, CaffeinateSession

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
resolves it and caches it, and importing resolves nothing.
``tests/test_package_invariants.py`` check 2b imports ``config`` with the root
pointed at a directory that does not exist and requires the import to succeed
and the first read to raise.

This ``__init__`` deliberately imports NO submodule. ``import oncotriage`` must
stay free; the caller names the module it wants.
"""

__all__ = ["settings", "paths", "constants", "config", "utils",
           "registries", "extraction", "fhir", "storage"]

# THE ONE VERSION NUMBER (pass 20f-2).
#
# Three used to disagree: ``oncotriage/api/server.py`` typed "2.0.0" into
# ``FastAPI(version=...)``, typed it a second time into GET /pipeline/info, and
# ``pyproject.toml`` declared ``version = "0.1.0"``. So the HTTP surface and
# ``pip show oncotriage`` reported the same build two major versions apart, and
# the follow-up recorded in the server named only two of the three sites.
#
# WHY IT LIVES IN THIS FILE rather than in pyproject.toml with the code reading
# it back. ``importlib.metadata.version("oncotriage")`` reads the installed
# dist-info FROM DISK, and ``app = create_app()`` runs at import of
# ``oncotriage.api.server`` -- which ``tests/test_package_invariants.py``
# section 2 imports with ``builtins.open`` and ``io.open`` trapped to raise, on
# the standing rule that importing a package module reads no file. A module
# attribute costs nothing and cannot break that. pyproject.toml takes this
# string through ``[tool.setuptools.dynamic]``, which setuptools resolves by
# reading the AST of this file at BUILD time, so the direction is
# source -> metadata and there is no runtime edge at all.
#
# WHY 2.0.0: it is what the API has always told clients, and 0.1.0 described a
# package that no longer exists (pyproject's own description called it "the
# importable foundation: settings, paths, config, utils", true at pass 20c-1).
# Raising the metadata is invisible; lowering the API would announce a
# regression that never happened. Full argument at ``create_app()``.
__version__ = "2.0.0"


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  5 2026

@author: ramyalsaffar
"""
