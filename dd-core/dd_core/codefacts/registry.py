"""Extension -> adapter registry. An adapter is a callable
``(abs_path, rel_path, source_text) -> CodeFacts | None``."""

from __future__ import annotations

import os

_ADAPTERS: dict = {}          # ".py" -> adapter callable

# Directories never worth walking. Deliberately does NOT include test dirs --
# some oracles (change-scoped selection) must see tests; those that want to skip
# tests pass extra_skip to iter_facts.
_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
         "build", "vendor", "site-packages", ".idea", ".vscode", "target",
         "bin", "obj"}


def register_adapter(extensions, adapter):
    """Register one adapter for one or more file extensions (with the dot)."""
    for ext in extensions:
        _ADAPTERS[ext.lower()] = adapter


def adapter_for_ext(ext: str):
    return _ADAPTERS.get(ext.lower())


def supported_extensions() -> set:
    return set(_ADAPTERS)


def extract_facts(path: str, repo_root: str | None = None, source: str | None = None):
    """Extract CodeFacts for a file, choosing the adapter by extension.

    Returns None when there is no adapter for the extension or the source cannot
    be read/parsed -- callers treat None as 'skip this file', never as an error.
    """
    ext = os.path.splitext(path)[1]
    adapter = _ADAPTERS.get(ext.lower())
    if adapter is None:
        return None
    if source is None:
        try:
            source = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            return None
    rel = (os.path.relpath(path, repo_root).replace("\\", "/")
           if repo_root else os.path.basename(path))
    try:
        return adapter(path, rel, source)
    except Exception:
        return None


def iter_source_files(repo_root: str, extra_skip=frozenset()):
    """Yield absolute paths of every file with a registered adapter, skipping
    vendored/build dirs (plus any in extra_skip)."""
    skip = _SKIP | set(extra_skip)
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        for f in files:
            if os.path.splitext(f)[1].lower() in _ADAPTERS:
                yield os.path.join(root, f)


def iter_facts(repo_root: str, extra_skip=frozenset()):
    """Yield CodeFacts for every adaptable file under repo_root (unparseable
    files are silently skipped)."""
    for path in iter_source_files(repo_root, extra_skip):
        facts = extract_facts(path, repo_root)
        if facts is not None:
            yield facts
