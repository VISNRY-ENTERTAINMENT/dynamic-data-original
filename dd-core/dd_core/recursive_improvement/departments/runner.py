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
  reachability  -- reachability_probe.run_reachability_probes (exported-never-consumed)
  billing       -- billing_probe.run_billing_probes (billing-path-filtered)
  network       -- network_probe.run_network_probes (bind/publish/debug/IaC exposure)
  contract_drift-- contract_drift_probe.run_contract_drift_probes (external-API drift preconditions)
  state_coverage-- state_coverage_probe.run_state_coverage_probes (partial registry/enum coverage)
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

    # Slugs produced this run, and the SOURCES whose probes completed without
    # error. Auto-close may only touch findings recorded by a source that both
    # (a) ran this cycle and (b) finished cleanly -- a crashed probe contributes
    # zero slugs, which is indistinguishable from "found nothing", and treating
    # crash-as-clean would close every finding of that department as fixed.
    # Findings from OTHER sources (Tier-1 reviewer, Tier-2 auditor, attack
    # probe, manual entries) are NEVER touched by probe auto-close: no probe
    # can attest to their absence.
    current_slugs: set[str] = set()
    completed_sources: set[str] = set()

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
                completed_sources.add("reflex-security")
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
                completed_sources.add("reflex-debt")
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
                completed_sources.add("reflex-obs")
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
                completed_sources.add("reflex-arch")
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
                # Per-commit mode scans ONLY changed manifests: an unchanged
                # manifest's findings are absent from this run without being
                # fixed. Only a FULL scan can attest absence for this source.
                if changed_files is None:
                    completed_sources.add("reflex-deps")
            except Exception as e:
                print(f"[departments] dependency probe error: {e}", file=sys.stderr)

        # ---- Contract / Invariant (existing oracles) ----
        if _enabled(cfg, "contract"):
            try:
                ran_any = _run_contract_dept(cfg, ddb, sha, status_lines, current_slugs)
                # Only attest when at least one manifest existed and was checked:
                # a deleted manifest makes old findings unmeasurable, not fixed.
                if ran_any:
                    completed_sources.add("reflex-contract")
            except Exception as e:
                print(f"[departments] contract probe error: {e}", file=sys.stderr)

        # ---- Reachability (exported-but-never-consumed) ----
        if _enabled(cfg, "reachability"):
            try:
                from dd_core.recursive_improvement.departments.reachability_probe import (
                    run_reachability_probes,
                )
                findings = run_reachability_probes(repo_root)
                new, dup, reop, supp = _record(cfg, ddb, findings, sha, "reflex-reach", current_slugs)
                _run_gate(cfg, ddb, status_lines, "reachability", new, dup, reop, supp)
                completed_sources.add("reflex-reach")
            except Exception as e:
                print(f"[departments] reachability probe error: {e}", file=sys.stderr)

        # ---- Billing (billing-path-filtered enforcement patterns) ----
        if _enabled(cfg, "billing"):
            try:
                from dd_core.recursive_improvement.departments.billing_probe import (
                    run_billing_probes,
                )
                findings = run_billing_probes(repo_root)
                new, dup, reop, supp = _record(cfg, ddb, findings, sha, "reflex-billing", current_slugs)
                _run_gate(cfg, ddb, status_lines, "billing", new, dup, reop, supp)
                completed_sources.add("reflex-billing")
            except Exception as e:
                print(f"[departments] billing probe error: {e}", file=sys.stderr)

        # ---- Network exposure (bind/publish/debug/IaC reachability) ----
        if _enabled(cfg, "network"):
            try:
                from dd_core.recursive_improvement.departments.network_probe import (
                    run_network_probes,
                )
                findings = run_network_probes(repo_root)
                new, dup, reop, supp = _record(cfg, ddb, findings, sha, "reflex-network", current_slugs)
                _run_gate(cfg, ddb, status_lines, "network", new, dup, reop, supp)
                completed_sources.add("reflex-network")
            except Exception as e:
                print(f"[departments] network probe error: {e}", file=sys.stderr)

        # ---- Contract drift (external-API drift preconditions) ----
        if _enabled(cfg, "contract_drift"):
            try:
                from dd_core.recursive_improvement.departments.contract_drift_probe import (
                    run_contract_drift_probes,
                )
                findings = run_contract_drift_probes(repo_root)
                new, dup, reop, supp = _record(cfg, ddb, findings, sha, "reflex-drift", current_slugs)
                _run_gate(cfg, ddb, status_lines, "contract_drift", new, dup, reop, supp)
                completed_sources.add("reflex-drift")
            except Exception as e:
                print(f"[departments] contract_drift probe error: {e}", file=sys.stderr)

        # ---- State coverage (partial registry/enum coverage) ----
        if _enabled(cfg, "state_coverage"):
            try:
                from dd_core.recursive_improvement.departments.state_coverage_probe import (
                    run_state_coverage_probes,
                )
                findings = run_state_coverage_probes(repo_root)
                new, dup, reop, supp = _record(cfg, ddb, findings, sha, "reflex-state", current_slugs)
                _run_gate(cfg, ddb, status_lines, "state_coverage", new, dup, reop, supp)
                completed_sources.add("reflex-state")
            except Exception as e:
                print(f"[departments] state_coverage probe error: {e}", file=sys.stderr)

        # ---- Probe-based auto-close (scoped by source authority) ----
        # Close ONLY findings recorded by a probe source that completed cleanly
        # THIS cycle and whose slug the probe no longer produces. See
        # _autoclose_resolved for why both conditions are load-bearing.
        try:
            closed = _autoclose_resolved(ddb, current_slugs, sha, cfg.floor,
                                         completed_sources)
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


