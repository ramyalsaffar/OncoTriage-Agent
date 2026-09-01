"""Offline measurement of the corpus and of what the pipeline recorded about it.

Item 20c, pass 3d.

    sampling      "28- Select Evaluation Sample.py" whole -- the seeded, stratified
                  30-patient draw out of inferences.db into a second database.

    cohort        WHICH patients a campaign runs, and which of them the k=2
                  stability re-run and the judge pass are taken over. Pure: it
                  reads a list of paths from its caller and returns a subset,
                  resolving no path and opening no file. It is the ONLY module
                  that reads the ruled programme's three sizes and three seeds,
                  which is what keeps oncotriage/batch/runner.py cohort-blind.

                  IT IS NOT ON A SERVING PATH AND IT IS IMPORTED BY ONE, which
                  is the one place the paragraph below is stretched: the batch
                  runner imports it. That is deliberate -- the alternative is
                  the runner owning the draw, which puts three programme
                  constants inside the loop that spends the money -- and it
                  costs nothing, because this module imports only
                  ``oncotriage.config``.

    cohort_diff   "34- Cohort Selector Diff Read Only.py" whole -- runs the LEGACY and the
                  CURRENT cohort selector over every bundle on disk and records
                  where they disagree. READ ONLY: it never deletes, moves or
                  rewrites a bundle.

WHY THESE ARE TOGETHER, AND WHY NOT IN ``storage`` AND ``fhir``.
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
