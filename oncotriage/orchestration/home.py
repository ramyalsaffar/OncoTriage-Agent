# Airflow Home
##############

"""The one place ``AIRFLOW_HOME`` is resolved.

Item 20c, pass 3c-2.

WHY THIS IS A MODULE AND NOT THREE COPIES OF THREE LINES
---------------------------------------------------------
All three orchestration modules need the same thing: "the Airflow home, which is
``oncotriage.paths.airflow_path`` unless the caller named one". Written out at
each site it is three lines each, and the first draft of this pass did write it
out three times. That is the same shape as the BM25 sparse model before pass
20c-3a -- one job, several independent construction sites, and nothing that
fails when they disagree.

What disagreement would cost here is small but real and entirely silent:
``airflow_setup`` migrating a database under one AIRFLOW_HOME while
``airflow_manager`` starts a scheduler under another produces two working
processes pointed at two different metadata databases, and the symptom is a DAG
that never appears in the UI. No exception, no counter, no log line saying the
two disagreed.

So there is one function, here, and the three modules import it.

WHY IT IS ITS OWN MODULE rather than living in ``orchestration/__init__.py``:
the ``__init__`` deliberately imports no submodule, so that ``import
oncotriage.orchestration`` costs nothing. A helper defined there would be fine
to import, but it would put resolvable behaviour in a file whose stated contract
is that it holds none.

WHAT IMPORTING THIS MODULE DOES: imports ``oncotriage.paths``. It resolves no
path -- ``paths`` is imported as a MODULE, never ``from oncotriage.paths import
airflow_path``, because a ``from X import name`` is an attribute read and would
fire the lazy resolver at import time.
"""

from oncotriage import paths


#------------------------------------------------------------------------------


def resolve_airflow_home(airflow_home=None):
    """Return the AIRFLOW_HOME to operate on, resolving the default lazily.

    Args:
        airflow_home: An explicit directory, or None for
            ``oncotriage.paths.airflow_path``.

    Returns:
        The directory, as whatever the caller passed or as the resolved string.

    Passing a directory resolves NOTHING: the glob only runs on the None branch.
    That is what lets a test point the orchestration modules at a scratch
    AIRFLOW_HOME on a machine with no sibling data tree at all.
    """
    if airflow_home is None:
        return paths.airflow_path
    return airflow_home


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
