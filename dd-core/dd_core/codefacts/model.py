"""The language-neutral fact model every oracle consumes.

An adapter fills these in from source; oracles read them and apply their
(language-neutral) naming heuristics. Fields are named for the CONCEPT, not any
one language's syntax, so a Go or TypeScript adapter populates the same shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FunctionFacts:
    """One function/method in a source file."""
    name: str
    lineno: int
    # Decorator/annotation tokens attached to the function. For `@router.get(...)`
    # this is {"router", "get"}; for a bare `@auth` it is {"auth"}. Languages
    # without decorators leave this empty.
    decorators: set = field(default_factory=set)
    # Every called-name token in the body: the id or final attribute segment of
    # each call (`guard()` -> "guard"; `self.repo.save()` -> "save").
    calls: set = field(default_factory=set)
    # Optional params whose default is the language's null literal (Python None,
    # JS null/undefined, ...), as (name, lineno). Private/underscore params are
    # already excluded by the adapter; dependency-suffix filtering is the
    # oracle's job.
    optional_none_params: list = field(default_factory=list)
    # String-literal key sets of each dict/object literal the function returns
    # (each as a frozenset). Used by the payload-contract oracle.
    returned_dict_keysets: list = field(default_factory=list)


@dataclass
class CodeFacts:
    """All facts an adapter extracts from one source file."""
    path: str                       # absolute path
    rel: str                        # repo-root-relative, forward-slashed
    language: str                   # adapter id, e.g. "python", "javascript"
    imports: set = field(default_factory=set)          # dotted import targets
    functions: list = field(default_factory=list)      # list[FunctionFacts]
    # Annotated fields whose default is null, as (name, lineno). Capability-suffix
    # gating is the oracle's job.
    optional_none_fields: list = field(default_factory=list)
    # Names provided as a non-null keyword/named argument at any call site.
    provided_keywords: set = field(default_factory=set)
    # Attribute-assignment targets given a real (non-null, non bare-forward)
    # value, e.g. the `x` in `self.x = build()`.
    provided_attributes: set = field(default_factory=set)
    # Attribute names read anywhere (the consumed side of wiring).
    consumed_attributes: set = field(default_factory=set)
    # (base_name_or_None, key, lineno) for each `x["key"]` string-literal read.
    subscript_reads: list = field(default_factory=list)
