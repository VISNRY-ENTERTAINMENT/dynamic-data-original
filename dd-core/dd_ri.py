#!/usr/bin/env python3
"""dd-reflex — command-line control for the reflex recursive-improvement loop.

    python dd_reflex.py init --repo-root .                # scaffold + wire hook (auto-detects)
    python dd_reflex.py doctor --config reflex.config.json # PROVE it would actually fire
    python dd_reflex.py gate --config reflex.config.json             # deterministic escalation check
    python dd_reflex.py run  --config reflex.config.json [--sha X]   # run the loop once (both tiers)
    python dd_reflex.py audit --config reflex.config.json            # force a Tier-2 audit now
    python dd_reflex.py status --config reflex.config.json           # list open gaps/findings

`init` writes a starter reflex.config.json, copies the charter templates next to
it for you to customize, and appends the post-commit hook block (idempotent).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dd_core.recursive_improvement.config import ReflexConfig  # noqa: E402
from dd_core.recursive_improvement import runner as R  # noqa: E402
from dd_core.recursive_improvement import gate as G  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_CHARTERS = os.path.join(_HERE, "dd_core", "recursive_improvement", "charters")
_HOOK_MARKER = "# Reflex loop (dd_core.recursive_improvement) -- post-ship recursive improvement"


def _hook_block(config_path):
    return (
        f'\n{_HOOK_MARKER}\n'
        f'# Backgrounded + fail-soft; skips non-substantive ships internally.\n'
        f'# Disable with REFLEX_DISABLE=1.\n'
        f'if [ "$REFLEX_DISABLE" != "1" ]; then\n'
        f'  ( python "{_HERE}/dd_reflex_hook.py" --config "{config_path}" '
        f'>/dev/null 2>&1 & ) || true\n'
        f'fi\n'
    )


_CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb",
             ".cs", ".cpp", ".c", ".h", ".kt", ".swift", ".php", ".scala", ".ex"}
_SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", "vendor",
              ".venv", "venv", ".idea", ".vscode", "site-packages", "reflex"}


def _detect_code_dirs(repo: str) -> list[str]:
    """Top-level dirs that actually contain code, for THIS repo's layout.

    Exists because a hardcoded ("src/","tests/","lib/","app/") default silently
    matched nothing on a repo laid out as engine/ domains/ judge/ -- the loop
    never fired and looked exactly like "no gaps found".
    """
    found = []
    try:
        for entry in sorted(os.listdir(repo)):
            d = os.path.join(repo, entry)
            if not os.path.isdir(d) or entry.startswith(".") or entry in _SKIP_DIRS:
                continue
            for root, dirs, files in os.walk(d):
                dirs[:] = [x for x in dirs if x not in _SKIP_DIRS
                           and not x.startswith(".")]
                if any(os.path.splitext(f)[1] in _CODE_EXT for f in files):
                    found.append(entry + "/")
                    break
    except Exception:
        pass
    return found


def _find_existing_ddb(repo: str) -> str | None:
    """Reuse the project's EXISTING ledger instead of fragmenting memory.

    A fresh --gap-db pointed at a brand-new file split findings away from a
    .ddb that already held verified claims. Look in the repo and its parent.
    """
    candidates = []
    for base in (repo, os.path.dirname(os.path.abspath(repo))):
        try:
            for f in os.listdir(base):
                if f.endswith(".ddb"):
                    candidates.append(os.path.join(base, f))
        except Exception:
            pass
    if not candidates:
        return None
    # biggest = most likely the real, already-populated project memory
    return max(candidates, key=lambda p: os.path.getsize(p))


def cmd_doctor(args):
    """Prove the config would ACTUALLY fire on this repo.

    The loop's worst failure is silence: a config that matches nothing produces
    the same output as a clean codebase. This makes that state loud.
    """
    cfg = ReflexConfig.load(args.config)
    repo = os.path.abspath(cfg.repo_root)
    problems, notes = [], []

    # 1. would any tracked file be considered substantive?
    tracked = R._git(repo, ["ls-files"]).splitlines()
    if not tracked:
        problems.append("git ls-files returned nothing -- is repo_root correct?")
    matched = [f for f in tracked if cfg.is_substantive_path(f)]
    if tracked and not matched:
        problems.append(
            f"NO tracked file matches substantive_prefixes="
            f"{list(cfg.substantive_prefixes)!r}. The loop would never fire and "
            f"would look exactly like 'no gaps found'. Detected code dirs here: "
            f"{_detect_code_dirs(repo) or '(none)'}. Fix substantive_prefixes, "
            f"or set it to [] for the repo-agnostic default.")
    else:
        notes.append(f"substantive files matched: {len(matched)}/{len(tracked)}")

    # 2. is the ledger real / shared?
    db = cfg.abspath(cfg.gap_db)
    if os.path.exists(db):
        notes.append(f"ledger: {db} ({os.path.getsize(db)} bytes)")
    else:
        existing = _find_existing_ddb(repo)
        (problems if existing else notes).append(
            f"ledger {db} does not exist yet"
            + (f" -- but {existing} does. Point gap_db at it instead of "
               f"fragmenting memory into a second ledger." if existing else
               " (will be created on first finding)"))

    # 3. is the AI actually callable?
    cli = cfg.resolved_cli()
    if not cli:
        problems.append(f"provider={cfg.provider!r} has no cli configured")
    elif not shutil.which(cli):
        problems.append(f"CLI {cli!r} is not on PATH -- discovery will no-op")
    else:
        notes.append(f"cli: {cli} (tier1={cfg.resolved_review_model()}, "
                     f"tier2={cfg.resolved_audit_model()})")

    # 4. charters customized?
    for name, path in (("reviewer", cfg.reviewer_charter_path()),
                       ("auditor", cfg.auditor_charter_path())):
        if "template" in os.path.basename(path):
            notes.append(f"{name} charter: packaged TEMPLATE (generic default; "
                         f"project-specific rules would sharpen findings)")
        else:
            try:
                if "[CUSTOMIZE]" in open(path, encoding="utf-8").read():
                    notes.append(f"{name} charter: copied but still has "
                                 f"[CUSTOMIZE] placeholders")
                else:
                    notes.append(f"{name} charter: customized")
            except Exception:
                problems.append(f"{name} charter unreadable: {path}")

    # 5. is the post-commit hook actually WIRED? WOULD FIRE previously meant
    #    "would fire if invoked" -- but nothing invokes the loop unless the
    #    hook block exists. Found the hard way (2026-08-29): a repo passed
    #    doctor, yet a Closes-tagged commit never reached autoclose because
    #    init --wire-hook had never been run there. The verdict must not
    #    claim more reachability than it observed.
    hook_path = os.path.join(repo, ".git", "hooks", "post-commit")
    try:
        hook_text = open(hook_path, encoding="utf-8").read() \
            if os.path.exists(hook_path) else ""
    except Exception:
        hook_text = ""
    if _HOOK_MARKER in hook_text:
        notes.append("hook: post-commit WIRED (reflex block present)")
    else:
        problems.append(
            "hook: post-commit NOT WIRED -- the loop only runs when a session "
            "invokes it by hand; commits (and their Closes tags) never reach "
            "autoclose. Run `dd_ri.py init --repo-root .` to wire it.")

    print("=" * 68)
    print(f"reflex doctor — {repo}")
    print("=" * 68)
    for n in notes:
        print(f"  ok   {n}")
    for p_ in problems:
        print(f"  FAIL {p_}")
    print("=" * 68)
    print("VERDICT:", "WOULD FIRE" if not problems else "WOULD NOT WORK")
    return 1 if problems else 0


def cmd_init(args):
    repo = os.path.abspath(args.repo_root)
    cfg_path = os.path.join(repo, "reflex.config.json")
    reflex_dir = os.path.join(repo, "reflex")
    os.makedirs(reflex_dir, exist_ok=True)

    # copy charter templates for the user to customize
    for name, dest in (("reviewer_charter.template.md", "reviewer_charter.md"),
                       ("auditor_charter.template.md", "auditor_charter.md")):
        d = os.path.join(reflex_dir, dest)
        if not os.path.exists(d):
            shutil.copyfile(os.path.join(_CHARTERS, name), d)

    if not os.path.exists(cfg_path):
        # Reuse this project's EXISTING ledger rather than starting a second one
        # and fragmenting its memory. Only fall back to the flag/default if the
        # project genuinely has no .ddb yet.
        gap_db = args.gap_db
        existing = _find_existing_ddb(repo)
        if existing and not args.gap_db_explicit:
            gap_db = os.path.relpath(existing, repo).replace("\\", "/")
            print(f"[reflex] reusing existing ledger {existing} "
                  f"(keeps findings with the project's other claims; pass "
                  f"--gap-db to override)")

        detected = _detect_code_dirs(repo)
        cfg = ReflexConfig(
            repo_root=".", gap_db=gap_db,
            # Left EMPTY on purpose = repo-agnostic (any changed file that
            # isn't obviously non-code). The detected dirs are reported below
            # so you can narrow it deliberately -- but an empty value can never
            # silently match nothing, which is the failure that matters.
            substantive_prefixes=(),
            reviewer_charter="reflex/reviewer_charter.md",
            auditor_charter="reflex/auditor_charter.md",
            north_star_anchors=tuple(args.anchors or []),
            ignored_prefixes=("reflex/",),
            dd_core_path=_HERE,
        )
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(cfg.to_dict(), fh, indent=2)
        print(f"[reflex] wrote {cfg_path}")
        print(f"[reflex] detected code dirs: {detected or '(none found)'}")
        print("[reflex] substantive_prefixes left EMPTY = reviews any code-ish "
              "change (works on any layout). Narrow it only if you mean to.")
    else:
        print(f"[reflex] {cfg_path} already exists (left as-is)")

    if args.wire_hook:
        hp = os.path.join(repo, ".git", "hooks", "post-commit")
        existing = ""
        if os.path.exists(hp):
            with open(hp, encoding="utf-8") as fh:
                existing = fh.read()
        if _HOOK_MARKER in existing:
            print("[reflex] post-commit hook already wired.")
        else:
            with open(hp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write((existing or "#!/bin/sh\n").rstrip("\n") + "\n"
                         + _hook_block(cfg_path))
            os.chmod(hp, os.stat(hp).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            print(f"[reflex] wired post-commit hook at {hp}")
    print("[reflex] init done. Customize reflex/*_charter.md and reflex.config.json.")
    return 0


def cmd_gate(args):
    cfg = ReflexConfig.load(args.config)
    ddb = R._load_store(cfg)
    try:
        counted, esc = G.run_gate(ddb, cfg.threshold, cfg.floor, cfg.gap_prefix)
    finally:
        ddb.close()
    print(esc if esc else f"[reflex] {counted} open gap(s) "
          f"(threshold {cfg.threshold}); no escalation.")
    return 0


def cmd_backlog(args):
    """The triaged view: what to act on now vs what is parked. This is the
    'managed backlog, not a firehose' surface -- run it any time."""
    cfg = ReflexConfig.load(args.config)
    ddb = R._load_store(cfg)
    try:
        for label, prefix in (("gaps (Tier 1)", cfg.gap_prefix),
                               ("audit (Tier 2)", cfg.audit_prefix)):
            t = G.triage(ddb, cfg.floor, prefix)
            if not t["all"]:
                continue
            print(f"== {label}: {len(t['act_now'])} MAJOR/act-now, "
                  f"{len(t['backlog'])} MEDIUM/should-do, "
                  f"{len(t.get('recommended', []))} RECOMMENDED/optional ==")
            for g in t["act_now"]:
                print(f"  ACT  [{(g['severity'] or '?').upper():8}] {g['title']}")
                print(f"       {g['subject']}")
            for g in t["backlog"]:
                print(f"  SHLD [{(g['severity'] or '?').upper():8}] {g['title']}")
                print(f"       {g['subject']}")
            for g in t.get("recommended", []):
                print(f"  opt  [{(g['severity'] or 'low').upper():8}] {g['title']}")
                print(f"       {g['subject']}")
    finally:
        ddb.close()
    return 0


def cmd_autoclose(args):
    """Manually run the commit-message auto-closer (normally automatic on every
    commit). Marks findings 'Closes arch.X:slug' in the given/HEAD commit."""
    cfg = ReflexConfig.load(args.config)
    sha = args.sha or R._git(cfg.repo_root, ["rev-parse", "HEAD"]).strip()
    closed = R.run_autoclose(cfg, sha)
    print(f"[reflex] auto-closed {len(closed)}: " + ", ".join(closed)
          if closed else "[reflex] nothing to auto-close in that commit.")
    return 0


def cmd_run(args):
    cfg = ReflexConfig.load(args.config)
    return R.run_post_commit(cfg, args.sha)


def cmd_audit(args):
    cfg = ReflexConfig.load(args.config)
    sha = args.sha or R._git(cfg.repo_root, ["rev-parse", "HEAD"]).strip()
    out = R.run_tier2(cfg, sha)
    print(out or "[reflex] audit produced no output (CLI unavailable?)")
    return 0


def cmd_status(args):
    cfg = ReflexConfig.load(args.config)
    ddb = R._load_store(cfg)
    try:
        for prefix in (cfg.gap_prefix, cfg.audit_prefix):
            for s in sorted(x for x in ddb.subjects() if x.startswith(prefix)):
                h = sorted(ddb.history(s, "status"),
                           key=lambda c: (getattr(c, "seq", 0), getattr(c, "recorded_at", "")))
                print(f"  {(h[-1].value if h else '?'):10} {s}")
    finally:
        ddb.close()
    return 0


def cmd_metrics(args):
    """Is the loop worth running? Precision, false-positive rate, MTTC, backlog
    -- computed deterministically from the ledger's own dispositions."""
    from dd_core.recursive_improvement import metrics as M
    cfg = ReflexConfig.load(args.config)
    ddb = R._load_store(cfg)
    try:
        m = M.compute(ddb, (cfg.gap_prefix, cfg.audit_prefix))
    finally:
        ddb.close()
    print(M.render(m))
    return 0


