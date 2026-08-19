"""dd_core.codefacts -- the language-neutral seam that makes the oracles
polyglot.

Every deterministic analyzer needs the SAME small set of structural facts about
a source file: what it imports, its functions (with their decorators, the calls
in their body, their optional dependency params, the dict-literal keys they
return), which capability fields it declares, and which names are provided
(passed as keyword / assigned) vs consumed (read). Those facts are
language-neutral CONCEPTS; only their EXTRACTION is language-specific.

So the oracles are written once against ``CodeFacts``, and a per-language
*adapter* produces ``CodeFacts`` from source. The Python adapter uses the stdlib
``ast`` (zero dependencies). The optional tree-sitter adapter produces the same
``CodeFacts`` for ~40 other languages (install ``dd-core[polyglot]``). Adding a
language is writing one adapter, never touching an oracle.

    from dd_core.codefacts import extract_facts
    facts = extract_facts(path)          # -> CodeFacts | None (None: unparseable
                                         #    or no adapter for the extension)

The naming heuristics that decide whether a param 'looks like a dependency' or a
field 'looks like a capability' are deliberately NOT here -- they are plain
string checks the oracle applies, identical across languages. The adapter's job
is purely: turn syntax into facts.
"""

from __future__ import annotations

from .model import CodeFacts, FunctionFacts
from .registry import (
    extract_facts, register_adapter, adapter_for_ext, supported_extensions,
    iter_facts, iter_source_files,
)

# Register the stdlib Python adapter on import (always available, no deps).
from . import python_adapter as _python_adapter  # noqa: E402
_python_adapter.register()

# The polyglot adapter self-registers IFF its optional dependency is present.
try:  # pragma: no cover - exercised only when the extra is installed
    from . import treesitter_adapter as _ts_adapter
    _ts_adapter.register()
except Exception:  # ImportError (extra not installed) or grammar load failure
    pass

__all__ = [
    "CodeFacts", "FunctionFacts", "extract_facts", "register_adapter",
    "adapter_for_ext", "supported_extensions", "iter_facts", "iter_source_files",
]
