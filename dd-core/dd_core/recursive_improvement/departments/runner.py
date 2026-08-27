"""Department probe runner -- orchestrates all deterministic department oracles.

Called from the main reflex runner (recursive_improvement/runner.py) after
autoclose and before Tier-1 model review. Each department oracle is isolated:
one failing never blocks the others. All findings flow through record_gaps()
-> gate -> ESCALATION.md identically to Tier-1 findings.

Department -> oracle mapping:
  security      -- security_probe.run_security_probes (+ attack_pattern_probe)
  debt          -- debt_probe.run_debt_probes
  observability -- observability_probe.run_observability_probes
  architecture  -- architecture_probe.run_architecture_probes (needs manifest)
  dependency    -- dependency_probe.run_dependency_probes (per-commit: changed files only)
  contract      -- existing invariants.py + contracts.py (manifest-driven)
  goal_alignment-- no deterministic oracle; handled by Tier-1 charter lens

goal_alignment has no deterministic oracle: it requires reasoning about intent
vs. diff, which is the Tier-1 model's job. The intent claim (if present in the
store) is injected into the Tier-1 prompt by runner.py so the model can check it.
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dd_core.recursive_improvement.config import ReflexConfig


def _enabled(cfg: "ReflexConfig", dept: str) -> bool:
    """A department is enabled if cfg.departments lists it (or lists 'all'),
    or if cfg.departments is empty (default = all enabled)."""
    depts = cfg.departments if hasattr(cfg, "departments") else ()
    if not depts:
        return True  # empty = all on (backwards-compatible default)
    return dept in depts or "all" in depts


def _record(cfg: "ReflexConfig", ddb, findings: list[dict], sha: str, source: str,
            slug_sink: set | None = None):
    if not findings:
        return 0, 0, 0, []
    if slug_sink is not None:
        for f in findings:
            if f.get("slug"):
                slug_sink.add(f["slug"])
    from dd_core.recursive_improvement.record import record_gaps
    return record_gaps(ddb, findings, sha, source,
                       prefix="arch.gap:", repo_root=cfg.repo_root)


def run_department_probes(cfg: "ReflexConfig", sha: str,
                          changed_files: list[str] | None = None) -> list[str]:
    """Run all enabled deterministic department probes.

    Returns a list of one-line status strings (one per department that ran),
    same format as Tier-1/Tier-2 status lines. Empty list if all disabled or
    all probes raised exceptions.
    """
    # Load DDB once -- shared across all probes in this run
    try:
        if cfg.dd_core_path and cfg.dd_core_path not in sys.path:
            sys.path.insert(0, cfg.dd_core_path)
        from dd_core import DynamicDataStore
        ddb = DynamicDataStore(cfg.abspath(cfg.gap_db))
    except Exception as e:
        print(f"[departments] cannot open store: {e}", file=sys.stderr)
        return []

    status_lines: list[str] = []
    repo_root = cfg.repo_root

    # Collect all slugs produced this run for probe-based auto-close later
    current_slugs: set[str] = set()

    try:
        # ---- Security ----
        if _enabled(cfg, "security"):
            try:
                from dd_core.recursive_improvement.departments.security_probe import (
                    run_security_probes,
                )
                findings = run_security_probes(repo_root)
                new, dup, reop, supp = _record(cfg, ddb, findings, sha, "reflex-security", current_slugs)
                _run_gate(cfg, ddb, status_lines, "security", new, dup, reop, supp)
            except Exception as e:
                print(f"[departments] security probe error: {e}", file=sys.stderr)

        # ---- Debt ----
        if _enabled(cfg, "debt"):
            try:
                from dd_core.recursive_improvement.departments.debt_probe import (
                    run_debt_probes,
                )
                findings = run_debt_probes(repo_root)
                new, dup, reop, supp = _record(cfg, ddb, findings, sha, "reflex-debt", current_slugs)
                _run_gate(cfg, ddb, status_lines, "debt", new, dup, reop, supp)
            except Exception as e:
                print(f"[departments] debt probe error: {e}", file=sys.stderr)

        # ---- Observability ----
        if _enabled(cfg, "observability"):
            try:
                from dd_core.recursive_improvement.departments.observability_probe import (
                    run_observability_probes,
                )
                findings = run_observability_probes(repo_root)
                new, dup, reop, supp = _record(cfg, ddb, findings, sha, "reflex-obs", current_slugs)
                _run_gate(cfg, ddb, status_lines, "observability", new, dup, reop, supp)
            except Exception as e:
                print(f"[departments] observability probe error: {e}", file=sys.stderr)

        # ---- Architecture ----
        if _enabled(cfg, "architecture"):
            try:
                from dd_core.recursive_improvement.departments.architecture_probe import (
                    run_architecture_probes,
                )
                manifest = getattr(cfg, "architecture_rules", "architecture_rules.json")
                manifest_path = cfg.abspath(manifest) if manifest else None
                findings = run_architecture_probes(repo_root, manifest_path)
                new, dup, reop, supp = _record(cfg, ddb, findings, sha, "reflex-arch", current_slugs)
                _run_gate(cfg, ddb, status_lines, "architecture", new, dup, reop, supp)
            except Exception as e:
                print(f"[departments] architecture probe error: {e}", file=sys.stderr)

        # ---- Dependency ----
        if _enabled(cfg, "dependency"):
            try:
                from dd_core.recursive_improvement.departments.dependency_probe import (
                    run_dependency_probes,
                )
                findings = run_dependency_probes(repo_root, changed_files)
                new, dup, reop, supp = _record(cfg, ddb, findings, sha, "reflex-deps", current_slugs)
                _run_gate(cfg, ddb, status_lines, "dependency", new, dup, reop, supp)
            except Exception as e:
                print(f"[departments] dependency probe error: {e}", file=sys.stderr)

        # ---- Contract / Invariant (existing oracles) ----
        if _enabled(cfg, "contract"):
            try:
                _run_contract_dept(cfg, ddb, sha, status_lines)
            except Exception as e:
                print(f"[departments] contract probe error: {e}", file=sys.stderr)

        # ---- Probe-based auto-close ----
        # Any open arch.gap claim whose slug is absent from this run's findings
        # is no longer detectable -- close it automatically.
        try:
            closed = _autoclose_resolved(ddb, current_slugs, sha, cfg.floor)
            if closed:
                status_lines.append(
                    f"[dept:autoclose] probe-resolved={len(closed)} "
                    + " ".join(closed[:5])
                    + ("..." if len(closed) > 5 else "")
                )
        except Exception as e:
            print(f"[departments] autoclose error: {e}", file=sys.stderr)

    finally:
        try:
            ddb.close()
        except Exception:
            pass

    return status_lines


def _autoclose_resolved(ddb, current_slugs: set[str], sha: str,
                         floor: float) -> list[str]:
    """Close any open arch.gap claim whose slug is not in this run's findings.

    The slug is the bare identifier (e.g. 'security-hardcoded-secret-app-api-py-8').
    The full subject is 'arch.gap:<slug>'. A finding is absent when no probe
    produced its slug this run, which means the code pattern that opened it is
    gone -- the fix is already committed.
    """
    from dd_core.recursive_improvement.gate import collect_open_gaps
    open_gaps = collect_open_gaps(ddb, floor, prefix="arch.gap:")
    closed = []
    for gap in open_gaps:
        subject = gap["subject"]  # e.g. "arch.gap:security-hardcoded-secret-..."
        slug = subject.removeprefix("arch.gap:")
        if slug not in current_slugs:
            ddb.assert_claim(
                subject, "status", "fixed",
                source="reflex-dept-autoclose",
                confidence=1.0,
                author_kind="system",
                evidence=(
                    f"probe did not detect slug in scan at commit {sha[:12]}; "
                    "pattern absent from codebase -- auto-closed"
                ),
            )
            closed.append(slug)
    return closed


def _run_gate(cfg, ddb, status_lines, dept_name, new, dup, reop, supp):
    """Run the escalation gate for this department's findings and append status."""
    from dd_core.recursive_improvement import gate as _gate
    counted, escalation = _gate.run_gate(ddb, cfg.threshold, cfg.floor, "arch.gap:")
    line = (f"[dept:{dept_name}] recorded={new} dup={dup} reopened={reop} "
            f"suppressed={len(supp)} open={counted}")
    if escalation:
        from dd_core.recursive_improvement.runner import _write
        _write(cfg, "ESCALATION.md",
               f"# Reflex escalation (dept:{dept_name})\n\n"
               f"```\n{escalation}\n```\n")
        line += " -> ESCALATION.md"
    status_lines.append(line)


