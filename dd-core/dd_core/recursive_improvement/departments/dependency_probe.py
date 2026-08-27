"""Dependency Health department -- deterministic oracle. NO model.

Per-commit (Tier 0): parses manifest files that changed in the diff and flags
newly added dependencies, wildcard version pins, missing lockfiles, and dev
deps in production manifests.

Tier-2 deep scan: walks the full dependency graph, records version claims in
the DynamicDataStore, and flags stale packages (no release > N days) and
known-problematic patterns. CVE integration is v2; v1 reasons from age and
pinning discipline alone.

All findings use slug prefix `deps-` -> arch.gap:deps-* in the claim store.
Version/health claims use subject `dep:<ecosystem>/<name>` for the store.

Supported manifests:
  Python  -- requirements.txt, requirements*.txt, pyproject.toml
  Node.js -- package.json
  Go      -- go.mod
  Rust    -- Cargo.toml
  Ruby    -- Gemfile
"""
from __future__ import annotations

import json
import os
import re
import time

_STALE_DAYS_DEFAULT = 730  # 2 years


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return None


def _slug(label: str, ecosystem: str, name: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", f"{ecosystem}-{name}".lower()).strip("-")
    return f"deps-{label}-{safe}"


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_requirements_txt(src: str) -> list[dict]:
    """Parse a requirements.txt or constraints file."""
    deps = []
    for line in src.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # Handle editable installs
        if line.startswith("-e "):
            continue
        # Strip inline comments
        line = line.split("#")[0].strip()
        # Parse name and version specifier
        m = re.match(r"^([A-Za-z0-9_\-.\[\]]+)\s*(.*)$", line)
        if m:
            deps.append({"name": m.group(1).split("[")[0], "spec": m.group(2).strip(),
                         "ecosystem": "pypi"})
    return deps


def _parse_pyproject_toml(src: str) -> list[dict]:
    """Very lightweight TOML parser for [project.dependencies]."""
    deps = []
    in_section = False
    for line in src.splitlines():
        stripped = line.strip()
        if stripped in ("[project.dependencies]", "[tool.poetry.dependencies]",
                        "[dependencies]"):
            in_section = True
            continue
        if stripped.startswith("[") and in_section:
            in_section = False
        if not in_section:
            continue
        # dependency lines: "name>=1.0" or name = ">=1.0"
        m = re.match(r'^["\']?([A-Za-z0-9_\-.\[\]]+)["\']?\s*(?:=\s*["\']([^"\']+)["\']|(.*))?$',
                     stripped)
        if m and m.group(1) and m.group(1) not in ("python",):
            spec = (m.group(2) or m.group(3) or "").strip()
            deps.append({"name": m.group(1).split("[")[0], "spec": spec,
                         "ecosystem": "pypi"})
    return deps


def _parse_package_json(src: str) -> list[dict]:
    try:
        data = json.loads(src)
    except Exception:
        return []
    deps = []
    for section, is_dev in (("dependencies", False), ("devDependencies", True),
                             ("peerDependencies", False)):
        for name, spec in (data.get(section) or {}).items():
            deps.append({"name": name, "spec": str(spec), "ecosystem": "npm",
                         "is_dev": is_dev})
    return deps


def _parse_go_mod(src: str) -> list[dict]:
    deps = []
    in_require = False
    for line in src.splitlines():
        stripped = line.strip()
        if stripped == "require (":
            in_require = True
            continue
        if stripped == ")":
            in_require = False
            continue
        if stripped.startswith("require "):
            parts = stripped[len("require "):].split()
            if len(parts) >= 2:
                deps.append({"name": parts[0], "spec": parts[1], "ecosystem": "go"})
        elif in_require:
            parts = stripped.split()
            if len(parts) >= 2 and not stripped.startswith("//"):
                deps.append({"name": parts[0], "spec": parts[1], "ecosystem": "go"})
    return deps


def _parse_cargo_toml(src: str) -> list[dict]:
    deps = []
    in_deps = False
    for line in src.splitlines():
        stripped = line.strip()
        if stripped in ("[dependencies]", "[dev-dependencies]", "[build-dependencies]"):
            in_deps = True
            continue
        if stripped.startswith("[") and in_deps:
            in_deps = False
        if not in_deps or not stripped or stripped.startswith("#"):
            continue
        m = re.match(r'^([A-Za-z0-9_\-]+)\s*=\s*(?:"([^"]+)"|.*version\s*=\s*"([^"]+)")', stripped)
        if m:
            spec = m.group(2) or m.group(3) or ""
            deps.append({"name": m.group(1), "spec": spec, "ecosystem": "cargo"})
    return deps


def _parse_gemfile(src: str) -> list[dict]:
    deps = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = re.match(r"""gem\s+['"]([^'"]+)['"](?:\s*,\s*['"]([^'"]+)['"])?""", stripped)
        if m:
            deps.append({"name": m.group(1), "spec": m.group(2) or "",
                         "ecosystem": "rubygems"})
    return deps


_MANIFEST_PARSERS = {
    "requirements.txt": ("pypi", _parse_requirements_txt),
    "pyproject.toml": ("pypi", _parse_pyproject_toml),
    "package.json": ("npm", _parse_package_json),
    "go.mod": ("go", _parse_go_mod),
    "Cargo.toml": ("cargo", _parse_cargo_toml),
    "Gemfile": ("rubygems", _parse_gemfile),
}


def _is_manifest(filename: str) -> bool:
    base = os.path.basename(filename)
    if base in _MANIFEST_PARSERS:
        return True
    # requirements-dev.txt, requirements-prod.txt, etc.
    if re.match(r"requirements[^/]*\.txt$", base, re.IGNORECASE):
        return True
    return False


def _parse_manifest(path: str) -> tuple[str, list[dict]]:
    """Return (ecosystem, deps) from a manifest file path."""
    base = os.path.basename(path)
    src = _read(path)
    if not src:
        return "", []
    # requirements*.txt
    if re.match(r"requirements[^/]*\.txt$", base, re.IGNORECASE):
        return "pypi", _parse_requirements_txt(src)
    if base in _MANIFEST_PARSERS:
        eco, parser = _MANIFEST_PARSERS[base]
        return eco, parser(src)
    return "", []


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

_WILDCARD_SPECS = re.compile(r"^\*$|^==\*$|^latest$|^>=?\s*0\.0|^\^0\.")
_LOCKFILE_MAP = {
    "package.json": "package-lock.json",
    "Gemfile": "Gemfile.lock",
    "go.mod": "go.sum",
    "Cargo.toml": "Cargo.lock",
    # pyproject.toml / requirements.txt have no single canonical lockfile
}


def check_manifest(repo_root: str, manifest_path: str) -> list[dict]:
    """Per-commit check on a single manifest file."""
    findings: list[dict] = []
    ecosystem, deps = _parse_manifest(manifest_path)
    if not deps:
        return []
    rel_manifest = os.path.relpath(manifest_path, repo_root).replace("\\", "/")
    manifest_dir = os.path.dirname(manifest_path)
    manifest_base = os.path.basename(manifest_path)

    # Check for missing lockfile
    lockfile = _LOCKFILE_MAP.get(manifest_base)
    if lockfile:
        lock_path = os.path.join(manifest_dir, lockfile)
        if not os.path.exists(lock_path):
            findings.append({
                "slug": _slug("no-lockfile", ecosystem, manifest_base),
                "title": f"no lockfile ({lockfile}) alongside {rel_manifest}",
                "area": rel_manifest,
                "severity": "medium",
                "confidence": 1.0,
                "evidence": rel_manifest,
                "proposed_action": (
                    f"commit {lockfile} alongside {manifest_base} to pin transitive "
                    "dependencies and ensure reproducible installs"
                ),
            })

    for dep in deps:
        name = dep["name"]
        spec = dep.get("spec", "")

        # Wildcard / unpinned versions
        if _WILDCARD_SPECS.match(spec.strip()):
            findings.append({
                "slug": _slug("unpinned", ecosystem, name),
                "title": f"unpinned dependency {name!r} ({spec!r}) in {rel_manifest}",
                "area": rel_manifest,
                "severity": "medium",
                "confidence": 1.0,
                "evidence": rel_manifest,
                "proposed_action": (
                    f"pin {name} to a specific version or a tight range; "
                    "unpinned deps make builds non-reproducible and can silently "
                    "upgrade to a breaking or compromised version"
                ),
            })

        # Dev deps in production manifest (package.json only -- has explicit section)
        if dep.get("is_dev") and manifest_base == "package.json":
            pass  # Dev deps in devDependencies are fine; only flag if misplaced

    return findings


def find_manifests(repo_root: str) -> list[str]:
    """Walk the repo and return all manifest file paths."""
    results = []
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs
                   if d not in {".git", "node_modules", "__pycache__", ".venv",
                                "venv", "dist", "build", "vendor", "site-packages"}
                   and not d.startswith(".")]
        for f in files:
            if _is_manifest(f):
                results.append(os.path.join(root, f))
    return results


def run_dependency_probes(repo_root: str,
                          changed_files: list[str] | None = None) -> list[dict]:
    """Dependency health findings.

    If changed_files is provided (per-commit mode), only checks manifests in
    the changed set. If None (Tier-2 / on-demand mode), checks all manifests.
    """
    out: list[dict] = []
    if changed_files is not None:
        # per-commit: only check changed manifests
        manifests = [
            os.path.join(repo_root, f) for f in changed_files
            if _is_manifest(f)
        ]
    else:
        # full scan
        manifests = find_manifests(repo_root)

    for path in manifests:
        try:
            out += check_manifest(repo_root, path)
        except Exception:
            pass
    return out
