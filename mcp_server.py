# MCP Server entry point
########################

"""Start the Model Context Protocol server on stdio.

    python mcp_server.py

THIN ENTRY POINT. Every definition lives in ``oncotriage/mcp/server.py``; the
three tools it exposes and what they wrap are documented there. What is here is
a ``__main__`` guard and ONE THING THAT COULD NOT LIVE IN THE PACKAGE: a
file-descriptor-level stdout guard wrapped around the package import itself.


WHY THIS FILE IS NOT NUMBERED, and not ``python -m oncotriage.mcp``
------------------------------------------------------------------
It follows ``fixture_capture.py`` / ``fixture_replay.py``: top level, no number.
The numbered sequence says what you can run IN PIPELINE ORDER, and this is not a
pipeline stage -- it is a second protocol over the same pipeline, started by a
client rather than by a person, and it would have to claim a number in the
30-49 range that ``tests/FILE NUMBER MAPPING.md`` has already spent.

It is also a filename an MCP client config has to contain. Every numbered file
in this project has a SPACE and a LEADING DIGIT in its name, which a JSON config
block survives only with quoting nobody gets right the first time.

``python -m oncotriage.mcp`` WAS BUILT FIRST AND WAS WITHDRAWN, and the reason is
worth recording because the mechanism looked fine. It needed
``oncotriage/mcp/__main__.py`` to import ``oncotriage.mcp.server`` from INSIDE a
function -- the guard below has to wrap that import -- and that is exactly what
``tests/test_package_invariants.py`` check 1b forbids: no ``oncotriage`` module
may import another ``oncotriage`` module from a function body, because a
deferred import is a dependency no scan of an import block can see. The check
caught it, correctly. A top-level script is not a package module, so the
deferred import is the ordinary entry-point shape here -- CLAUDE.md's own
instruction for adding a script is "leave a ``__main__`` block that imports what
it calls" -- and no invariant has to be weakened to get the guard.


THE GUARD, AND THE WINDOW IT CLOSES
-----------------------------------
Over stdio the client parses this process's stdout as JSON-RPC, one message per
line. One stray byte ends the session. This project writes to stdout during
import, measured rather than assumed:

    $ python -c "import oncotriage.fhir.parser" 2>/dev/null
    [Paths] Settings module loaded from .../oncotriage/settings.py (via the ...)

That is ``oncotriage/paths.py`` line 121, at module scope; lines 318 and 323 add
``Running on local machine`` and ``[Paths] Project root: ...`` when a path first
resolves. THE SIX-LINE PACKAGE BOOTSTRAP BELOW PRINTS TOO, on the branch where
``oncotriage`` is not installed and this directory has to go on ``sys.path`` --
which is the branch a first-time user is on.

mcp 2.0.0's ``stdio_server()`` protects the SERVING window on its own:
``_claim_fd(1, ...)`` in ``mcp/server/stdio.py`` duplicates the real stdout to a
private descriptor and points fd 1 at stderr, so a ``print`` from inside a tool
misses the wire. But that claim is made when the transport starts, and every
module-scope ``print`` in the dependency graph has already run by then. Two
windows, two guards; this is the first one.

FILE-DESCRIPTOR LEVEL, not ``contextlib.redirect_stdout``. A Python-level
redirect rebinds ``sys.stdout`` and is invisible to anything writing to fd 1
directly -- a C extension, a subprocess, a library holding ``sys.__stdout__``.
``dup2`` covers every writer at once.

IT IS RELEASED BEFORE THE SERVER STARTS, which looks like undoing it and is not.
``stdio_server()`` claims fd 1 by duplicating whatever it points at and serving
the protocol on that duplicate. If fd 1 still pointed at stderr, THE PROTOCOL
WOULD BE WRITTEN TO STDERR and the client would wait forever for a process that
is answering into the void. The restore is the handover from this guard to the
SDK's.

THE FLUSH IS LOAD-BEARING AND IS THE EASY THING TO GET WRONG. ``sys.stdout`` is
a ``TextIOWrapper`` over a buffer on fd 1, block-buffered when stdout is a pipe,
which is what a client gives it. Text printed during the import window sits in
that buffer; still there when fd 1 is restored, it would be flushed onto the
WIRE afterwards -- the guard would have moved the corruption later rather than
prevented it. So the flush happens inside the window, and ``line_buffering`` is
turned on first so that later prints leave no residue for interpreter exit.

``tests/test_mcp_server_stdio_contract.py`` section 8c is the negative control:
it runs the same server with this guard bypassed and requires stdout to be
corrupted. If it ever stops being corrupted, section 8b is passing for free.
"""

