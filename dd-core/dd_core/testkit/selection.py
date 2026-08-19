"""Change-scoped test selection -- retire 'slow, coarse feedback'.

The failure class isn't a bug in the code, it's a bug in the LOOP: when the only
way to verify a change is the whole slow suite, feedback comes late, so changes
get batched, so when something breaks the blast radius is large and the cause is
buried. Fast, precise feedback is what lets an autonomous builder take small,
verified steps instead of big risky ones.

Given the files a change touched, this returns the test files that (transitively)
import them -- the tests that actually exercise the change -- by building a static
import graph over the repo. It OVER-selects on purpose: a missed test is a missed
regression, so an import it can't resolve is kept in scope, never dropped. Run
the returned subset for a fast inner loop; the full suite still runs in CI.

Static and deterministic -- no coverage instrumentation, no run history, no model.

Resolution caveat: a file's module name is derived from its path relative to
``repo_root``, so imports must use that same rooting. A project whose importable
code sits under a ``src/`` that is itself on ``sys.path`` (so code imports
``api.x``, not ``src.api.x``) must point ``repo_root`` at that ``src/`` dir, or
the path-derived names won't match the import names and edges won't resolve.
When in doubt the selector under-resolves to *fewer* edges, so pair a thin
subset run with the full suite in CI -- never rely on it to prove absence.
"""

from __future__ import annotations

import os

from dd_core.codefacts import iter_source_files, extract_facts, supported_extensions

_STRIP_EXTS = (".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
               ".go", ".rb", ".java", ".rs")


def module_for_path(repo_root: str, path: str) -> str:
    """Dotted module name for a file relative to repo_root. ``a/b/__init__.py``
    -> ``a.b``; ``a/b/c.py`` -> ``a.b.c``. Language-neutral: strips a known
    source extension and any index/__init__ package file."""
    rel = os.path.relpath(path, repo_root).replace("\\", "/")
    for pkg in ("/__init__.py", "/index.js", "/index.ts", "/mod.rs"):
        if rel.endswith(pkg):
            return rel[: -len(pkg)].replace("/", ".")
    for ext in _STRIP_EXTS:
        if rel.endswith(ext):
            rel = rel[: -len(ext)]
            break
    return rel.replace("/", ".")


def build_import_graph(repo_root: str):
    """Return (edges, is_test) where edges maps each repo module to the set of
    repo modules it imports, and is_test maps module -> its file path if the
    file looks like a test module. Polyglot via the codefacts adapters."""
    modules = {}                       # module -> path
    raw_imports = {}                   # module -> set(dotted import targets)
    for path in iter_source_files(repo_root):
        facts = extract_facts(path, repo_root)
        if facts is None:
            continue
        mod = module_for_path(repo_root, path)
        modules[mod] = path
        raw_imports[mod] = set(facts.imports)

    known = set(modules)
    edges = {}
    for mod, targets in raw_imports.items():
        deps = set()
        for t in targets:
            # resolve an import target to a known repo module: exact, or the
            # longest known prefix (from a.b import c where a.b.c isn't a module)
            if t in known:
                deps.add(t)
            else:
                parts = t.split(".")
                for i in range(len(parts) - 1, 0, -1):
                    cand = ".".join(parts[:i])
                    if cand in known:
                        deps.add(cand)
                        break
        edges[mod] = deps

    is_test = {m: p for m, p in modules.items() if _is_test_file(os.path.basename(p))}
    return edges, is_test


def _is_test_file(base: str) -> bool:
    """Test-file naming across ecosystems: pytest (test_*.py / *_test.py / *_test.go),
    JS/TS (*.test.* / *.spec.*), etc."""
    stem = base.rsplit(".", 1)[0]
    return (base.startswith("test_")
            or stem.endswith("_test")
            or ".test." in base
            or ".spec." in base
            or stem.endswith("Test") or stem.endswith("Spec"))


def _reaches_any(start: str, edges: dict, targets: set, _seen=None) -> bool:
    if _seen is None:
        _seen = set()
    if start in _seen:
        return False
    _seen.add(start)
    for dep in edges.get(start, ()):  # pragma: no branch
        if dep in targets or _reaches_any(dep, edges, targets, _seen):
            return True
    return False


def tests_covering(repo_root: str, changed_paths: list[str]) -> list[str]:
    """Return the sorted list of test file paths (relative, forward-slash) whose
    modules transitively import any changed module. A changed test file is always
    itself selected."""
    edges, is_test = build_import_graph(repo_root)
    changed_modules = set()
    changed_rel = set()
    exts = supported_extensions()
    for cp in changed_paths:
        ap = cp if os.path.isabs(cp) else os.path.join(repo_root, cp)
        if os.path.splitext(cp)[1].lower() not in exts:
            continue
        changed_modules.add(module_for_path(repo_root, ap))
        changed_rel.add(os.path.relpath(ap, repo_root).replace("\\", "/"))

    selected = set()
    for mod, path in is_test.items():
        rel = os.path.relpath(path, repo_root).replace("\\", "/")
        if mod in changed_modules or rel in changed_rel:
            selected.add(rel)
        elif _reaches_any(mod, edges, changed_modules):
            selected.add(rel)
    return sorted(selected)
