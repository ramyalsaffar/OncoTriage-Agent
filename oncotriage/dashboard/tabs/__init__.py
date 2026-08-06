"""
One module per dashboard tab (pass 20c-3c-1).

Each module holds exactly one ``render_*_tab`` function, sliced verbatim out of
"21- Streamlit Dashboard.py". The split follows the file's own tab structure,
which is also the order ``oncotriage.dashboard.app.main`` renders them in.

``reproducibility.py`` is 1,400+ lines because it is ONE function; the tab
boundary is the finest cut available without restructuring it, which is a
redesign and not part of this relocation.

Like ``oncotriage.dashboard``, this ``__init__`` imports nothing.
"""


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