def cmd_probe(args):
    """Run the deterministic structural probes (no model) and print findings --
    e.g. optional params that are built but never wired."""
    from dd_core.recursive_improvement import probes as P
    cfg = ReflexConfig.load(args.config)
    found = P.run_all_probes(cfg.repo_root)
    if not found:
        print("[reflex] probes found nothing.")
        return 0
    for g in found:
        print(f"  [{g['severity'].upper()}] {g['title'][:120]}")
        print(f"       evidence: {g['evidence']}")
    print(f"[reflex] {len(found)} probe finding(s) "
          f"(deterministic hints; keyword-passing only, so a positionally-injected dep looks unwired -- scan, do not trust blindly).")
    return 0


def cmd_wiring(args):
    """Wiring Prover: capabilities that are DECLARED (an injected dep param or an
    Optional capability field) and CONSUMED (read) but never PROVIDED (no caller
    passes them, no builder assigns them) -- the 'built but not wired' bug that a
    single-diff review is blind to. Deterministic, no model. With --record the
    findings flow into the ledger (dedup -> triage -> self-calibration)."""
    from dd_core.recursive_improvement import runner as R
    from dd_core.recursive_improvement import wiring as W
    from dd_core.recursive_improvement.record import record_gaps
    cfg = ReflexConfig.load(args.config)
    found = W.unwired_capabilities(cfg.repo_root)
    if not found:
        print("[wiring] no unwired capabilities found -- every declared+consumed "
              "capability has a provider.")
        return 0
    for g in found:
        print(f"  [{g['severity'].upper()}] {g['title'][:130]}")
        print(f"       {g['evidence']}  ::  {g['proposed_action'][:90]}")
    if getattr(args, "record", False):
        ddb = R._load_store(cfg)
        try:
            new, dup, reop, supp = record_gaps(
                ddb, found, sha="wiring", source="reflex-wiring",
                prefix=cfg.audit_prefix,
                dedup_prefixes=(cfg.gap_prefix, cfg.audit_prefix),
                repo_root=cfg.repo_root)
            print(f"[wiring] recorded={new} dup={dup} reopened={reop} into the ledger")
        finally:
            ddb.close()
    print(f"[wiring] {len(found)} unwired capability finding(s).")
    return 0


