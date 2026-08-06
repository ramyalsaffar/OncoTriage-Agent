"""Re-export shim over ``oncotriage.settings``.

Item 20a created this file with the content itself. Item 20c moved that content
into the ``oncotriage`` package and left this behind, because files load it BY
FILE LOCATION under exactly this name. As of item 20c pass 3d there is exactly
ONE such caller left:

    01- Imports.py          searches three candidate directories for
                            "oncotriage_settings.py" and exec_module()s it

    28- Select 30 Samples.py  DID THE SAME, beside its own __file__, and does
                            not any more: pass 3d moved its body to
                            ``oncotriage/evaluation/sampling.py``, which imports
                            ``oncotriage.paths`` like every other package
                            module. The by-location load was right when it was
                            written -- File 28 was not in the exec chain and did
                            not want File 01's model and client imports for two
                            database queries -- and became obsolete once
                            ``oncotriage.paths`` cost nothing to import. It also
                            registered a SECOND copy of this module in
                            ``sys.modules`` beside the one the package holds,
                            two ``_RESOLVED`` caches answering one question.

Loading a module by location does not consult sys.path, so this file has to
keep existing at the code directory under this exact name for as long as
``01- Imports.py`` does it that way. Passes 20d-20f are where it stops.

Everything is re-exported EXPLICITLY, by name. A star import would make the
list of what this shim provides depend on ``oncotriage.settings``'s internals,
and the first private helper added there would silently join the public surface
— which is the opposite of what a compatibility shim is for.
"""

# --- Make the oncotriage package importable ---------------------------------
# This file is loaded by LOCATION, which does not consult sys.path, so the
# package next to it is not automatically importable. It usually is anyway --
# `python "28- Select 30 Samples.py"` from the code directory puts that
# directory at sys.path[0], and `pip install -e .` puts it there permanently --
# but "usually" is not a guarantee, and the failure would be an ImportError
# from inside an exec'd file with no indication of which directory was missing.
#
# So: try the import, and only if it fails add this file's own directory and
# say so. The print is not decoration; a package resolved from an unexpected
# place is exactly the kind of thing that must not be silent.
try:  # pragma: no cover - the happy path is the normal one
    import oncotriage as _oncotriage_pkg  # noqa: F401
except ImportError:
    import os as _os
    import sys as _sys
    _here = _os.path.dirname(_os.path.abspath(__file__))
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
        print(f"[Bootstrap] oncotriage was not importable; added {_here} to sys.path")
    import oncotriage as _oncotriage_pkg  # noqa: F401
    del _os, _sys, _here

from oncotriage.settings import (  # noqa: E402,F401
    ENV_MAIN_PATH,
    ENV_CODE_PATH,
    ENV_KEYS_PATH,
    ENV_DATA_TRIAL_PATH,
    FALLBACK_MAIN_PATH,
    _from_env,
    with_trailing_sep,
    require_existing_directory,
    resolve_main_path,
    resolve_code_path,
    resolve_keys_path,
    resolve_data_trial_path,
)

# load_env_keys and REQUIRED_ENV_KEYS moved to oncotriage.paths in pass 20c-2a,
# beside the keys_path they default to. Re-exported here anyway: this shim's
# contract is the set of names the pre-package oncotriage_settings.py exposed,
# and dropping two of them because the package rearranged itself would break a
# caller that loaded this file by location and never asked about the package.
#
# Imported from oncotriage.paths, not re-declared. Importing this shim therefore
# resolves the directory tree, which it already did indirectly through every
# caller that loads it.
from oncotriage.paths import (  # noqa: E402,F401
    REQUIRED_ENV_KEYS,
    load_env_keys,
)

__all__ = [
    "ENV_MAIN_PATH",
    "ENV_CODE_PATH",
    "ENV_KEYS_PATH",
    "ENV_DATA_TRIAL_PATH",
    "FALLBACK_MAIN_PATH",
    "REQUIRED_ENV_KEYS",
    "with_trailing_sep",
    "require_existing_directory",
    "resolve_main_path",
    "resolve_code_path",
    "resolve_keys_path",
    "resolve_data_trial_path",
    "load_env_keys",
]


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  4 2026
"""
