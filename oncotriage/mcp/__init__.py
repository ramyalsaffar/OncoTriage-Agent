"""The Model Context Protocol server over the matching pipeline.

A sibling of ``oncotriage/api/server.py``: the same pipeline, a second
protocol. The REST API is reached by a program somebody wrote; this is reached
by a model choosing a tool from a description, inside a conversation nobody
here can see. That difference is why the not-for-clinical-use framing rides on
every tool description AND every result rather than living in a README.

THIS PACKAGE IS NAMED ``oncotriage.mcp`` AND THE SDK IS NAMED ``mcp``, AND THAT
IS SAFE RATHER THAN LUCKY. Python 3 resolves ``import mcp`` and
``from mcp.server import MCPServer`` absolutely -- a sibling module never
shadows a top-level one -- so ``oncotriage/mcp/server.py`` reaches the SDK
exactly as any other file would. The one arrangement that WOULD break it is
putting ``oncotriage/`` itself on ``sys.path``, and nothing does: every entry
point's bootstrap inserts the CODE directory, one level up. Verified by running
rather than reasoned about; see the check in
``tests/test_mcp_server_stdio_contract.py``.

THIS FILE IMPORTS NOTHING ON PURPOSE. ``python -m oncotriage.mcp`` imports
``oncotriage/__init__.py`` and then this file BEFORE ``__main__.py`` gets
control, so anything imported here would run outside the stdout guard that
``__main__.py`` exists to hold. ``oncotriage/__init__.py`` is silent -- measured
-- and this file must stay silent too.
"""


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
