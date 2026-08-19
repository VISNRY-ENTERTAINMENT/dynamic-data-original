"""dd_core.recursive_improvement -- a reusable self-improvement loop on Dynamic
Data. (Formerly `dd_core.reflex`; that name is kept as a compatibility shim.)

After a project ships a commit, a model reviews it (Tier 1: the diff) and, every
N major commits, audits the whole codebase against its roadmap + north star
(Tier 2). Findings are recorded as append-only claims in a Dynamic Data ledger;
DETERMINISTIC machinery then dedups, validates evidence, triages by severity,
auto-closes on fix, and reports metrics. The model is ONLY in the discovery
step -- it proposes; it never decides what is recorded, hidden, escalated, or
closed.

See ../../RECURSIVE_IMPROVEMENT.md (front door) and
../../04_RECURSIVE_IMPROVEMENT.md (architecture). Point it at any project's .ddb
via a reflex.config.json and wire one hook line.

    from dd_core.recursive_improvement import ReflexConfig, run_post_commit
"""

from .config import ReflexConfig
from .runner import (
    run_post_commit, run_tier1, run_tier2, maybe_run_tier2, run_autoclose,
)
from .autoclose import autoclose_from_commit, subjects_closed_by_message
from .evidence import validate_evidence
from . import probes, metrics, learn

__all__ = [
    "ReflexConfig",
    "run_post_commit",
    "run_tier1",
    "run_tier2",
    "maybe_run_tier2",
    "run_autoclose",
    "autoclose_from_commit",
    "subjects_closed_by_message",
    "validate_evidence",
    "probes",
    "metrics",
    "learn",
]