def _run_contract_dept(cfg, ddb, sha, status_lines):
    """Wire the existing invariants.py + contracts.py into the department runner."""
    import json
    import os

    # invariants
    inv_manifest = os.path.join(cfg.repo_root, "invariants.json")
    if os.path.exists(inv_manifest):
        from dd_core.recursive_improvement.invariants import check_invariants
        findings = check_invariants(cfg.repo_root, inv_manifest)
        new, dup, reop, supp = _record(cfg, ddb, findings, sha, "reflex-contract")
        _run_gate(cfg, ddb, status_lines, "contract-invariants", new, dup, reop, supp)

    # contracts -- standalone contracts.json, or "contracts" key in invariants.json
    contracts_manifest = os.path.join(cfg.repo_root, "contracts.json")
    if not os.path.exists(contracts_manifest):
        if os.path.exists(inv_manifest):
            try:
                with open(inv_manifest, encoding="utf-8") as fh:
                    data = json.load(fh)
                if "contracts" in data:
                    contracts_manifest = inv_manifest
                else:
                    contracts_manifest = ""
            except Exception:
                contracts_manifest = ""
    if contracts_manifest and os.path.exists(contracts_manifest):
        from dd_core.recursive_improvement.contracts import check_contracts
        findings = check_contracts(cfg.repo_root, contracts_manifest)
        new, dup, reop, supp = _record(cfg, ddb, findings, sha, "reflex-contract")
        _run_gate(cfg, ddb, status_lines, "contract-payloads", new, dup, reop, supp)