def _opening_source(ddb, subject: str) -> str | None:
    """The source that recorded the LATEST 'open' status claim for a subject."""
    try:
        hist = ddb.history(subject, "status")
    except Exception:
        return None
    opens = [c for c in hist if getattr(c, "value", None) == "open"]
    if not opens:
        return None
    latest = sorted(opens, key=lambda c: (getattr(c, "seq", 0),
                                          getattr(c, "recorded_at", "")))[-1]
    return getattr(latest, "source", None)


def _autoclose_resolved(ddb, current_slugs: set[str], sha: str,
                         floor: float,
                         completed_sources: set[str] | None = None) -> list[str]:
    """Close open arch.gap claims that a completed probe no longer detects.

    TWO conditions, both load-bearing (each guards against a way this
    mechanism could silently destroy real findings):

    1. SOURCE AUTHORITY: the finding's latest 'open' claim must have been
       recorded by a source in `completed_sources` -- a probe that ran THIS
       cycle and finished without error. Model-generated findings (Tier-1
       reviewer, Tier-2 auditor), attack-probe findings, and manual entries
       are never produced by these probes, so their absence from a probe scan
       proves nothing; closing them here would erase the whole model-reviewed
       backlog on the first department run. A crashed probe is excluded the
       same way: it contributes zero slugs, which is indistinguishable from
       "found nothing", and crash-as-clean would close every finding of that
       department as fixed -- health reported but never verified.

    2. ABSENCE: the slug is not in `current_slugs` -- the probe that owns it
       re-scanned and no longer produces it, meaning the code pattern that
       opened it is gone and the fix is already committed.

    `completed_sources=None` (legacy call shape) closes NOTHING -- fail-closed,
    never fail-open.
    """
    if not completed_sources:
        return []
    from dd_core.recursive_improvement.gate import collect_open_gaps
    open_gaps = collect_open_gaps(ddb, floor, prefix="arch.gap:")
    closed = []
    for gap in open_gaps:
        subject = gap["subject"]  # e.g. "arch.gap:security-hardcoded-secret-..."
        slug = subject.removeprefix("arch.gap:")
        if slug in current_slugs:
            continue  # still detected -- stays open
        src = _opening_source(ddb, subject)
        if src not in completed_sources:
            continue  # no probe that ran cleanly this cycle owns this finding
        ddb.assert_claim(
            subject, "status", "fixed",
            source="reflex-dept-autoclose",
            confidence=1.0,
            author_kind="system",
            evidence=(
                f"source {src} re-scanned cleanly at commit {sha[:12]} and no "
                "longer detects this slug; pattern absent -- auto-closed"
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


def _run_contract_dept(cfg, ddb, sha, status_lines,
                       slug_sink: set | None = None) -> bool:
    """Wire the existing invariants.py + contracts.py into the department runner.

    Returns True when at least one manifest existed and was checked -- the
    condition under which this source can attest to a finding's absence.
    Findings recorded here MUST flow into slug_sink like every other
    department's, or auto-close would treat still-detected contract findings
    as resolved (the bug fixed alongside source-scoped auto-close).
    """
    import json
    import os

    ran_any = False

    # invariants
    inv_manifest = os.path.join(cfg.repo_root, "invariants.json")
    if os.path.exists(inv_manifest):
        from dd_core.recursive_improvement.invariants import check_invariants
        findings = check_invariants(cfg.repo_root, inv_manifest)
        new, dup, reop, supp = _record(cfg, ddb, findings, sha, "reflex-contract", slug_sink)
        _run_gate(cfg, ddb, status_lines, "contract-invariants", new, dup, reop, supp)
        ran_any = True

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
        else:
            contracts_manifest = ""
    if contracts_manifest and os.path.exists(contracts_manifest):
        from dd_core.recursive_improvement.contracts import check_contracts
        findings = check_contracts(cfg.repo_root, contracts_manifest)
        new, dup, reop, supp = _record(cfg, ddb, findings, sha, "reflex-contract", slug_sink)
        _run_gate(cfg, ddb, status_lines, "contract-payloads", new, dup, reop, supp)
        ran_any = True

    return ran_any
