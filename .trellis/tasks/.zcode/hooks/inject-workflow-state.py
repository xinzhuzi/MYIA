#!/usr/bin/env python3
"""Temporary shim: old hook config resolves this path when the shell cwd
drifts to .trellis/tasks. Walk up to the real project root and exec the
actual hook script there. Remove once the session reloads .zcode/config.json."""
import os
import sys

here = os.path.abspath(__file__)
name = os.path.basename(here)
d = os.path.dirname(os.path.dirname(here))  # .trellis/tasks
while d != "/":
    cand = os.path.join(d, ".zcode", "hooks", name)
    if os.path.isfile(cand) and os.path.abspath(cand) != here:
        os.execv(sys.executable, [sys.executable, cand] + sys.argv[1:])
    d = os.path.dirname(d)
sys.exit(0)
