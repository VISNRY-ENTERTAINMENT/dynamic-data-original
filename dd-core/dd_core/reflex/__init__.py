"""Backwards-compatibility shim. The reflex loop is now named **Recursive
Improvement** (dd_core.recursive_improvement). This package re-exports it so the
old dd_core.reflex.* import paths keep working. Prefer the new name in new code.
"""
from dd_core.recursive_improvement import *  # noqa: F401,F403
