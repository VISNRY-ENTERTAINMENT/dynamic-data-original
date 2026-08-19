#!/usr/bin/env python3
"""Backwards-compatibility shim. The CLI is now `dd_ri.py` (Recursive
Improvement). This forwards to it so old invocations keep working."""
import runpy
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.argv[0] = os.path.join(_here, "dd_ri.py")
runpy.run_path(os.path.join(_here, "dd_ri.py"), run_name="__main__")