import os
import sys
from collections import Counter


#------------------------------------------------------------------------------


GUARD_FAILURES = Counter()
"""Times the stdout guard could not do its job, keyed ``{stage}:{ExceptionType}``.

The project's standing rule is that no exception is caught without being
re-raised or recorded. Two of these cannot be re-raised -- failing to duplicate
or reconfigure a descriptor must not stop the server from starting -- so they
are counted and printed. The one that CAN NOT be survived, a failed restore, is
recorded and then re-raised. Read by ``_report_guard_failures()``.
"""


def _stderr(message):
    """Diagnostics go to stderr. This file must never write to stdout."""
    print(f"[oncotriage-mcp] {message}", file=sys.stderr, flush=True)


def _report_guard_failures():
    """Read ``GUARD_FAILURES`` and say so on stderr. Silence means it was clean."""
    if GUARD_FAILURES:
        _stderr(f"WARNING: the stdout guard was degraded: {dict(GUARD_FAILURES)}. "
                f"Output written during import may have reached the protocol "
                f"stream; if the client reports a parse error, this is why.")


def _bootstrap_and_import():
    """Put the package on ``sys.path`` if needed, then return its ``main``.

    THE SIX-LINE BOOTSTRAP IS THE ONE EVERY ENTRY POINT CARRIES -- import the
    package, falling back to this directory then the working directory, and
    PRINT that it did. `pip install -e .` makes it a no-op. It is inside this
    function, and this function is called inside the guard, precisely because
    that print is on stdout.
    """
    try:
        import oncotriage  # noqa: F401
    except ImportError:
        for _candidate, _how in (
            (os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals()
             else None, "__file__"),
            (os.getcwd(), "cwd"),
        ):
            if _candidate and os.path.isdir(os.path.join(_candidate, "oncotriage")):
                if _candidate not in sys.path:
                    sys.path.insert(0, _candidate)
                print(f"[Bootstrap] oncotriage package found at {_candidate} "
                      f"(via {_how}); added to sys.path")
                break
        else:
            raise
        del _candidate, _how

    from oncotriage.mcp.server import main
    return main


def _import_under_guard():
    """Run ``_bootstrap_and_import()`` with fd 1 pointed at stderr.

    Best effort in the same sense the SDK's own ``_claim_fd`` is: if a
    descriptor cannot be duplicated, proceed WITHOUT the fd-level guard rather
    than refuse to start -- a server that will not run is worse than one whose
    banner might land on the wire, and the failure is recorded either way.
    """
    saved_fd = None
    try:
        saved_fd = os.dup(1)
    except OSError as exc:
        GUARD_FAILURES[f"dup:{type(exc).__name__}"] += 1

    if saved_fd is not None:
        try:
            os.dup2(2, 1)
        except OSError as exc:
            GUARD_FAILURES[f"divert:{type(exc).__name__}"] += 1
            os.close(saved_fd)
            saved_fd = None

    try:
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except (AttributeError, OSError, ValueError) as exc:
            GUARD_FAILURES[f"reconfigure:{type(exc).__name__}"] += 1
        return _bootstrap_and_import()
    finally:
        try:
            sys.stdout.flush()
        except (OSError, ValueError) as exc:
            GUARD_FAILURES[f"flush:{type(exc).__name__}"] += 1
        if saved_fd is not None:
            try:
                os.dup2(saved_fd, 1)
            except OSError as exc:
                # Fatal, and the only one here that is. The transport would
                # serve the protocol on stderr and the client would hang with no
                # diagnosis, so this is recorded AND re-raised.
                GUARD_FAILURES[f"restore:{type(exc).__name__}"] += 1
                _stderr("FATAL: could not restore stdout; the protocol stream "
                        "would have been written to stderr.")
                raise
            finally:
                os.close(saved_fd)


#------------------------------------------------------------------------------


# ===========================================================================
# COMMAND-LINE EXECUTION
# ===========================================================================

if __name__ == "__main__":
    """
    Serve the three MCP tools on stdio until the client disconnects.

    Usage:
        python mcp_server.py

    It reads JSON-RPC from stdin and writes JSON-RPC to stdout, so running it by
    hand in a terminal looks like a hang. That is correct behaviour: it is
    waiting for a client. See the client config block in the module docstring of
    oncotriage/mcp/server.py and in CLAUDE.md.
    """
    _server_main = _import_under_guard()
    _report_guard_failures()
    _server_main()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
