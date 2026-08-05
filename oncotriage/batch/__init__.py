"""Full-corpus batch execution of the matching pipeline.

Item 20c, pass 3b.

    runner   "25- Batch Runner.py" whole -- the checkpoint and results files,
             the per-patient worker, the two ThreadPoolExecutor passes (main and
             resample), the one-shot mid-run model-change announcer, and the
             summary report.

WHY IT IS ITS OWN SUBPACKAGE and not part of ``oncotriage.api``: both drive the
same pipeline over the same patients, and that is where the similarity ends.
The API answers one request at a time over HTTP and holds no state between them;
this runs 22,000 patients with no HTTP at all, resumes from a checkpoint after a
crash, and deliberately re-runs a seeded subset. Neither imports the other, and
``process_patient`` mirroring ``_run_matching_pipeline`` is a documented
correspondence rather than shared code -- the two differ in error handling, in
what they return, and in what a failure is allowed to do.

THIS IS THE PACKAGE'S FIRST REAL CONCURRENCY TEST. ``MAX_WORKERS`` = 12 threads
go through ``oncotriage.agent.deps`` on every patient, which until pass 20c-3a
had only ever run single-threaded. Pass 3a put the whole override-then-cache
sequence inside the lock for that reason; pass 3b's "47- Package Split Test.py"
drives MAX_WORKERS threads through every accessor and asserts one shared object
and exactly one build per key.

THE THREAD-SAFETY MONKEYPATCH IS GONE. File 25 wrapped ``log_inference`` in a
lock IN ITS OWN NAMESPACE, which protected this file and left
``17- FastAPI Server.py`` -- the project's only other concurrent writer --
unserialized. The lock lives in ``oncotriage/storage/database_logger.py`` now.

This ``__init__`` imports no submodule. ``import oncotriage.batch`` pulls in
neither tqdm nor the agent; the caller names the module it wants.
"""

__all__ = ["runner"]


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
