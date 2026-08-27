"""Department probes for the reflex loop.

Each department is a deterministic oracle (no model) that scans source code
for a specific class of gap and returns findings in the standard gap-JSON
format. All findings flow through the same record_gaps() -> gate -> ESCALATION.md
path as Tier-1 and Tier-2 findings.

Slug convention: department prefix encodes the department.
  arch.gap:security-*     -- Security department
  arch.gap:debt-*         -- Debt/Completeness department
  arch.gap:obs-*          -- Observability department
  arch.gap:arch-*         -- Architecture department
  arch.gap:deps-*         -- Dependency Health department
"""
from __future__ import annotations

from .security_probe import run_security_probes
from .debt_probe import run_debt_probes
from .observability_probe import run_observability_probes
from .architecture_probe import run_architecture_probes
from .dependency_probe import run_dependency_probes
from .runner import run_department_probes

__all__ = [
    "run_security_probes",
    "run_debt_probes",
    "run_observability_probes",
    "run_architecture_probes",
    "run_dependency_probes",
    "run_department_probes",
]
