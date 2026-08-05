"""OncoTriage — the importable foundation of the pipeline.

Item 20c, pass 1. Before this pass every project file was a numbered,
space-containing script that could only be ``exec()``'d into a caller's
globals. Files 01, 02 and 03 are now real modules underneath, and the numbered
files survive as shims that re-export them so nothing else had to change.

Module layout and the ONE allowed import direction
--------------------------------------------------
::

    settings   <-- paths <-- config <-- utils
        ^                       ^
        +-----------------------+

    settings   env-var names, path resolution, load_env_keys()
    paths      IS_DOCKER, _glob_one and every path variable
    constants  the two coding-system sentinels (imports nothing)
    config     every tunable, plus LAZY client/keys accessors
    utils      cost, retry, partial dates, exec_chain, CaffeinateSession

Arrows point at what a module may import. There is no arrow back:

* ``config`` must never import ``utils``. That edge is the cycle this pass
  removed — ``02- Utility Functions.py`` read ``PRICING_CONFIG`` and
  ``qdrant_client`` out of File 03 while File 03 called ``load_env_keys()``
  out of File 02. Under ``exec()`` both landed in one namespace and resolved
  at runtime; as modules it is an ``ImportError``. ``load_env_keys`` moving
  into ``settings`` is what broke it.
* ``constants`` imports nothing at all, from anywhere.

Importing any module in this package must stay side-effect free in the ways
that matter: no network client is constructed, no local model is downloaded or
loaded, and no database is opened. ``paths`` does touch the filesystem — it
globs the sibling directories — which is the one deliberate exception, and it
is why the network-free proof imports ``config`` and ``utils`` rather than
asserting about the whole package.

This ``__init__`` deliberately imports NO submodule. ``import oncotriage`` must
stay free; the caller names the module it wants.
"""

__all__ = ["settings", "paths", "constants", "config", "utils"]


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  5 2026

@author: ramyalsaffar
"""
