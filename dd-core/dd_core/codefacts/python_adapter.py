"""Python adapter: stdlib ``ast`` -> CodeFacts. Zero dependencies.

This is the reference adapter; it reproduces exactly the facts the oracles
extracted before the codefacts seam existed, so the refactor is behaviour-
preserving. All the naming heuristics (dependency suffixes, capability suffixes)
stay in the oracles -- this only turns Python syntax into facts.
"""

from __future__ import annotations

import ast

from .model import CodeFacts, FunctionFacts
from .registry import register_adapter

_EXTENSIONS = (".py", ".pyi")


def _is_none(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _callee_name(call: ast.Call):
    f = call.func
    return getattr(f, "id", None) or getattr(f, "attr", None)


def _is_field_default_none(val) -> bool:
    """`field(default=None)` / `Field(default=None)`."""
    return (isinstance(val, ast.Call)
            and (getattr(val.func, "id", None) or getattr(val.func, "attr", "")) in ("field", "Field")
            and any(kw.arg == "default" and _is_none(kw.value) for kw in val.keywords))


def _decorator_tokens(func) -> set:
    names = set()
    for dec in func.decorator_list:
        node = dec.func if isinstance(dec, ast.Call) else dec
        while isinstance(node, ast.Attribute):
            names.add(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            names.add(node.id)
    return names


def _literal_str_keys(dict_node: ast.Dict) -> frozenset:
    return frozenset(k.value for k in dict_node.keys
                     if isinstance(k, ast.Constant) and isinstance(k.value, str))


def _returned_keysets(func) -> list:
    local_dicts = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    local_dicts[tgt.id] = _literal_str_keys(node.value)
    out = []
    for node in ast.walk(func):
        if isinstance(node, ast.Return) and node.value is not None:
            if isinstance(node.value, ast.Dict):
                out.append(_literal_str_keys(node.value))
            elif isinstance(node.value, ast.Name) and node.value.id in local_dicts:
                out.append(local_dicts[node.value.id])
    return out


def _optional_none_params(func) -> list:
    a = func.args
    pos = a.args[len(a.args) - len(a.defaults):] if a.defaults else []
    out = []
    for arg, default in list(zip(pos, a.defaults)) + list(zip(a.kwonlyargs, a.kw_defaults)):
        if arg.arg.startswith("_"):
            continue
        if _is_none(default):
            out.append((arg.arg, arg.lineno))
    return out


def _function_facts(func) -> FunctionFacts:
    calls = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            calls.add(_callee_name(node))
    calls.discard(None)
    return FunctionFacts(
        name=func.name, lineno=func.lineno,
        decorators=_decorator_tokens(func), calls=calls,
        optional_none_params=_optional_none_params(func),
        returned_dict_keysets=_returned_keysets(func),
    )


def parse(path: str, rel: str, source: str) -> CodeFacts:
    tree = ast.parse(source)
    facts = CodeFacts(path=path, rel=rel, language="python")

    for node in ast.walk(tree):
        # imports
        if isinstance(node, ast.Import):
            for n in node.names:
                facts.imports.add(n.name)
        elif isinstance(node, ast.ImportFrom):
            if not node.level and node.module:
                facts.imports.add(node.module)
                for n in node.names:
                    facts.imports.add(f"{node.module}.{n.name}")
        # optional null-default fields (capability candidates)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None and (_is_none(node.value) or _is_field_default_none(node.value)):
                facts.optional_none_fields.append((node.target.id, node.lineno))
        # providers: non-null keyword args
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg and not _is_none(kw.value):
                    facts.provided_keywords.add(kw.arg)
        # providers: attribute assignment with a real value (not null, not bare fwd)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute):
                    v = node.value
                    if not _is_none(v) and not (isinstance(v, ast.Name) and v.id == tgt.attr):
                        facts.provided_attributes.add(tgt.attr)
        # consumers: attribute reads
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            facts.consumed_attributes.add(node.attr)
        # consumers: string-literal subscript reads
        if isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                base = node.value.id if isinstance(node.value, ast.Name) else None
                facts.subscript_reads.append((base, sl.value, node.lineno))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facts.functions.append(_function_facts(node))

    return facts


def register():
    register_adapter(_EXTENSIONS, parse)
