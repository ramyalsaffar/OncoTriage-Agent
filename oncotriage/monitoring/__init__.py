"""Monitoring the pipeline's inputs and outputs over time.

Item 20c, pass 3b.

    drift   "20- Drift Detection.py" whole -- the three statistical tests
            (KS, PSI, z-score), the one threshold alert that deliberately does
            NOT compare against a baseline (ecog_unavailable_rate), the four
            detectors built on them, the drift_metrics writer and the report.

WHY IT IS ITS OWN SUBPACKAGE and not part of ``oncotriage.storage``: storage
answers "write this row" and "give me these rows". This answers "has the thing
those rows describe changed", which is a different question with its own
statistics and its own thresholds. It READS a database the storage layer writes
and appends to one table of it; it does not use the logger, and it does not
import it -- ``resolve_drift_db_path`` is deliberately a separate resolver from
``resolve_inference_db_path`` for that reason.

TWO THINGS ABOUT File 20 WORTH KNOWING BEFORE READING drift.py
--------------------------------------------------------------
It contained ZERO import statements, so it resolved only inside a namespace
somebody else had filled -- which meant the command in its own ``__main__``
docstring, ``python "20- Drift Detection.py"``, could never work. It does now.

Its two database calls read a bare ``inferences_path`` global, and
"41- ECOG Availability Metric Test.py" REBOUND that global to keep its
round-trip test off the production database. That was the last writer in the
repository whose isolation rested on rebinding a shared global; every function
here takes ``db_path``.

``20- Drift Detection.py`` survives as a full re-export shim, because File 41
exec-chains it.

This ``__init__`` imports no submodule, so ``import oncotriage.monitoring``
pulls in neither scipy nor pandas. The caller names the module it wants.
"""

__all__ = ["drift"]


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
