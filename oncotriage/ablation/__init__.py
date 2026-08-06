"""The ablation study and its analysis.

Item 20c, pass 3d.

    study      "26- Ablation Study.py" whole -- the seven configurations, the
               stratified sample, the checkpoint, the two locks, the thread
               pool that runs one config at a time, and the writer for
               ablation_results.db.

    analysis   "27- Ablation Analysis.py" whole -- the comparison table with
               bootstrap CIs, the BH-FDR-corrected Wilcoxon family, the
               minimum-detectable-effect calculation, nine figures and the two
               reports. It READS ablation_results.db and never writes it.

WHY THIS IS ITS OWN SUBPACKAGE. The two modules are one artifact seen twice:
``study`` defines the schema of ``ablation_results.db`` and ``analysis`` is the
only reader of it, so the pair share a contract that nothing else in the project
participates in. Splitting them across ``batch`` (which runs the pipeline over a
corpus) and ``storage`` (which owns inferences.db) would put the two halves of
that contract in packages whose other members have nothing to do with it.

``analysis`` imports ``matplotlib`` at module scope, the second module in the
package allowed to after ``oncotriage.fhir.explore`` and for the same reason:
nine of its functions draw. ``study`` imports neither it nor anything that does.

Neither module is imported by the pipeline, the API, the dashboard or the batch
runner. The dependency runs one way -- ``study`` reaches into ``agent`` to
invoke the graph it is measuring, and nothing reaches back.
"""


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
