"""Polyglot adapter: tree-sitter -> CodeFacts. OPTIONAL dependency.

This is what makes the oracles genuinely language-agnostic: the same CodeFacts
the Python adapter builds from stdlib ``ast``, this builds from tree-sitter's
real grammars for ~14 other languages. Install with
``pip install dd-core[polyglot]``; without it, this module doesn't register and
Python-only analysis continues unaffected.

Design: a single generic extractor (`_extract`) driven by a per-language
``LangSpec`` that names the grammar's node types for the handful of constructs
the oracles need. Adding a language is adding one ``LangSpec`` (and, rarely, a
node type to a shared set) -- never touching an oracle or the extractor.

Per-language fact coverage (graceful: a fact a grammar doesn't expose is simply
empty, never wrong):

  * imports, functions, calls, member/field reads, subscript reads -> ALL
    languages. These power change-scoped Selection, Consequence Preview,
    Invariant Manifests, and the consumed/call side of the Wiring Prover.
  * returned object/map keys (Contracts producers) -> js/ts, ruby, go.
  * optional-null params + provided attributes (Wiring declared/provided side)
    -> js/ts (Python has full support via its own stdlib adapter).

So Selection / Consequence / Invariants are fully polyglot; Wiring and Contracts
are fullest on Python + JS/TS and partial elsewhere -- documented, not hidden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tree_sitter_language_pack import get_parser

from .model import CodeFacts, FunctionFacts
from .registry import register_adapter

_NULLISH = {"null", "nil", "None", "undefined", "NULL", "nullptr"}
_NAME_TYPES = {"identifier", "field_identifier", "property_identifier",
               "simple_identifier", "type_identifier", "constant", "name"}
_STRING_TYPES = {"string", "interpreted_string_literal", "string_literal",
                 "raw_string_literal", "string_content", "encapsed_string"}
_IMPORT_NODES = {"import_declaration", "import_spec", "use_declaration",
                 "using_directive", "import_statement", "import_header",
                 "namespace_use_declaration", "preproc_include",
                 "package_import", "import", "require_expression",
                 "include_expression"}
# call-callee names that mean "import this string" (ruby/php/node style)
_REQUIRE_NAMES = {"require", "require_relative", "require_once", "load",
                  "import", "include", "include_once"}
_DOTTED_TYPES = {"scoped_identifier", "qualified_name", "dotted_name",
                 "scoped_type_identifier", "identifier", "namespace_name"}

_parsers: dict = {}


@dataclass
class LangSpec:
    lang: str
    exts: tuple
    call_types: frozenset
    func_types: frozenset
    member_types: frozenset = frozenset()
    subscript_types: frozenset = frozenset()
    object_types: frozenset = frozenset()
    callee_fields: tuple = ("function", "name", "method", "callee")
    param_container_types: frozenset = frozenset()


# --- small tree helpers --------------------------------------------------------
def _parser(lang: str):
    if lang not in _parsers:
        _parsers[lang] = get_parser(lang)
    return _parsers[lang]


def _text(node) -> str:
    return node.text.decode("utf-8", "ignore")


def _line(node) -> int:
    return node.start_point[0] + 1


def _walk(node):
    yield node
    for c in node.children:
        yield from _walk(c)


def _rightmost_name(node):
    """The trailing name token of a possibly-qualified reference: `a.b.c` -> 'c',
    `a::b` -> 'b', `x.Field` -> 'Field', `guard` -> 'guard'. Works across every
    grammar's member/selector/scoped/navigation node because it just takes the
    last name token in source order."""
    if node is None:
        return None
    res = None
    for n in _walk(node):
        if n.type in _NAME_TYPES:
            res = _text(n)
    return res


def _string_value(node):
    if node is None:
        return None
    if node.type in _STRING_TYPES:
        for c in node.children:
            if c.type in ("string_fragment", "string_content"):
                return _text(c)
        return _text(node).strip("'\"`<>")
    return None


def _first_string(node):
    for n in _walk(node):
        v = _string_value(n)
        if v is not None:
            return v
    return None


def _normalize_import(target: str) -> str:
    t = target.strip().strip("'\"`<>")
    t = t.replace("::", ".").replace("/", ".").replace("\\", ".")
    if t.endswith((".h", ".hpp", ".py", ".rb", ".go")):
        t = t.rsplit(".", 1)[0]
    return t.strip(".")


# --- generic extraction --------------------------------------------------------
def _callee_token(spec: LangSpec, call):
    callee = None
    for f in spec.callee_fields:
        callee = call.child_by_field_name(f)
        if callee is not None:
            break
    if callee is None:
        # first named child that isn't the argument list
        for c in call.children:
            if c.is_named and "argument" not in c.type:
                callee = c
                break
    return _rightmost_name(callee)


def _function_name(node) -> str:
    name = node.child_by_field_name("name")
    if name is not None:
        return _rightmost_name(name) or "<anon>"
    decl = node.child_by_field_name("declarator")
    if decl is not None:
        rn = _rightmost_name(decl)
        if rn:
            return rn
    for c in node.children:
        if c.type in _NAME_TYPES:
            return _text(c)
    return "<anon>"


_DOTTED_RE = re.compile(r"[A-Za-z_$][\w$]*(?:(?:\.|::|/)[A-Za-z_$][\w$]*)+")


def _collect_imports(root, facts: CodeFacts):
    for n in _walk(root):
        if n.type not in _IMPORT_NODES:
            continue
        added = False
        s = _first_string(n)
        if s:
            facts.imports.add(_normalize_import(s))
            added = True
        for c in _walk(n):
            if c.type in _DOTTED_TYPES:
                txt = _text(c)
                if txt and ("." in txt or "::" in txt or c.type != "identifier"):
                    facts.imports.add(_normalize_import(txt))
                    added = True
                    break
        if not added:
            # flat `import a . b . c` (scala) or any grammar that spreads the
            # path across sibling tokens: recover it from the node text.
            m = _DOTTED_RE.search(_text(n))
            if m:
                facts.imports.add(_normalize_import(m.group(0)))


def _object_keys(spec: LangSpec, obj_node) -> frozenset:
    keys = set()
    for c in obj_node.children:
        if c.type in ("pair", "keyed_element", "hash_key_symbol", "element"):
            k = c.child_by_field_name("key") or (c.children[0] if c.children else None)
            if k is None:
                continue
            if k.type in _NAME_TYPES:
                keys.add(_text(k).rstrip(":"))
            else:
                sv = _string_value(k)
                if sv is not None:
                    keys.add(sv)
    return frozenset(keys)


def _returned_keysets(spec: LangSpec, fn_node) -> list:
    if not spec.object_types:
        return []
    out = []
    for n in _walk(fn_node):
        if n.type == "return_statement" or (spec.lang == "ruby" and n.type in spec.object_types):
            targets = ([n] if n.type in spec.object_types
                       else [c for c in n.children if c.type in spec.object_types])
            for obj in targets:
                out.append(_object_keys(spec, obj))
    # ruby: an implicit last-expression hash is also a return; catch top-level ones
    return [ks for ks in out if ks]


def _optional_null_params(spec: LangSpec, fn_node) -> list:
    out = []
    for n in _walk(fn_node):
        if n.type in ("assignment_pattern", "default_parameter", "optional_parameter"):
            left = n.child_by_field_name("left") or n.child_by_field_name("name")
            right = n.child_by_field_name("right") or n.child_by_field_name("value")
            if left is not None and right is not None and _text(right) in _NULLISH:
                nm = _rightmost_name(left)
                if nm and not nm.startswith("_"):
                    out.append((nm, _line(n)))
    return out


def _extract(spec: LangSpec, path: str, rel: str, root) -> CodeFacts:
    facts = CodeFacts(path=path, rel=rel, language=spec.lang)
    _collect_imports(root, facts)

    for n in _walk(root):
        # require("x")/load "x" style imports expressed as ordinary calls
        if n.type in spec.call_types and _callee_token(spec, n) in _REQUIRE_NAMES:
            s = _first_string(n)
            if s:
                facts.imports.add(_normalize_import(s))
        if n.type in spec.member_types:
            facts.consumed_attributes.add(_rightmost_name(n))
        elif n.type in spec.subscript_types:
            base = n.children[0] if n.children else None
            idx = n.child_by_field_name("index")
            sv = _string_value(idx) if idx is not None else _first_string(n)
            if sv is not None:
                base_name = _text(base) if base is not None and base.type in _NAME_TYPES else None
                facts.subscript_reads.append((base_name, sv, _line(n)))

    facts.consumed_attributes.discard(None)

    for fn_node in (n for n in _walk(root) if n.type in spec.func_types):
        calls = set()
        for n in _walk(fn_node):
            if n.type in spec.call_types:
                calls.add(_callee_token(spec, n))
        calls.discard(None)
        facts.functions.append(FunctionFacts(
            name=_function_name(fn_node), lineno=_line(fn_node),
            decorators=set(), calls=calls,
            optional_none_params=_optional_null_params(spec, fn_node),
            returned_dict_keysets=_returned_keysets(spec, fn_node),
        ))
    return facts


# --- language specs ------------------------------------------------------------
_CALL = frozenset({"call_expression"})
_SPECS = [
    LangSpec("javascript", (".js", ".jsx", ".mjs", ".cjs"), _CALL,
             frozenset({"function_declaration", "method_definition",
                        "function_expression", "arrow_function",
                        "generator_function_declaration"}),
             member_types=frozenset({"member_expression"}),
             subscript_types=frozenset({"subscript_expression"}),
             object_types=frozenset({"object"})),
    LangSpec("typescript", (".ts",), _CALL,
             frozenset({"function_declaration", "method_definition",
                        "function_expression", "arrow_function"}),
             member_types=frozenset({"member_expression"}),
             subscript_types=frozenset({"subscript_expression"}),
             object_types=frozenset({"object"})),
    LangSpec("tsx", (".tsx",), _CALL,
             frozenset({"function_declaration", "method_definition",
                        "function_expression", "arrow_function"}),
             member_types=frozenset({"member_expression"}),
             subscript_types=frozenset({"subscript_expression"}),
             object_types=frozenset({"object"})),
    LangSpec("go", (".go",), _CALL,
             frozenset({"function_declaration", "method_declaration"}),
             member_types=frozenset({"selector_expression"}),
             subscript_types=frozenset({"index_expression"}),
             object_types=frozenset({"composite_literal"})),
    LangSpec("rust", (".rs",), _CALL,
             frozenset({"function_item"}),
             member_types=frozenset({"field_expression"}),
             subscript_types=frozenset({"index_expression"})),
    LangSpec("ruby", (".rb",), frozenset({"call"}),
             frozenset({"method", "singleton_method"}),
             member_types=frozenset(),                       # a.b parses as `call`
             subscript_types=frozenset({"element_reference"}),
             object_types=frozenset({"hash"})),
    LangSpec("java", (".java",), frozenset({"method_invocation"}),
             frozenset({"method_declaration", "constructor_declaration"}),
             member_types=frozenset({"field_access"}),
             subscript_types=frozenset({"array_access"})),
    LangSpec("csharp", (".cs",), frozenset({"invocation_expression"}),
             frozenset({"method_declaration", "constructor_declaration",
                        "local_function_statement"}),
             member_types=frozenset({"member_access_expression"}),
             subscript_types=frozenset({"element_access_expression"})),
    LangSpec("kotlin", (".kt", ".kts"), _CALL,
             frozenset({"function_declaration"}),
             member_types=frozenset({"navigation_expression"}),
             subscript_types=frozenset({"indexing_expression"})),
    LangSpec("c", (".c", ".h"), _CALL,
             frozenset({"function_definition"}),
             member_types=frozenset({"field_expression"}),
             subscript_types=frozenset({"subscript_expression"})),
    LangSpec("cpp", (".cpp", ".cc", ".cxx", ".hpp", ".hh"), _CALL,
             frozenset({"function_definition"}),
             member_types=frozenset({"field_expression"}),
             subscript_types=frozenset({"subscript_expression"})),
    LangSpec("php", (".php",),
             frozenset({"function_call_expression", "member_call_expression",
                        "scoped_call_expression"}),
             frozenset({"function_definition", "method_declaration"}),
             member_types=frozenset({"member_access_expression"}),
             subscript_types=frozenset({"subscript_expression"})),
    LangSpec("swift", (".swift",), _CALL,
             frozenset({"function_declaration"}),
             member_types=frozenset({"navigation_expression"})),
    LangSpec("scala", (".scala", ".sc"), _CALL,
             frozenset({"function_definition"}),
             member_types=frozenset({"field_expression"})),
]


def _make_adapter(spec: LangSpec):
    def adapter(path: str, rel: str, source: str):
        tree = _parser(spec.lang).parse(source.encode("utf-8", "ignore"))
        return _extract(spec, path, rel, tree.root_node)
    return adapter


def register():
    for spec in _SPECS:
        try:
            _parser(spec.lang)                  # verify the grammar loads
        except Exception:
            continue
        register_adapter(spec.exts, _make_adapter(spec))
