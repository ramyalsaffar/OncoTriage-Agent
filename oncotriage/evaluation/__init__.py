"""Offline measurement of the corpus and of what the pipeline recorded about it.

Item 20c, pass 3d.

    sampling      "28- Select 30 Samples.py" whole -- the seeded, stratified
                  30-patient draw out of inferences.db into a second database.

    cohort_diff   "34- Cohort Selector Diff.py" whole -- runs the LEGACY and the
                  CURRENT cohort selector over every bundle on disk and records
                  where they disagree. READ ONLY: it never deletes, moves or
                  rewrites a bundle.

WHY THESE TWO ARE TOGETHER, AND WHY NOT IN ``storage`` AND ``fhir``.
Each of them would fit an existing package on the shape of its dependencies --
``sampling`` touches nothing but sqlite3 and ``paths``, ``cohort_diff`` touches
nothing but ``fhir`` and ``registries``. Both are grouped here instead because
of what they are FOR rather than what they import: they are hand-run
measurements that produce a report, and neither is on any serving path.
``oncotriage.storage`` is imported by ``api.server`` and ``batch.runner``, and
``oncotriage.fhir`` by the agent; adding a report generator to either would put
an interactive tool inside a package a production import walks through.

That argument is about legibility, not about correctness -- Python imports
modules and not packages, and both ``__init__.py`` files here are empty of code
-- so the alternative placement is recorded rather than dismissed.

Neither module writes anywhere the pipeline reads. ``sampling`` opens the
production inferences.db and issues no statement against it; ``cohort_diff``
opens patient bundles read-only.
"""


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