def _changed_files(args, cfg):
    """Explicit --changed wins; else the working-tree diff against HEAD."""
    if getattr(args, "changed", None):
        return list(args.changed)
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-C", cfg.repo_root, "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, check=True).stdout
        return [ln.strip() for ln in out.splitlines() if ln.strip().endswith(".py")]
    except Exception:
        return []


def cmd_select(args):
    """Change-scoped test selection: print the test files that (transitively)
    import the changed files -- the fast inner-loop subset to run."""
    from dd_core.testkit import selection
    cfg = ReflexConfig.load(args.config)
    changed = _changed_files(args, cfg)
    if not changed:
        print("[select] no changed .py files (pass --changed a.py b.py, or make a change).")
        return 0
    tests = selection.tests_covering(cfg.repo_root, changed)
    if not tests:
        print(f"[select] no tests statically cover {changed} -- run the full suite.")
        return 0
    print(f"[select] {len(tests)} covering test file(s) for {len(changed)} changed file(s):")
    for t in tests:
        print(f"  {t}")
    print("pytest " + " ".join(tests))
    return 0


def cmd_blast(args):
    """Consequence Preview: show the downstream blast radius of the changed
    files, and flag high-fan-in changes with no covering test in the change."""
    from dd_core.recursive_improvement import consequence
    cfg = ReflexConfig.load(args.config)
    changed = _changed_files(args, cfg)
    if not changed:
        print("[blast] no changed .py files (pass --changed a.py b.py).")
        return 0
    radius = consequence.blast_radius(cfg.repo_root, changed)
    for mod, info in sorted(radius.items(), key=lambda kv: -kv[1]["fan_in"]):
        print(f"  {mod}: {info['fan_in']} downstream module(s)")
        for d in info["dependents"][:8]:
            print(f"       -> {d}")
    findings = consequence.preview(cfg.repo_root, changed)
    if findings:
        print(f"[blast] {len(findings)} high-fan-in change(s) without a covering test in the diff:")
        for f in findings:
            print(f"  [{f['severity'].upper()}] {f['title'][:130]}")
    else:
        print("[blast] no high-fan-in untested changes.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="dd-ri", description="Recursive Improvement -- an AI checks its own "
        "work against a repo, deterministically, on Dynamic Data.")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init")
    i.add_argument("--repo-root", default=".")
    i.add_argument("--gap-db", default="")
    i.add_argument("--anchors", nargs="*", default=[])
    i.add_argument("--wire-hook", action="store_true", default=True)
    i.set_defaults(fn=cmd_init)

    for name, fn in (("gate", cmd_gate), ("run", cmd_run),
                     ("audit", cmd_audit), ("status", cmd_status),
                     ("doctor", cmd_doctor), ("backlog", cmd_backlog),
                     ("autoclose", cmd_autoclose), ("metrics", cmd_metrics),
                     ("probe", cmd_probe), ("wiring", cmd_wiring),
                     ("select", cmd_select), ("blast", cmd_blast)):
        sp = sub.add_parser(name)
        sp.add_argument("--config", default=None)
        if name in ("run", "audit", "autoclose"):
            sp.add_argument("--sha", default=None)
        if name == "wiring":
            sp.add_argument("--record", action="store_true",
                            help="record findings into the ledger (dedup+triage)")
        if name in ("select", "blast"):
            sp.add_argument("--changed", nargs="*", default=None,
                            help="changed files (default: git diff --name-only HEAD)")
        sp.set_defaults(fn=fn)

    args = p.parse_args(argv)
    if getattr(args, "cmd", None) == "init":
        args.gap_db_explicit = bool(args.gap_db)
        args.gap_db = args.gap_db or "../reflex.ddb"
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
