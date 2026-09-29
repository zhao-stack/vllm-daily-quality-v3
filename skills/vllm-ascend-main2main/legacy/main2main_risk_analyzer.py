#!/usr/bin/env python3
"""Analyze vLLM main2main upgrade risks for vllm-ascend.

The script consumes mapping tables stored in the same directory as this file.
It predicts affected vllm-ascend files from a vLLM commit range and can evaluate
the prediction against a vllm-ascend commit or PR merge commit.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
LOW_SIGNAL_IMPORTED_ATTRIBUTES = {"F", "math", "nn", "np", "numpy", "torch", "typing"}
CORE_REVIEW_SCORE_THRESHOLD = 50
MANIFEST_FILE = "main2main_mapping_manifest.json"
DEFAULT_ANALYSIS_PROFILE = "exact-contracts"

# Kept for compatibility with skill copies that predate the manifest.  The
# broad table stays opt-in: it is a search aid, not an actionable relationship.
DEFAULT_TABLES = [
    {"name": "method", "file": "main2main_method_mapping.json", "role": "primary", "enabled_by_default": True},
    {"name": "field", "file": "main2main_field_mapping.json", "role": "primary", "enabled_by_default": False},
    {
        "name": "call_protocol",
        "file": "main2main_call_protocol_mapping.json",
        "role": "primary",
        "enabled_by_default": False,
    },
    {
        "name": "registration",
        "file": "main2main_registration_mapping.json",
        "role": "closure",
        "enabled_by_default": False,
    },
    {
        "name": "inheritance",
        "file": "main2main_inheritance_mapping.json",
        "role": "secondary",
        "enabled_by_default": False,
    },
    {"name": "broad", "file": "main2main_mapping.json", "role": "secondary", "enabled_by_default": False},
]


@dataclass
class Prediction:
    files_by_table: dict[str, set[str]]
    records_by_table: dict[str, list[dict[str, Any]]]
    changed_vllm_files: list[str]
    vllm_root: Path | None = None
    old: str | None = None
    new: str | None = None
    ascend_root: Path | None = None
    profile: str = DEFAULT_ANALYSIS_PROFILE

    @property
    def files(self) -> set[str]:
        merged: set[str] = set()
        for files in self.files_by_table.values():
            merged.update(files)
        return merged


@dataclass
class Validation:
    method_total: int
    method_stale: list[dict[str, Any]]
    field_total: int
    field_stale: list[dict[str, Any]]
    call_protocol_total: int
    call_protocol_stale: list[dict[str, Any]]
    missing_method_candidates: list[dict[str, Any]]


def run_git(repo: Path, args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def run_git_text(repo: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout if result.returncode == 0 else ""


def load_table(name: str) -> dict[str, Any]:
    path = SKILL_DIR / name
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def mapping_manifest_payload() -> dict[str, Any]:
    """Load the full manifest, preserving staged profile metadata."""

    path = SKILL_DIR / MANIFEST_FILE
    if not path.exists():
        return {
            "default_profile": DEFAULT_ANALYSIS_PROFILE,
            "profiles": {
                DEFAULT_ANALYSIS_PROFILE: {"tables": ["method"]},
                "expanded": {
                    "tables": [
                        "method",
                        "field",
                        "call_protocol",
                        "registration",
                        "inheritance",
                    ]
                },
            },
            "tables": [dict(item) for item in DEFAULT_TABLES],
        }
    with path.open("r", encoding="utf-8-sig") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"{MANIFEST_FILE}: root must be an object")
    return payload


def mapping_manifest() -> list[dict[str, Any]]:
    """Load the explicit table manifest, with a legacy-layout fallback.

    A malformed manifest is an operator error and must be visible.  Falling
    back is reserved for skill copies where the manifest does not exist yet.
    """

    payload = mapping_manifest_payload()
    tables = payload.get("tables")
    if not isinstance(tables, list):
        raise ValueError(f"{MANIFEST_FILE}: 'tables' must be a list")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(tables):
        if not isinstance(item, dict):
            raise ValueError(f"{MANIFEST_FILE}: tables[{index}] must be an object")
        # ``id/source/enabled`` was used by an early draft of the manifest;
        # accept it so optimized skill snapshots can be upgraded in place.
        name = item.get("name") or item.get("id")
        file_name = item.get("file") or item.get("source")
        role = item.get("role") or "secondary"
        if not all(isinstance(value, str) and value for value in (name, file_name, role)):
            raise ValueError(
                f"{MANIFEST_FILE}: tables[{index}] requires non-empty name, file, and role"
            )
        result.append(
            {
                **item,
                "name": name,
                "file": file_name,
                "role": role,
                "enabled_by_default": bool(
                    item.get("enabled_by_default", item.get("enabled", False))
                ),
            }
        )
    return result


def analysis_profiles() -> dict[str, list[str]]:
    payload = mapping_manifest_payload()
    raw_profiles = payload.get("profiles") or {}
    profiles: dict[str, list[str]] = {}
    if isinstance(raw_profiles, dict):
        for name, value in raw_profiles.items():
            tables = value.get("tables") if isinstance(value, dict) else None
            if isinstance(name, str) and isinstance(tables, list):
                profiles[name] = [str(item) for item in tables]
    if not profiles:
        profiles[DEFAULT_ANALYSIS_PROFILE] = [
            canonical_table_name(item)
            for item in mapping_manifest()
            if item.get("enabled_by_default")
        ]
    return profiles


def default_analysis_profile() -> str:
    payload = mapping_manifest_payload()
    value = payload.get("default_profile")
    profiles = analysis_profiles()
    if isinstance(value, str) and value in profiles:
        return value
    if DEFAULT_ANALYSIS_PROFILE in profiles:
        return DEFAULT_ANALYSIS_PROFILE
    return next(iter(profiles))


def resolve_analysis_profile(value: str | None) -> str:
    profile = value or default_analysis_profile()
    if profile not in analysis_profiles():
        choices = ", ".join(sorted(analysis_profiles()))
        raise ValueError(f"unknown analysis profile {profile!r}; choose one of: {choices}")
    return profile


def enabled_table_specs(profile: str | None = None) -> dict[str, dict[str, Any]]:
    selected = set(analysis_profiles()[resolve_analysis_profile(profile)])
    return {
        canonical_table_name(item): item
        for item in mapping_manifest()
        if canonical_table_name(item) in selected
    }


def canonical_table_name(spec: dict[str, Any]) -> str:
    file_name = str(spec.get("file") or "")
    name = str(spec.get("name") or "")
    if "call_protocol" in file_name or "call_protocol" in name:
        return "call_protocol"
    for candidate in ("registration", "inheritance", "method", "field", "broad"):
        if candidate in file_name or candidate in name:
            return candidate
    return name


def table_file(name: str, fallback: str) -> str:
    spec = next((item for item in mapping_manifest() if canonical_table_name(item) == name), None)
    return str(spec.get("file")) if spec else fallback


def record_actionable(record: dict[str, Any]) -> bool:
    """Return false for records explicitly retired by table maintenance."""

    action = str(record.get("action") or "").strip().lower()
    status = str(record.get("status") or "").strip().lower()
    verification = record.get("verification") or {}
    verification_status = (
        str(verification.get("status") or "").strip().lower()
        if isinstance(verification, dict)
        else ""
    )
    explicit_verification_status = str(record.get("verification_status") or "").strip().lower()
    retired = {"dismiss", "dismissed", "stale"}
    return (
        action not in retired
        and status not in retired
        and verification_status not in retired
        and explicit_verification_status not in retired
    )


def active_mappings(file_name: str) -> list[dict[str, Any]]:
    return [
        record
        for record in load_table(file_name).get("mappings", [])
        if isinstance(record, dict) and record_actionable(record)
    ]


def relationship_verified(record: dict[str, Any]) -> bool:
    if record.get("_relationship_rejected_current") is True:
        return False
    if (
        record.get("relationship_verified") is True
        or record.get("_relationship_verified_current") is True
    ):
        return True
    if str(record.get("verification_status") or "").lower() == "verified":
        return True
    verification = record.get("verification") or {}
    return isinstance(verification, dict) and str(verification.get("status") or "").lower() == "verified"


def module_to_file(root: Path, module: str) -> str | None:
    rel = module.replace(".", "/")
    for suffix in (".py", "/__init__.py"):
        path = root / f"{rel}{suffix}"
        if path.exists():
            return path.relative_to(root).as_posix()
    return None


def compact_signature(lines: list[str], line_no: int, limit: int = 40) -> str:
    collected: list[str] = []
    balance = 0
    for offset, line in enumerate(lines[line_no - 1 : line_no - 1 + limit]):
        stripped = line.strip()
        if not stripped:
            continue
        collected.append(stripped)
        balance += stripped.count("(") - stripped.count(")")
        if offset > 0 and balance <= 0 and stripped.endswith(":"):
            break
        if offset == 0 and stripped.endswith(":") and balance <= 0:
            break
    text = " ".join(collected)
    return " ".join(text.split())


def assignment_target_names(target: ast.expr) -> set[str]:
    names: set[str] = set()
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, ast.Attribute):
        names.add(target.attr)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            names.update(assignment_target_names(item))
    return names


def parse_python_module(path: Path) -> dict[str, Any]:
    if not path.exists() or path.suffix not in {".py", ".pyi"}:
        return {"classes": {}, "functions": {}, "symbols": set()}
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {"classes": {}, "functions": {}, "symbols": set()}
    classes: dict[str, dict[str, Any]] = {}
    functions: dict[str, dict[str, Any]] = {}
    symbols: set[str] = set()

    def base_name(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Subscript):
            return base_name(node.value)
        if isinstance(node, ast.Call):
            return base_name(node.func)
        return ""

    def record_statement_symbols(node: ast.stmt, bucket: set[str]) -> None:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                bucket.update(assignment_target_names(target))
        elif isinstance(node, ast.AnnAssign):
            bucket.update(assignment_target_names(node.target))
        elif isinstance(node, ast.AugAssign):
            bucket.update(assignment_target_names(node.target))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    bucket.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bucket.add(alias.asname or alias.name.split(".", 1)[0])

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = {
                "line": node.lineno,
                "signature": compact_signature(lines, node.lineno),
            }
            symbols.add(node.name)
            for stmt in ast.walk(node):
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt is not node:
                    symbols.add(stmt.name)
                if isinstance(stmt, ast.Assign):
                    targets = stmt.targets
                elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
                    targets = [stmt.target]
                else:
                    targets = []
                for target in targets:
                    symbols.update(assignment_target_names(target))
            continue
        if not isinstance(node, ast.ClassDef):
            record_statement_symbols(node, symbols)
            continue
        methods: dict[str, dict[str, Any]] = {}
        attrs: set[str] = set()
        nested_functions: set[str] = set()
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods[child.name] = {
                    "line": child.lineno,
                    "signature": compact_signature(lines, child.lineno),
                }
                for stmt in ast.walk(child):
                    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt is not child:
                        nested_functions.add(stmt.name)
                    if isinstance(stmt, ast.Assign):
                        targets = stmt.targets
                    elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
                        targets = [stmt.target]
                    else:
                        targets = []
                    for target in targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "self"
                        ):
                            attrs.add(target.attr)
            else:
                for nested in ast.walk(child):
                    if isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods[nested.name] = {
                            "line": nested.lineno,
                            "signature": compact_signature(lines, nested.lineno),
                        }
                record_statement_symbols(child, attrs)
        classes[node.name] = {
            "line": node.lineno,
            "bases": [base_name(base) for base in node.bases if base_name(base)],
            "methods": methods,
            "attrs": attrs,
            "nested_functions": nested_functions,
        }
        symbols.add(node.name)
    return {"classes": classes, "functions": functions, "symbols": symbols}


def parse_python_text(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {"classes": {}, "functions": {}}
    classes: dict[str, dict[str, Any]] = {}
    functions: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = {
                "line": node.lineno,
                "signature": compact_signature(lines, node.lineno),
            }
            continue
        if not isinstance(node, ast.ClassDef):
            continue
        methods: dict[str, dict[str, Any]] = {}
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods[child.name] = {
                    "line": child.lineno,
                    "signature": compact_signature(lines, child.lineno),
                }
        classes[node.name] = {"line": node.lineno, "methods": methods}
    return {"classes": classes, "functions": functions}


def parse_python_classes(path: Path) -> dict[str, dict[str, Any]]:
    return parse_python_module(path).get("classes", {})


def imported_vllm_classes(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists() or path.suffix not in {".py", ".pyi"}:
        return {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return {}
    imports: dict[str, dict[str, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("vllm"):
            for alias in node.names:
                local_name = alias.asname or alias.name
                imports[local_name] = {
                    "module": node.module,
                    "name": alias.name,
                }
    return imports


def upstream_import_bindings(text: str) -> dict[str, tuple[str, str]]:
    """Map local names to upstream modules and optional imported symbols."""

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    bindings: dict[str, tuple[str, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name != "*":
                    bindings[alias.asname or alias.name] = (node.module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bindings[alias.asname or alias.name.split(".", 1)[0]] = (
                    alias.name,
                    "",
                )
    return bindings


def dotted_ast_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def callable_ast_node(
    text: str, class_name: str | None, method_name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return callable_nodes(text, class_name).get(method_name)


def callable_forwards_kwargs_to_super(
    text: str, class_name: str | None, method_name: str
) -> bool:
    """Prove ``**kwargs`` is forwarded by this callable to ``super()``."""

    node = callable_ast_node(text, class_name, method_name)
    if node is None or node.args.kwarg is None:
        return False
    kwarg = node.args.kwarg.arg
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        owner = child.func.value
        if not (
            isinstance(owner, ast.Call)
            and isinstance(owner.func, ast.Name)
            and owner.func.id == "super"
        ):
            continue
        if child.func.attr != method_name:
            continue
        if any(keyword.arg is None and isinstance(keyword.value, ast.Name) and keyword.value.id == kwarg for keyword in child.keywords):
            return True
    return False


def callable_calls_super_method(
    text: str, class_name: str | None, method_name: str
) -> bool:
    """Prove the callable invokes the same method on ``super()``."""

    node = callable_ast_node(text, class_name, method_name)
    if node is None:
        return False
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        owner = child.func.value
        if (
            isinstance(owner, ast.Call)
            and isinstance(owner.func, ast.Name)
            and owner.func.id == "super"
            and child.func.attr == method_name
        ):
            return True
    return False


def optional_parameter_additions_only(
    old_shape: tuple[Any, ...] | None, new_shape: tuple[Any, ...] | None
) -> bool:
    """Return true when a delta only adds optional named parameters."""

    if old_shape is None or new_shape is None:
        return False
    old_pos, old_kw, old_vararg, old_kwarg = old_shape
    new_pos, new_kw, new_vararg, new_kwarg = new_shape
    old_parameters = {name: required for name, required in (*old_pos, *old_kw)}
    new_parameters = {name: required for name, required in (*new_pos, *new_kw)}
    if any(
        name not in new_parameters or new_parameters[name] != required
        for name, required in old_parameters.items()
    ):
        return False
    additions = [
        required for name, required in new_parameters.items() if name not in old_parameters
    ]
    return bool(additions) and not any(additions) and old_vararg == new_vararg and old_kwarg == new_kwarg


def callable_call_graph(text: str) -> tuple[dict[str, set[str]], set[str], dict[str, tuple[str, str]]]:
    """Return local callable edges, local class names and import bindings."""

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}, set(), {}
    graph: dict[str, set[str]] = {}
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}

    def calls(node: ast.AST) -> set[str]:
        values: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                name = dotted_ast_name(child.func)
                if name:
                    values.add(name)
        return values

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            graph[node.name] = calls(node)
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    graph[f"{node.name}.{child.name}"] = calls(child)
    return graph, classes, upstream_import_bindings(text)


def reachable_imported_calls(text: str, root_callable: str) -> list[tuple[list[str], str, str, str]]:
    """Resolve imported callables reached from a local patch callable."""

    graph, classes, bindings = callable_call_graph(text)
    queue: list[tuple[str, list[str]]] = [(root_callable, [root_callable])]
    visited: set[str] = set()
    results: list[tuple[list[str], str, str, str]] = []
    while queue:
        current, path = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for called in graph.get(current, set()):
            root = called.split(".", 1)[0]
            if root in classes:
                target = f"{root}.__init__"
                if target in graph:
                    queue.append((target, [*path, target]))
                continue
            if called in graph:
                queue.append((called, [*path, called]))
                continue
            binding = bindings.get(root)
            if not binding:
                continue
            module, symbol = binding
            if not module.startswith("vllm_ascend"):
                continue
            imported_symbol = symbol or called.split(".")[-1]
            results.append((path, module, imported_symbol, called))
    return results


def upstream_class_bases(
    text: str, module: str, class_name: str
) -> list[tuple[str, str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    owner = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name),
        None,
    )
    if owner is None:
        return []
    bindings = upstream_import_bindings(text)
    bases: list[tuple[str, str]] = []
    for base in owner.bases:
        dotted = dotted_ast_name(base)
        if not dotted:
            continue
        parts = dotted.split(".")
        binding = bindings.get(parts[0])
        if binding:
            bound_module, bound_symbol = binding
            if bound_symbol:
                if len(parts) == 1:
                    bases.append((bound_module, bound_symbol))
                else:
                    bases.append(
                        (
                            ".".join([bound_module, bound_symbol, *parts[1:-1]]),
                            parts[-1],
                        )
                    )
            else:
                bases.append(
                    (
                        ".".join([bound_module, *parts[1:-1]]),
                        parts[-1],
                    )
                )
        elif len(parts) == 1:
            bases.append((module, parts[0]))
        elif dotted.startswith("vllm."):
            bases.append((".".join(parts[:-1]), parts[-1]))
    return bases


def resolve_upstream_method_in_mro(
    vllm_root: Path,
    commit: str,
    files: set[str],
    module: str,
    class_name: str,
    method: str,
    source_cache: dict[tuple[str, str], str],
    seen: set[tuple[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Resolve the effective method owner through a statically provable MRO."""

    visited = set() if seen is None else seen
    key = (module, class_name)
    if key in visited:
        return None
    visited.add(key)
    file_path = module_file_from_tree(files, module)
    if not file_path:
        return None
    text = source_at_commit(vllm_root, commit, file_path, source_cache)
    node = callable_nodes(text, class_name).get(method)
    if node is not None:
        return {
            "file": file_path,
            "module": module,
            "class": class_name,
            "method": method,
            "shape": callable_parameter_shape(text, class_name, method),
            "signature": compact_signature(text.splitlines(), node.lineno),
        }
    for base_module, base_class in upstream_class_bases(text, module, class_name):
        resolved = resolve_upstream_method_in_mro(
            vllm_root,
            commit,
            files,
            base_module,
            base_class,
            method,
            source_cache,
            visited,
        )
        if resolved:
            resolved = dict(resolved)
            resolved["mro_via"] = [class_name, *(resolved.get("mro_via") or [])]
            return resolved
    return None


def imported_vllm_symbols(path: Path) -> list[dict[str, Any]]:
    """Return concrete vLLM imports and module-path literals used by a file."""

    if not path.exists() or path.suffix not in {".py", ".pyi"}:
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    imports: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    attribute_references: set[str] = set()

    def dotted_name(node: ast.AST) -> str | None:
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            return None
        parts.append(current.id)
        return ".".join(reversed(parts))

    def add(
        kind: str,
        module: str,
        symbol: str = "",
        *,
        version_guarded: bool = False,
    ) -> None:
        key = (kind, module, symbol)
        if module.startswith("vllm") and key not in seen:
            seen.add(key)
            imports.append(
                {
                    "kind": kind,
                    "module": module,
                    "symbol": symbol,
                    "version_guarded": version_guarded,
                }
            )

    guarded_attribute_references: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or "vllm_version_is" not in ast.dump(node.test):
            continue
        for guarded_node in ast.walk(
            ast.Module(body=[*node.body, *node.orelse], type_ignores=[])
        ):
            if isinstance(guarded_node, ast.Attribute):
                reference = dotted_name(guarded_node)
                if reference and reference.startswith("vllm."):
                    guarded_attribute_references.add(reference)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name != "*":
                    add("from_import", node.module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                add("module_import", alias.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.fullmatch(r"vllm(?:\.[A-Za-z_][A-Za-z0-9_]*)+", node.value):
                add("module_literal", node.value)
        elif isinstance(node, ast.Attribute):
            reference = dotted_name(node)
            if reference and reference.startswith("vllm."):
                attribute_references.add(reference)
    maximal_references = {
        reference
        for reference in attribute_references
        if not any(
            other != reference and other.startswith(reference + ".")
            for other in attribute_references
        )
    }
    for reference in sorted(maximal_references):
        add(
            "attribute_chain",
            reference,
            version_guarded=reference in guarded_attribute_references,
        )
    return imports


def module_file_at_commit(root: Path, commit: str, module: str) -> str | None:
    rel = module.replace(".", "/")
    for suffix in (".py", "/__init__.py"):
        candidate = f"{rel}{suffix}"
        result = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{commit}:{candidate}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return candidate
    return None


def python_files_at_commit(root: Path, commit: str) -> set[str]:
    """Load the vLLM Python tree once to avoid one git process per import."""

    return {
        item
        for item in run_git(
            root, ["ls-tree", "-r", "--name-only", commit, "--", "vllm"]
        )
        if item.endswith((".py", ".pyi"))
    }


def module_file_from_tree(files: set[str], module: str) -> str | None:
    rel = module.replace(".", "/")
    for suffix in (".py", "/__init__.py"):
        candidate = f"{rel}{suffix}"
        if candidate in files:
            return candidate
    return None


def resolve_module_reference(
    files: set[str], reference: str
) -> tuple[str, str, str] | None:
    """Split a dotted expression into its longest real module and symbol tail."""

    parts = reference.split(".")
    for index in range(len(parts), 0, -1):
        module = ".".join(parts[:index])
        file_path = module_file_from_tree(files, module)
        if file_path:
            return file_path, module, ".".join(parts[index:])
    return None


def file_to_module(file_path: str) -> str:
    value = file_path[:-3] if file_path.endswith(".py") else file_path
    if value.endswith("/__init__"):
        value = value[: -len("/__init__")]
    return value.replace("/", ".")


def exact_file_renames(
    root: Path, old: str, new: str, similarity: int = 70
) -> dict[str, str]:
    """Return Git-proven file relocations for the requested range."""

    lines = run_git(
        root,
        [
            "diff",
            "--name-status",
            f"--find-renames={similarity}%",
            old,
            new,
            "--",
            "vllm",
        ],
    )
    renames: dict[str, str] = {}
    for line in lines:
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            renames[parts[1]] = parts[2]
    return renames


def top_level_symbol_profile(text: str, symbol: str) -> str | None:
    """Return a public contract profile, excluding implementation bodies."""

    if not text or not symbol:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == symbol:
            methods = []
            fields = []
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(
                        (
                            child.name,
                            ast.dump(
                                child.args,
                                annotate_fields=True,
                                include_attributes=False,
                            ),
                        )
                    )
                elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                    fields.append(
                        ast.dump(
                            child,
                            annotate_fields=True,
                            include_attributes=False,
                        )
                    )
            return repr(
                {
                    "bases": [
                        ast.dump(base, annotate_fields=True, include_attributes=False)
                        for base in node.bases
                    ],
                    "methods": methods,
                    "fields": fields,
                }
            )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
            return ast.dump(
                node.args, annotate_fields=True, include_attributes=False
            )
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if symbol in set().union(*(assignment_target_names(target) for target in targets)):
                return ast.dump(node, annotate_fields=True, include_attributes=False)
    return None


def method_mapping_key(record: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    upstream = record.get("upstream") or {}
    ascend = record.get("ascend") or {}
    return (
        str(upstream.get("file") or ""),
        str(upstream.get("class") or ""),
        str(upstream.get("method") or upstream.get("function") or ""),
        str(ascend.get("file") or ""),
        str(ascend.get("class") or ""),
        str(ascend.get("method") or ascend.get("function") or ""),
    )


def method_exists(root: Path, rel_file: str | None, class_name: str | None, method: str | None) -> bool:
    if not rel_file or not method:
        return False
    module = parse_python_module(root / rel_file)
    if class_name:
        classes = module.get("classes", {})
        class_info = classes.get(class_name, {})
        return (
            method in class_info.get("methods", {})
            or method in class_info.get("attrs", set())
            or method in class_info.get("nested_functions", set())
        )
    return method in module.get("functions", {}) or method in module.get("symbols", set())


def class_exists(root: Path, rel_file: str | None, class_name: str | None) -> bool:
    if not rel_file or not class_name:
        return False
    return class_name in parse_python_module(root / rel_file).get("classes", {})


def symbol_exists(root: Path, rel_file: str | None, symbol: str | None) -> bool:
    if not rel_file or not symbol:
        return False
    path = root / rel_file
    if not path.exists() or path.suffix not in {".py", ".pyi"}:
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    if symbol in text:
        return True
    module = parse_python_module(path)
    classes = module.get("classes", {})
    functions = module.get("functions", {})
    symbols = module.get("symbols", set())
    parts = [part for part in symbol.split(".") if part]
    if len(parts) == 1:
        return parts[0] in classes or parts[0] in functions or parts[0] in symbols
    class_name = parts[-2]
    member_name = parts[-1]
    class_info = classes.get(class_name)
    if class_info and (
        member_name in class_info.get("methods", {})
        or member_name in class_info.get("attrs", set())
        or member_name in class_info.get("nested_functions", set())
    ):
        return True
    return member_name in functions or member_name in symbols


def _legacy_validate_method_table(vllm_root: Path, ascend_root: Path) -> tuple[int, list[dict[str, Any]]]:
    mappings = active_mappings(table_file("method", "main2main_method_mapping.json"))
    stale: list[dict[str, Any]] = []
    for record in mappings:
        upstream = record.get("upstream") or {}
        ascend = record.get("ascend") or {}
        problems: list[str] = []
        if not method_exists(
            vllm_root,
            upstream.get("file"),
            upstream.get("class"),
            upstream.get("method") or upstream.get("function"),
        ):
            problems.append("上游方法不存在或已移动")
        if not method_exists(
            ascend_root,
            ascend.get("file"),
            ascend.get("class"),
            ascend.get("method") or ascend.get("function"),
        ):
            problems.append("Ascend 方法不存在或已移动")
        if problems:
            stale.append(
                {
                    "relation": record.get("relation"),
                    "risk": record.get("risk"),
                    "upstream": upstream,
                    "ascend": ascend,
                    "problems": problems,
                }
            )
    return len(mappings), stale


def _legacy_validate_call_protocol_table(vllm_root: Path, ascend_root: Path) -> tuple[int, list[dict[str, Any]]]:
    mappings = active_mappings(table_file("call_protocol", "main2main_call_protocol_mapping.json"))
    stale: list[dict[str, Any]] = []
    for record in mappings:
        upstream = record.get("upstream") or {}
        ascend = record.get("ascend") or {}
        problems: list[str] = []
        up_file = upstream.get("file")
        asc_file = ascend.get("file")
        if not isinstance(up_file, str) or not (vllm_root / up_file).exists():
            problems.append("上游文件不存在或已移动")
        if not isinstance(asc_file, str) or not (ascend_root / asc_file).exists():
            problems.append("Ascend 文件不存在或已移动")
        symbol = ascend.get("symbol") or ascend.get("callee") or ascend.get("class")
        if isinstance(asc_file, str) and isinstance(symbol, str) and (ascend_root / asc_file).exists():
            text = (ascend_root / asc_file).read_text(encoding="utf-8", errors="ignore")
            if symbol not in text:
                problems.append("Ascend 协议符号未在文件中找到")
        if problems:
            stale.append(
                {
                    "relation": record.get("relation"),
                    "risk": record.get("risk"),
                    "upstream": upstream,
                    "ascend": ascend,
                    "problems": problems,
                }
            )
    return len(mappings), stale


def validate_method_table(vllm_root: Path, ascend_root: Path) -> tuple[int, list[dict[str, Any]]]:
    mappings = active_mappings(table_file("method", "main2main_method_mapping.json"))
    stale: list[dict[str, Any]] = []
    for record in mappings:
        upstream = record.get("upstream") or {}
        ascend = record.get("ascend") or {}
        problems: list[str] = []
        if not method_exists(
            vllm_root,
            upstream.get("file"),
            upstream.get("class"),
            upstream.get("method") or upstream.get("function"),
        ):
            problems.append("upstream callable not found or moved")
        if not method_exists(
            ascend_root,
            ascend.get("file"),
            ascend.get("class"),
            ascend.get("method") or ascend.get("function"),
        ):
            problems.append("Ascend callable not found or moved")
        if problems:
            stale.append(
                {
                    "relation": record.get("relation"),
                    "risk": record.get("risk"),
                    "upstream": upstream,
                    "ascend": ascend,
                    "problems": problems,
                }
            )
    return len(mappings), stale


def validate_call_protocol_table(vllm_root: Path, ascend_root: Path) -> tuple[int, list[dict[str, Any]]]:
    mappings = active_mappings(table_file("call_protocol", "main2main_call_protocol_mapping.json"))
    stale: list[dict[str, Any]] = []
    for record in mappings:
        upstream = record.get("upstream") or {}
        ascend = record.get("ascend") or {}
        problems: list[str] = []
        up_file = upstream.get("file")
        asc_file = ascend.get("file")
        if not isinstance(up_file, str) or not (vllm_root / up_file).exists():
            problems.append("upstream file not found or moved")
        if not isinstance(asc_file, str) or not (ascend_root / asc_file).exists():
            problems.append("Ascend file not found or moved")
        symbol = ascend.get("symbol") or ascend.get("callee") or ascend.get("class")
        if isinstance(asc_file, str) and isinstance(symbol, str):
            if not symbol_exists(ascend_root, asc_file, symbol):
                problems.append("Ascend protocol symbol not found")
        if problems:
            stale.append(
                {
                    "relation": record.get("relation"),
                    "risk": record.get("risk"),
                    "upstream": upstream,
                    "ascend": ascend,
                    "problems": problems,
                }
            )
    return len(mappings), stale


def validate_field_table(vllm_root: Path, ascend_root: Path) -> tuple[int, list[dict[str, Any]]]:
    mappings = active_mappings(table_file("field", "main2main_field_mapping.json"))
    stale: list[dict[str, Any]] = []
    for record in mappings:
        upstream = record.get("upstream") or {}
        ascend = record.get("ascend") or {}
        problems: list[str] = []
        up_file = upstream.get("file")
        if isinstance(up_file, str) and up_file and not (vllm_root / up_file).exists():
            problems.append("上游文件不存在或已移动")
        missing_targets = [
            target
            for target in ascend_target_files(record)
            if not (ascend_root / target).exists()
        ]
        if missing_targets:
            problems.append("Ascend 文件不存在或已移动: " + ", ".join(missing_targets[:3]))
        if problems:
            stale.append(
                {
                    "relation": record.get("relation"),
                    "risk": record.get("risk"),
                    "upstream": upstream,
                    "ascend": ascend,
                    "problems": problems,
                }
            )
    return len(mappings), stale


def discover_missing_method_candidates(vllm_root: Path, ascend_root: Path, limit: int = 200) -> list[dict[str, Any]]:
    existing = {
        method_mapping_key(record)
        for record in active_mappings(table_file("method", "main2main_method_mapping.json"))
    }
    candidates: list[dict[str, Any]] = []
    for path in sorted((ascend_root / "vllm_ascend").rglob("*.py")):
        rel_file = path.relative_to(ascend_root).as_posix()
        imports = imported_vllm_classes(path)
        if not imports:
            continue
        ascend_classes = parse_python_classes(path)
        for ascend_class, ascend_info in ascend_classes.items():
            for base in ascend_info.get("bases", []):
                imported = imports.get(base)
                if not imported:
                    continue
                upstream_file = module_to_file(vllm_root, imported["module"])
                if not upstream_file:
                    continue
                upstream_classes = parse_python_classes(vllm_root / upstream_file)
                upstream_info = upstream_classes.get(imported["name"])
                if not upstream_info:
                    continue
                for method, ascend_method in ascend_info.get("methods", {}).items():
                    if method.startswith("_") and method not in {"__init__", "__call__"}:
                        continue
                    if method == "__init__" and not callable_forwards_kwargs_to_super(
                        path.read_text(encoding="utf-8", errors="ignore"),
                        ascend_class,
                        method,
                    ):
                        continue
                    upstream_method = upstream_info.get("methods", {}).get(method)
                    if not upstream_method:
                        continue
                    key = (
                        upstream_file,
                        imported["name"],
                        method,
                        rel_file,
                        ascend_class,
                        method,
                    )
                    if key in existing:
                        continue
                    candidates.append(
                        {
                            "relation": "override_candidate",
                            "risk": "critical"
                            if upstream_method.get("signature") != ascend_method.get("signature")
                            else "high",
                            "upstream": {
                                "file": upstream_file,
                                "module": imported["module"],
                                "class": imported["name"],
                                "method": method,
                                "line": upstream_method.get("line"),
                                "signature": upstream_method.get("signature"),
                            },
                            "ascend": {
                                "file": rel_file,
                                "class": ascend_class,
                                "method": method,
                                "line": ascend_method.get("line"),
                                "signature": ascend_method.get("signature"),
                            },
                            "base_expression": base,
                            "signature_changed": upstream_method.get("signature") != ascend_method.get("signature"),
                            "reason": "Ascend class inherits a vLLM class and overrides the same method, but method mapping table has no exact entry.",
                        }
                    )
                    if len(candidates) >= limit:
                        return candidates
    return candidates


def discover_changed_override_candidates(
    vllm_root: Path,
    ascend_root: Path,
    old: str,
    new: str,
    limit: int = 400,
) -> list[dict[str, Any]]:
    """Discover range-specific direct overrides missing from canonical tables.

    Unlike validation against the final tree, this also preserves evidence for
    an upstream base class or method removed by the requested range.
    """

    existing = {
        method_mapping_key(record)
        for record in active_mappings(table_file("method", "main2main_method_mapping.json"))
    }
    source_cache: dict[tuple[str, str], str] = {}
    candidates: list[dict[str, Any]] = []
    module_cache: dict[tuple[str, str], str | None] = {}
    trees = {
        old: python_files_at_commit(vllm_root, old),
        new: python_files_at_commit(vllm_root, new),
    }
    rename_map = exact_file_renames(vllm_root, old, new)

    def module_file(commit: str, module: str) -> str | None:
        key = (commit, module)
        if key not in module_cache:
            module_cache[key] = module_file_from_tree(trees[commit], module)
        return module_cache[key]

    for path in sorted((ascend_root / "vllm_ascend").rglob("*.py")):
        rel_file = path.relative_to(ascend_root).as_posix()
        imports = imported_vllm_classes(path)
        if not imports:
            continue
        ascend_classes = parse_python_classes(path)
        for ascend_class, ascend_info in ascend_classes.items():
            for base in ascend_info.get("bases", []):
                imported = imports.get(base)
                if not imported:
                    continue
                old_file = module_file(old, imported["module"])
                new_file = module_file(new, imported["module"])
                if old_file and not new_file:
                    new_file = rename_map.get(old_file)
                old_text = (
                    source_at_commit(vllm_root, old, old_file, source_cache)
                    if old_file
                    else ""
                )
                new_text = (
                    source_at_commit(vllm_root, new, new_file, source_cache)
                    if new_file
                    else ""
                )
                old_class = parse_python_text(old_text).get("classes", {}).get(imported["name"])
                new_class = parse_python_text(new_text).get("classes", {}).get(imported["name"])
                if not old_class:
                    continue

                if not new_class:
                    candidates.append(
                        {
                            "relation": "override_candidate",
                            "risk": "critical",
                            "relationship_verified": True,
                            "verification_status": "verified",
                            "upstream": {
                                "file": old_file,
                                "module": imported["module"],
                                "class": imported["name"],
                            },
                            "ascend": {"file": rel_file, "class": ascend_class},
                            "base_expression": base,
                            "_contract_delta": {
                                "kind": "base_class_removed_or_relocated",
                                "old_file": old_file,
                                "new_file": new_file,
                            },
                            "_dynamic_candidate": True,
                            "reason": "Directly inherited upstream base disappeared or relocated in this range.",
                        }
                    )
                    if len(candidates) >= limit:
                        return candidates
                    continue

                for method, ascend_method in ascend_info.get("methods", {}).items():
                    if method.startswith("_") and method not in {"__init__", "__call__"}:
                        continue
                    old_method = resolve_upstream_method_in_mro(
                        vllm_root,
                        old,
                        trees[old],
                        imported["module"],
                        imported["name"],
                        method,
                        source_cache,
                    )
                    new_method = resolve_upstream_method_in_mro(
                        vllm_root,
                        new,
                        trees[new],
                        imported["module"],
                        imported["name"],
                        method,
                        source_cache,
                    )
                    if not old_method and not new_method:
                        continue
                    if (
                        old_method
                        and new_method
                        and old_method.get("shape") == new_method.get("shape")
                    ):
                        continue
                    key = (
                        str((new_method or old_method or {}).get("file") or ""),
                        str((new_method or old_method or {}).get("class") or ""),
                        method,
                        rel_file,
                        ascend_class,
                        method,
                    )
                    if key in existing:
                        continue
                    candidates.append(
                        {
                            "relation": "override_candidate",
                            "risk": "critical",
                            "relationship_verified": True,
                            "verification_status": "verified",
                            "upstream": {
                                "file": (new_method or old_method or {}).get("file"),
                                "file_aliases": [
                                    item
                                    for item in (
                                        (old_method or {}).get("file"),
                                        (new_method or {}).get("file"),
                                    )
                                    if item
                                ],
                                "module": (new_method or old_method or {}).get("module"),
                                "class": (new_method or old_method or {}).get("class"),
                                "method": method,
                                "signature": new_method.get("signature") if new_method else None,
                            },
                            "ascend": {
                                "file": rel_file,
                                "class": ascend_class,
                                "method": method,
                                "line": ascend_method.get("line"),
                                "signature": ascend_method.get("signature"),
                            },
                            "base_expression": base,
                            "_contract_delta": {
                                "kind": "override_signature_delta"
                                if old_method and new_method
                                else "overridden_method_introduced"
                                if new_method
                                else "overridden_method_removed_or_relocated",
                                "old_signature": old_method.get("signature") if old_method else None,
                                "new_signature": new_method.get("signature") if new_method else None,
                                "old_owner": old_method.get("class") if old_method else None,
                                "new_owner": new_method.get("class") if new_method else None,
                                "mro_verified": True,
                            },
                            "_dynamic_candidate": True,
                            "reason": "Direct override changed in the requested upstream range but is absent from the canonical method table.",
                        }
                    )
                    if len(candidates) >= limit:
                        return candidates
    return candidates


def discover_patch_binding_candidates(
    vllm_root: Path,
    ascend_root: Path,
    old: str,
    new: str,
    changed: set[str],
    limit: int = 400,
) -> list[dict[str, Any]]:
    """Discover exact upstream-attribute assignments to Ascend callables."""

    trees = {
        old: python_files_at_commit(vllm_root, old),
        new: python_files_at_commit(vllm_root, new),
    }
    renames = exact_file_renames(vllm_root, old, new)
    source_cache: dict[tuple[str, str], str] = {}
    existing = {
        method_mapping_key(record)
        for record in active_mappings(table_file("method", "main2main_method_mapping.json"))
    }
    grouped: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}

    def contract(
        module: str, class_name: str | None, method: str
    ) -> tuple[str, str, dict[str, Any]] | None:
        old_file = module_file_from_tree(trees[old], module)
        if not old_file:
            return None
        new_file = module_file_from_tree(trees[new], module) or renames.get(old_file)
        old_text = source_at_commit(vllm_root, old, old_file, source_cache)
        new_text = (
            source_at_commit(vllm_root, new, new_file, source_cache)
            if new_file
            else ""
        )
        old_shape = callable_parameter_shape(old_text, class_name, method)
        new_shape = callable_parameter_shape(new_text, class_name, method)
        if old_shape is None and new_shape is None:
            return None
        if old_shape == new_shape:
            return None
        if old_shape is not None and new_shape is not None:
            kind = "override_signature_delta"
        elif new_shape is not None:
            kind = "overridden_method_introduced"
        else:
            kind = "overridden_method_removed_or_relocated"
        return old_file, new_file or old_file, {
            "kind": kind,
            "old_shape": old_shape,
            "new_shape": new_shape,
            "binding_verified": True,
        }

    for path in sorted((ascend_root / "vllm_ascend").rglob("*.py")):
        rel_file = path.relative_to(ascend_root).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        bindings = upstream_import_bindings(text)
        parsed = parse_python_module(path)
        local_functions = set(parsed.get("functions", {}))
        local_classes = set(parsed.get("classes", {}))

        class BindingVisitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.scopes: list[str] = []
                self.guards: list[str] = []

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
                self.scopes.append(node.name)
                self.generic_visit(node)
                self.scopes.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_If(self, node: ast.If) -> None:  # noqa: N802
                self.guards.append(ast.unparse(node.test))
                for child in node.body:
                    self.visit(child)
                self.guards.pop()
                for child in node.orelse:
                    self.visit(child)

            def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
                replacement = dotted_ast_name(node.value)
                if not replacement:
                    self.generic_visit(node)
                    return
                replacement_parts = replacement.split(".")
                if replacement_parts[0] in local_functions:
                    ascend_class = None
                    ascend_method = replacement_parts[0]
                elif (
                    len(replacement_parts) == 2
                    and replacement_parts[0] in local_classes
                ):
                    ascend_class, ascend_method = replacement_parts
                else:
                    self.generic_visit(node)
                    return
                for target in node.targets:
                    dotted = dotted_ast_name(target)
                    if not dotted:
                        continue
                    parts = dotted.split(".")
                    binding = bindings.get(parts[0])
                    if not binding:
                        continue
                    bound_module, bound_symbol = binding
                    reference_parts = [bound_module]
                    if bound_symbol:
                        reference_parts.append(bound_symbol)
                    reference_parts.extend(parts[1:])
                    resolved = resolve_module_reference(
                        trees[old], ".".join(reference_parts)
                    )
                    if not resolved:
                        continue
                    _, module, symbol_tail = resolved
                    tail = symbol_tail.split(".") if symbol_tail else []
                    if not tail:
                        continue
                    method = tail[-1]
                    class_name = tail[-2] if len(tail) > 1 else None
                    details = contract(module, class_name, method)
                    if not details and class_name:
                        class_name = None
                        details = contract(module, class_name, method)
                    if not details:
                        continue
                    old_file, new_file, delta = details
                    key = (
                        new_file,
                        class_name or "",
                        method,
                        rel_file,
                        ascend_class or "",
                        ascend_method,
                    )
                    occurrence = {
                        "file": rel_file,
                        "line": node.lineno,
                        "expression": ast.unparse(node),
                        "scope": self.scopes[-1] if self.scopes else "module_import",
                        "guard": " and ".join(self.guards) if self.guards else None,
                    }
                    if key in grouped:
                        grouped[key]["occurrences"].append(occurrence)
                        continue
                    if key in existing:
                        continue
                    grouped[key] = {
                        "relation": "monkey_patch",
                        "risk": "critical",
                        "relationship_verified": True,
                        "verification_status": "verified",
                        "upstream": {
                            "file": new_file,
                            "file_aliases": [old_file, new_file],
                            "module": module,
                            "class": class_name,
                            "method": method,
                        },
                        "ascend": {
                            "file": rel_file,
                            "class": ascend_class,
                            "method": ascend_method,
                            "line": parsed.get("functions", {})
                            .get(ascend_method, {})
                            .get("line"),
                        },
                        "patch_expression": ast.unparse(node),
                        "binding": {
                            "kind": "module_attribute_assignment",
                            "target": f"{module}.{symbol_tail}",
                            "replacement": replacement,
                            "scope": occurrence["scope"],
                            "guard": occurrence["guard"],
                        },
                        "occurrences": [occurrence],
                        "_contract_delta": delta,
                        "_dynamic_candidate": True,
                        "_root_local_callable": ascend_method,
                        "reason": "An exact Ascend callable is assigned to this changed upstream callable attribute.",
                    }
                self.generic_visit(node)

        BindingVisitor().visit(tree)
        if len(grouped) >= limit:
            break

    candidates = list(grouped.values())[:limit]

    # Close one verified patch graph into imported Ascend helpers when the
    # corresponding changed upstream module/function is unique.
    closure_seen: set[tuple[str, str, str]] = set()
    for patch in list(candidates):
        ascend = patch.get("ascend") or {}
        patch_file = str(ascend.get("file") or "")
        root_callable = str(patch.pop("_root_local_callable", "") or "")
        if not patch_file or not root_callable:
            continue
        patch_text = (ascend_root / patch_file).read_text(
            encoding="utf-8", errors="ignore"
        )
        for call_path, downstream_module, symbol, expression in reachable_imported_calls(
            patch_text, root_callable
        ):
            suffix = downstream_module.removeprefix("vllm_ascend.").replace(".", "/")
            matches = [
                file_path
                for file_path in trees[old]
                if file_path.endswith(f"/{suffix}.py")
                and callable_parameter_shape(
                    source_at_commit(vllm_root, old, file_path, source_cache),
                    None,
                    symbol,
                )
                is not None
            ]
            if len(matches) != 1:
                continue
            old_file = matches[0]
            module = file_to_module(old_file)
            details = contract(module, None, symbol)
            if not details:
                continue
            old_file, new_file, delta = details
            closure_key = (new_file, symbol, downstream_module)
            if closure_key in closure_seen:
                continue
            closure_seen.add(closure_key)
            downstream_file = module_to_file(ascend_root, downstream_module)
            if not downstream_file:
                continue
            candidates.append(
                {
                    "relation": "patch_call_closure",
                    "risk": "critical",
                    "relationship_verified": True,
                    "verification_status": "verified",
                    "upstream": {
                        "file": new_file,
                        "file_aliases": [old_file, new_file],
                        "module": module,
                        "class": None,
                        "method": symbol,
                    },
                    "ascend": {
                        "file": downstream_file,
                        "class": None,
                        "method": symbol,
                    },
                    "binding": {
                        "kind": "verified_patch_call_closure",
                        "root_target": (patch.get("binding") or {}).get("target"),
                        "patch_file": patch_file,
                        "call_path": [*call_path, expression],
                        "downstream_module": downstream_module,
                    },
                    "_contract_delta": delta,
                    "_dynamic_candidate": True,
                    "reason": "A changed upstream helper is mirrored by an imported Ascend helper reachable from an exact patch binding.",
                }
            )
            if len(candidates) >= limit:
                return candidates
    return candidates


def discover_import_contract_candidates(
    vllm_root: Path,
    ascend_root: Path,
    old: str,
    new: str,
    changed: set[str],
    limit: int = 1000,
    *,
    exact_only: bool = False,
) -> list[dict[str, Any]]:
    """Find directly imported modules/symbols whose exact contract moved or vanished.

    ``exact_only`` deliberately excludes module string literals and generic
    implementation changes. It retains only a direct import plus a provable
    module relocation/removal, symbol removal, or unique AST-identical rename.
    """

    source_cache: dict[tuple[str, str], str] = {}
    module_cache: dict[tuple[str, str], str | None] = {}
    profile_cache: dict[tuple[str, str, str], str | None] = {}
    candidates: list[dict[str, Any]] = []
    trees = {
        old: python_files_at_commit(vllm_root, old),
        new: python_files_at_commit(vllm_root, new),
    }
    rename_map = exact_file_renames(vllm_root, old, new)

    def module_file(commit: str, module: str) -> str | None:
        key = (commit, module)
        if key not in module_cache:
            module_cache[key] = module_file_from_tree(trees[commit], module)
        return module_cache[key]

    def profile(commit: str, file_path: str, symbol: str) -> str | None:
        key = (commit, file_path, symbol)
        if key not in profile_cache:
            profile_cache[key] = top_level_symbol_profile(
                source_at_commit(vllm_root, commit, file_path, source_cache), symbol
            )
        return profile_cache[key]

    for path in sorted((ascend_root / "vllm_ascend").rglob("*.py")):
        rel_file = path.relative_to(ascend_root).as_posix()
        for imported in imported_vllm_symbols(path):
            if exact_only and (
                imported["kind"] == "module_literal"
                or imported.get("version_guarded") is True
            ):
                # Conditional compatibility branches are handled by the exact
                # patch-binding detector, which can inspect binding direction.
                # String literals are not executable import relationships.
                continue
            module = imported["module"]
            symbol = imported.get("symbol") or ""
            if imported["kind"] == "attribute_chain":
                resolved = resolve_module_reference(trees[old], module)
                if not resolved:
                    continue
                old_file, module, symbol = resolved
                new_file = module_file(new, module)
            else:
                old_file = module_file(old, module)
                new_file = module_file(new, module)
            if not old_file:
                continue

            delta: dict[str, Any] | None = None
            risk = "medium"
            relation = "imported_owner_contract"
            if not new_file:
                relocated_file = rename_map.get(old_file)
                delta = {
                    "kind": "imported_module_removed_or_relocated",
                    "module": module,
                    "symbol": symbol,
                    "old_file": old_file,
                    "new_file": relocated_file,
                    "new_module": file_to_module(relocated_file)
                    if relocated_file
                    else None,
                    "relocation_verified": bool(relocated_file),
                }
                risk = "critical" if imported["kind"] != "module_literal" else "high"
                relation = "module_literal_contract" if imported["kind"] == "module_literal" else "import_contract"
            elif symbol and (is_changed(old_file, changed) or is_changed(new_file, changed)):
                old_profile = profile(old, old_file, symbol)
                new_profile = profile(new, new_file, symbol)
                if old_profile and not new_profile:
                    old_text = source_at_commit(vllm_root, old, old_file, source_cache)
                    new_text = source_at_commit(vllm_root, new, new_file, source_cache)
                    renamed_to = exact_callable_rename(
                        old_text, new_text, None, symbol
                    )
                    delta = {
                        "kind": (
                            "imported_symbol_exactly_renamed"
                            if renamed_to
                            else "imported_symbol_removed_without_exact_destination"
                        ),
                        "module": module,
                        "symbol": symbol,
                        "old_symbol": symbol,
                        "new_symbol": renamed_to,
                        "old_file": old_file,
                        "new_file": new_file,
                        "rename_verified": bool(renamed_to),
                    }
                    risk = "critical"
                    relation = "import_contract"
                elif old_profile and old_profile != new_profile and not exact_only:
                    delta = {
                        "kind": "imported_owner_changed",
                        "module": module,
                        "symbol": symbol,
                    }
            if not delta:
                continue
            candidates.append(
                {
                    "relation": relation,
                    "risk": risk,
                    "relationship_verified": imported["kind"] != "module_literal",
                    "verification_status": "verified"
                    if imported["kind"] != "module_literal"
                    else "candidate",
                    "upstream": {
                        "file": new_file or delta.get("new_file") or old_file,
                        "file_aliases": [
                            item
                            for item in (old_file, new_file, delta.get("new_file"))
                            if item
                        ],
                        "module": module,
                        "symbol": symbol or module,
                    },
                    "ascend": {"file": rel_file, "expression": f"{module}.{symbol}".rstrip(".")},
                    "_contract_delta": delta,
                    "_dynamic_candidate": True,
                    "reason": "Ascend directly imports an upstream module/owner changed by this range.",
                }
            )
            if len(candidates) >= limit:
                return candidates
    return candidates


def discover_copied_contract_candidates(
    ascend_root: Path,
    changed: set[str],
    limit: int = 400,
) -> list[dict[str, Any]]:
    """Find explicit copied/adapted-from contracts whose upstream file changed."""

    marker = re.compile(
        r"(?:adapt(?:ed)?|copied)\s+from[^\n]*"
        r"(vllm/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.py)",
        re.IGNORECASE,
    )
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted((ascend_root / "vllm_ascend").rglob("*.py")):
        rel_file = path.relative_to(ascend_root).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in marker.finditer(text):
            upstream_path = match.group(1)
            key = (upstream_path, rel_file)
            if key in seen or not is_changed(upstream_path, changed):
                continue
            seen.add(key)
            candidates.append(
                {
                    "relation": "copied_contract",
                    "risk": "high",
                    "relationship_verified": True,
                    "verification_status": "verified",
                    "upstream": {"file": upstream_path},
                    "ascend": {"file": rel_file},
                    "_contract_delta": {"kind": "copied_source_changed"},
                    "_dynamic_candidate": True,
                    "reason": "Ascend explicitly declares this code copied/adapted from a changed upstream file.",
                }
            )
            if len(candidates) >= limit:
                return candidates
    return candidates


def has_same_name_module_attribute_binding(text: str, name: str) -> bool:
    """Prove ``module.name = name`` for a local adapted callable."""

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Name) or value.id != name:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(
            isinstance(target, ast.Attribute) and target.attr == name
            for target in targets
        ):
            return True
    return False


def discover_exact_adapted_callable_candidates(
    vllm_root: Path,
    ascend_root: Path,
    old: str,
    new: str,
    changed: set[str],
    limit: int = 400,
) -> list[dict[str, Any]]:
    """Find exact callable deltas in explicitly adapted upstream source files.

    A file-level ``Adapted/Copied from ...vllm/...py`` declaration proves the
    source relationship. A finding is emitted only when the Ascend file still
    defines the old top-level callable and the upstream callable signature
    changed, disappeared, or has one AST-identical rename destination.
    """

    marker = re.compile(
        r"(?:adapt(?:ed)?|copied)\s+from[^\n]*"
        r"(vllm/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.py)",
        re.IGNORECASE,
    )
    source_cache: dict[tuple[str, str], str] = {}
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for path in sorted((ascend_root / "vllm_ascend").rglob("*.py")):
        rel_file = path.relative_to(ascend_root).as_posix()
        ascend_text = path.read_text(encoding="utf-8", errors="ignore")
        ascend_callables = callable_nodes(ascend_text, None)
        if not ascend_callables:
            continue
        for match in marker.finditer(ascend_text):
            upstream_path = match.group(1)
            if not is_changed(upstream_path, changed):
                continue
            old_text = source_at_commit(
                vllm_root, old, upstream_path, source_cache
            )
            new_text = source_at_commit(
                vllm_root, new, upstream_path, source_cache
            )
            old_callables = callable_nodes(old_text, None)
            for name in sorted(set(old_callables) & set(ascend_callables)):
                if not has_same_name_module_attribute_binding(ascend_text, name):
                    continue
                key = (upstream_path, rel_file, name)
                if key in seen:
                    continue
                old_shape = callable_parameter_shape(old_text, None, name)
                new_shape = callable_parameter_shape(new_text, None, name)
                delta: dict[str, Any] | None = None
                if old_shape is not None and new_shape is not None and old_shape != new_shape:
                    delta = {
                        "kind": "callable_signature_changed",
                        "old_shape": old_shape,
                        "new_shape": new_shape,
                        "owner": None,
                        "name": name,
                        "file": upstream_path,
                    }
                elif old_shape is not None and new_shape is None:
                    renamed_to = exact_callable_rename(
                        old_text, new_text, None, name
                    )
                    delta = {
                        "kind": (
                            "callable_renamed"
                            if renamed_to
                            else "callable_removed_without_exact_destination"
                        ),
                        "old_name": name,
                        "new_name": renamed_to,
                        "owner": None,
                        "file": upstream_path,
                        "rename_verified": bool(renamed_to),
                    }
                if not delta:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "relation": "adapted_free_function",
                        "risk": "critical",
                        "relationship_verified": True,
                        "verification_status": "verified",
                        "upstream": {
                            "file": upstream_path,
                            "class": None,
                            "method": name,
                        },
                        "ascend": {
                            "file": rel_file,
                            "class": None,
                            "method": name,
                        },
                        "binding": {
                            "kind": "explicit_adapted_callable",
                            "source_file": upstream_path,
                            "function_contract_verified": True,
                        },
                        "_contract_delta": delta,
                        "_dynamic_candidate": True,
                        "reason": (
                            "Ascend explicitly adapts this upstream file and "
                            "retains the callable whose exact interface changed."
                        ),
                    }
                )
                if len(candidates) >= limit:
                    return candidates
    return candidates


def normalize_ascend_file(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if value.startswith("vllm_ascend/"):
        return value
    return None


def upstream_file(record: dict[str, Any]) -> str | None:
    upstream = record.get("upstream") or {}
    value = upstream.get("file") or record.get("upstream_file")
    return value if isinstance(value, str) else None


def upstream_files(record: dict[str, Any]) -> list[str]:
    upstream = record.get("upstream") or {}
    values: list[str] = []
    for key in ("file", "file_aliases", "upstream_aliases", "aliases"):
        value = upstream.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value if isinstance(item, str))
    top_level_file = record.get("upstream_file")
    if isinstance(top_level_file, str):
        values.append(top_level_file)
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def ascend_file(record: dict[str, Any]) -> str | None:
    ascend = record.get("ascend") or {}
    for key in ("file", "registry_file", "registered_file"):
        value = normalize_ascend_file(ascend.get(key))
        if value:
            return value
    value = normalize_ascend_file(record.get("ascend_file"))
    if value:
        return value
    broad_targets = record.get("ascend_files")
    if isinstance(broad_targets, list):
        for item in broad_targets:
            if isinstance(item, dict):
                value = normalize_ascend_file(item.get("path"))
            else:
                value = normalize_ascend_file(item)
            if value:
                return value
    return None


def ascend_target_files(record: dict[str, Any]) -> list[str]:
    ascend = record.get("ascend") or {}
    values: list[str] = []
    for key in ("file", "registry_file", "registered_file"):
        value = normalize_ascend_file(ascend.get(key))
        if value and value not in values:
            values.append(value)
    top_level_file = normalize_ascend_file(record.get("ascend_file"))
    if top_level_file and top_level_file not in values:
        values.append(top_level_file)
    broad_targets = record.get("ascend_files")
    if isinstance(broad_targets, list):
        for item in broad_targets:
            value = normalize_ascend_file(item.get("path")) if isinstance(item, dict) else normalize_ascend_file(item)
            if value and value not in values:
                values.append(value)
    return values


def record_targets_current_ascend(record: dict[str, Any], ascend_root: Path | None) -> bool:
    if ascend_root is None:
        return True
    targets = ascend_target_files(record)
    if not targets:
        return True
    return all((ascend_root / target).exists() for target in targets)


def verify_current_method_relationship(
    record: dict[str, Any], ascend_root: Path | None
) -> str | None:
    """Prove a direct override or patch assignment in the selected Ascend tree.

    This deliberately verifies only the syntax-level relationship. Patch
    import reachability remains a separate registration-closure review.
    """

    if ascend_root is None:
        return None
    relation = str(record.get("relation") or "")
    ascend = record.get("ascend") or {}
    file_name = normalize_ascend_file(ascend.get("file"))
    if not file_name:
        return None
    path = ascend_root / file_name
    if not path.exists() or path.suffix not in {".py", ".pyi"}:
        return None

    if relation in {"override", "override_candidate"}:
        class_name = ascend.get("class")
        method_name = ascend.get("method") or ascend.get("function")
        if not isinstance(class_name, str) or not isinstance(method_name, str):
            return None
        class_info = parse_python_module(path).get("classes", {}).get(class_name)
        if not class_info or method_name not in class_info.get("methods", {}):
            return None
        if method_name == "__init__":
            text = path.read_text(encoding="utf-8", errors="ignore")
            binding = record.get("binding") or {}
            if not (
                callable_calls_super_method(text, class_name, method_name)
                or (
                    isinstance(binding, dict)
                    and binding.get("construction_binding_verified") is True
                )
            ):
                return None
        base_expression = str(record.get("base_expression") or "")
        base_name = base_expression.split("[", 1)[0].rsplit(".", 1)[-1]
        upstream_class = str((record.get("upstream") or {}).get("class") or "")
        if (
            base_name
            and base_name in class_info.get("bases", [])
            and (not upstream_class or base_name == upstream_class)
        ):
            return (
                f"{class_name} directly inherits {base_name} "
                f"and defines {method_name}"
            )
        return None

    if relation == "monkey_patch":
        expression = str(record.get("patch_expression") or "")
        if not expression:
            return None
        compact_source = re.sub(
            r"\s+", "", path.read_text(encoding="utf-8", errors="ignore")
        )
        if re.sub(r"\s+", "", expression) in compact_source:
            return "exact patch assignment exists in the selected Ascend source"
    if relation == "patch_call_closure":
        binding = record.get("binding") or {}
        if not isinstance(binding, dict):
            return None
        patch_file = normalize_ascend_file(binding.get("patch_file"))
        call_path = binding.get("call_path")
        if not patch_file or not isinstance(call_path, list) or not call_path:
            return None
        patch_path = ascend_root / patch_file
        if not patch_path.exists():
            return None
        patch_text = patch_path.read_text(encoding="utf-8", errors="ignore")
        if all(str(item).split(".")[-1] in patch_text for item in call_path):
            return "exact downstream helper is reachable from a verified patch call path"
    if relation == "adapted_free_function":
        method_name = ascend.get("method") or ascend.get("function")
        binding = record.get("binding") or {}
        if (
            isinstance(method_name, str)
            and isinstance(binding, dict)
            and binding.get("kind") == "explicit_adapted_callable"
            and binding.get("function_contract_verified") is True
            and method_name in callable_nodes(
                path.read_text(encoding="utf-8", errors="ignore"), None
            )
        ):
            return "explicit adapted-from source and exact top-level callable exist"
    return None


def is_low_signal_imported_attribute(record: dict[str, Any]) -> bool:
    if record.get("relation") != "imported_upstream_attribute":
        return False
    upstream = record.get("upstream") or {}
    attribute = upstream.get("attribute")
    if not isinstance(attribute, str):
        return False
    prefix = attribute.split(".", 1)[0]
    return prefix in LOW_SIGNAL_IMPORTED_ATTRIBUTES


def is_changed(path: str | None, changed: set[str]) -> bool:
    if not path:
        return False
    if path in changed:
        return True
    prefix = path.rstrip("/") + "/"
    return any(item.startswith(prefix) for item in changed)


def release_tag_version(value: str | None) -> str | None:
    if not value or not value.startswith("v"):
        return None
    version = value[1:]
    parts = version.split(".")
    if len(parts) < 2 or not all(part[:1].isdigit() for part in parts[:2]):
        return None
    return version


def range_base_ref(value: str | None) -> str | None:
    if not value or ".." not in value:
        return None
    return value.split("..", 1)[0]


def release_guard_records(
    ascend_root: Path,
    old: str,
    new: str,
    ascend_commit: str | None,
) -> list[dict[str, Any]]:
    old_version = release_tag_version(old)
    new_version = release_tag_version(new)
    if not old_version or not new_version or old_version == new_version:
        return []

    old_tag = f"v{old_version}"
    base_ref = range_base_ref(ascend_commit)
    hits: dict[str, list[str]] = {}

    def add_hit(file_path: str, line: str) -> None:
        if not file_path.startswith("vllm_ascend/"):
            return
        if old_version not in line and old_tag not in line:
            return
        hits.setdefault(file_path, [])
        if len(hits[file_path]) < 3:
            hits[file_path].append(line.strip())

    if base_ref:
        text = run_git_text(
            ascend_root,
            [
                "grep",
                "-n",
                "-e",
                "vllm_version_is",
                "-e",
                old_tag,
                base_ref,
                "--",
                "vllm_ascend",
            ],
        )
        prefix = f"{base_ref}:"
        for raw in text.splitlines():
            item = raw[len(prefix) :] if raw.startswith(prefix) else raw
            parts = item.split(":", 2)
            if len(parts) == 3:
                add_hit(parts[0], parts[2])
    else:
        root = ascend_root / "vllm_ascend"
        if root.exists():
            for file_path in root.rglob("*.py"):
                rel = file_path.relative_to(ascend_root).as_posix()
                try:
                    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                for line in lines:
                    if "vllm_version_is" in line or old_tag in line:
                        add_hit(rel, line)

    records: list[dict[str, Any]] = []
    for file_path, examples in sorted(hits.items()):
        records.append(
            {
                "relation": "release_version_guard",
                "risk": "medium",
                "upstream": {
                    "file": ".github/vllm-release-tag.commit",
                    "attribute": f"{old_tag} -> v{new_version}",
                },
                "ascend": {
                    "file": file_path,
                    "expression": "release tag guard or version note",
                },
                "context": (
                    f"Release tag changed from {old_tag} to v{new_version}; "
                    "old-version guard or note in this file needs review."
                ),
                "examples": examples,
            }
        )
    return records


def record_tokens(record: dict[str, Any], table: str) -> list[str]:
    upstream = record.get("upstream") or {}
    ascend = record.get("ascend") or {}
    tokens: list[str] = []

    for key in ("class", "method", "function", "symbol", "attribute", "field", "keyword", "callee"):
        value = upstream.get(key)
        if isinstance(value, str):
            tokens.append(value)

    for key in ("keywords", "missing_keywords"):
        value = upstream.get(key) or ascend.get(key)
        if isinstance(value, list):
            tokens.extend(str(item) for item in value)

    if table == "registration":
        for key in ("registered_file", "registry_file"):
            value = ascend.get(key)
            if isinstance(value, str):
                tokens.append(value)

    if table == "inheritance":
        class_name = record.get("upstream_class")
        if isinstance(class_name, str):
            tokens.append(class_name)
        for key in ("overridden_methods", "child_methods"):
            value = record.get(key)
            if isinstance(value, list):
                tokens.extend(str(item) for item in value if isinstance(item, str))

    if table == "broad":
        value = record.get("symbols")
        if isinstance(value, list):
            tokens.extend(str(item) for item in value if isinstance(item, str))

    return list(dict.fromkeys(token for token in tokens if token))


def diff_text(vllm_root: Path, old: str, new: str, file_path: str, cache: dict[str, str]) -> str:
    if file_path not in cache:
        cache[file_path] = run_git_text(
            vllm_root,
            ["diff", "--unified=0", "--no-ext-diff", old, new, "--", file_path],
        )
    return cache[file_path]


def changed_diff_lines(text: str) -> list[str]:
    return [
        line[1:]
        for line in text.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]


def token_pattern(token: str) -> re.Pattern[str]:
    """Compile an exact identifier/path token matcher.

    Dotted tokens remain exact as a whole.  Bare identifiers can still match
    an attribute leaf (``obj.field``), but never a substring such as
    ``field`` in ``field_count``.
    """

    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])")


def token_matches_changed_lines(token: str, lines: list[str]) -> bool:
    pattern = token_pattern(token)
    return any(pattern.search(line) for line in lines)


def scoped_callable_ast_profile(
    text: str,
    owner: str,
    callable_name: str,
) -> tuple[bool, str | None]:
    """Return a location-independent AST profile for one class method.

    The boolean indicates whether parsing succeeded. A successful parse with
    a ``None`` profile means the requested owner/callable is absent.
    """

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False, None

    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != owner:
            continue
        for child in node.body:
            if (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name == callable_name
            ):
                return True, ast.dump(
                    child, annotate_fields=True, include_attributes=False
                )
        return True, None
    return True, None


def owner_scoped_method_touched(
    vllm_root: Path,
    old: str,
    new: str,
    record: dict[str, Any],
    source_cache: dict[tuple[str, str], str],
) -> bool | None:
    """Compare the exact mapped class method, avoiding cross-class name hits."""

    upstream = record.get("upstream") or {}
    trigger = record.get("trigger") or {}
    owner = trigger.get("owner") or upstream.get("class")
    callable_name = trigger.get("callable") or upstream.get("method")
    if not isinstance(owner, str) or not owner:
        return None
    if not isinstance(callable_name, str) or not callable_name:
        return None

    compared = False
    for file_path in upstream_files(record):
        old_text = source_at_commit(vllm_root, old, file_path, source_cache)
        new_text = source_at_commit(vllm_root, new, file_path, source_cache)
        old_parsed, old_profile = scoped_callable_ast_profile(
            old_text, owner, callable_name
        )
        new_parsed, new_profile = scoped_callable_ast_profile(
            new_text, owner, callable_name
        )
        if not old_parsed or not new_parsed:
            continue
        compared = True
        if old_profile != new_profile:
            return True
    return False if compared else None


def record_diff_proximity_label(
    prediction: Prediction,
    record: dict[str, Any],
    table: str,
    cache: dict[str, str],
) -> str:
    tokens = record_tokens(record, table)
    changed_line_match = False
    hunk_header_match = False
    any_changed_line = False
    for file_path in upstream_files(record):
        text = diff_text(prediction.vllm_root, prediction.old, prediction.new, file_path, cache)
        for line in text.splitlines():
            if line.startswith("@@"):
                if tokens and any(token_pattern(token).search(line) for token in tokens):
                    hunk_header_match = True
                continue
            if not (line.startswith("+") or line.startswith("-")):
                continue
            if line.startswith(("+++", "---")):
                continue
            any_changed_line = True
            changed_line = line[1:]
            if tokens and any(token_pattern(token).search(changed_line) for token in tokens):
                changed_line_match = True
    if changed_line_match:
        return "映射里的函数/字段出现在变更行"
    if hunk_header_match:
        return "变更位于映射函数附近"
    if any_changed_line and not tokens:
        return "上游文件有改动"
    return "仅同文件相关"


def token_touched(
    vllm_root: Path,
    old: str,
    new: str,
    record: dict[str, Any],
    table: str,
    cache: dict[str, str],
    source_cache: dict[tuple[str, str], str] | None = None,
) -> bool:
    if table == "method":
        scoped_result = owner_scoped_method_touched(
            vllm_root,
            old,
            new,
            record,
            source_cache if source_cache is not None else {},
        )
        if scoped_result is not None:
            return scoped_result

    tokens = record_tokens(record, table)
    for file_path in upstream_files(record):
        text = diff_text(vllm_root, old, new, file_path, cache)
        lines = changed_diff_lines(text)
        if not tokens or any(token_matches_changed_lines(token, lines) for token in tokens):
            return True
    return False


def expression_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = expression_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return expression_name(node.func)
    if isinstance(node, ast.Subscript):
        return expression_name(node.value)
    return ""


def all_call_keyword_profiles(text: str) -> dict[str, set[tuple[str, ...]]]:
    if not text:
        return {}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    result: dict[str, set[tuple[str, ...]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        actual = expression_name(node.func)
        if not actual:
            continue
        keywords = tuple(sorted(keyword.arg if keyword.arg is not None else "**" for keyword in node.keywords))
        result.setdefault(actual, set()).add(keywords)
    return result


def call_keyword_profiles(text: str, callee: str) -> set[tuple[str, ...]]:
    if not callee:
        return set()
    profiles = all_call_keyword_profiles(text)
    return profiles.get(callee, set())


def source_at_commit(
    vllm_root: Path,
    commit: str,
    file_path: str,
    cache: dict[tuple[str, str], str],
) -> str:
    key = (commit, file_path)
    if key not in cache:
        cache[key] = run_git_text(vllm_root, ["show", f"{commit}:{file_path}"])
    return cache[key]


def call_keyword_delta(
    vllm_root: Path,
    old: str,
    new: str,
    record: dict[str, Any],
    source_cache: dict[tuple[str, str], str],
    profile_cache: dict[tuple[str, str], dict[str, set[tuple[str, ...]]]] | None = None,
) -> dict[str, Any] | None:
    upstream = record.get("upstream") or {}
    callee = upstream.get("callee") or upstream.get("function")
    if not isinstance(callee, str) or not callee:
        return None
    profile_cache = profile_cache if profile_cache is not None else {}

    def profiles(commit: str, file_path: str) -> set[tuple[str, ...]]:
        key = (commit, file_path)
        if key not in profile_cache:
            profile_cache[key] = all_call_keyword_profiles(
                source_at_commit(vllm_root, commit, file_path, source_cache)
            )
        values = profile_cache[key]
        return values.get(callee, set())

    for file_path in upstream_files(record):
        old_profiles = profiles(old, file_path)
        new_profiles = profiles(new, file_path)
        if old_profiles == new_profiles:
            continue
        old_keywords = set().union(*(set(profile) for profile in old_profiles)) if old_profiles else set()
        new_keywords = set().union(*(set(profile) for profile in new_profiles)) if new_profiles else set()
        added = sorted(new_keywords - old_keywords)
        removed = sorted(old_keywords - new_keywords)
        # A call being duplicated or moved with an identical keyword contract
        # is not a protocol delta.
        if not added and not removed:
            continue
        return {
            "kind": "callee_keyword_delta",
            "callee": callee,
            "file": file_path,
            "added_keywords": added,
            "removed_keywords": removed,
        }
    return None


def field_contract_profiles(text: str, owner: str | None, attribute: str) -> set[str]:
    if not text or not attribute:
        return set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    profiles: set[str] = set()

    def target_matches(target: ast.expr, *, in_owner: bool) -> bool:
        if isinstance(target, ast.Name):
            return in_owner and target.id == attribute
        if isinstance(target, ast.Attribute):
            return in_owner and target.attr == attribute and (
                not owner or (isinstance(target.value, ast.Name) and target.value.id in {"self", "cls"})
            )
        if isinstance(target, (ast.Tuple, ast.List)):
            return any(target_matches(item, in_owner=in_owner) for item in target.elts)
        return False

    class ContractVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.class_stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            self.class_stack.append(node.name)
            self.generic_visit(node)
            self.class_stack.pop()

        def record_assignment(self, node: ast.stmt, targets: list[ast.expr]) -> None:
            current_owner = self.class_stack[-1] if self.class_stack else None
            in_owner = owner is None or current_owner == owner
            if any(target_matches(target, in_owner=in_owner) for target in targets):
                profiles.add(ast.dump(node, annotate_fields=True, include_attributes=False))

        def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
            self.record_assignment(node, list(node.targets))
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
            self.record_assignment(node, [node.target])
            self.generic_visit(node)

        def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
            self.record_assignment(node, [node.target])
            self.generic_visit(node)

    ContractVisitor().visit(tree)
    return profiles


def field_contract_delta(
    prediction: Prediction,
    record: dict[str, Any],
    source_cache: dict[tuple[str, str], str],
) -> bool:
    if not prediction.vllm_root or not prediction.old or not prediction.new:
        return False
    upstream = record.get("upstream") or {}
    raw_attribute = upstream.get("attribute") or upstream.get("field")
    if not isinstance(raw_attribute, str) or not raw_attribute:
        return False
    attribute = raw_attribute.rsplit(".", 1)[-1]
    owner = upstream.get("owner") or upstream.get("class")
    owner = owner if isinstance(owner, str) else None
    for file_path in upstream_files(record):
        old_profiles = field_contract_profiles(
            source_at_commit(prediction.vllm_root, prediction.old, file_path, source_cache),
            owner,
            attribute,
        )
        new_profiles = field_contract_profiles(
            source_at_commit(prediction.vllm_root, prediction.new, file_path, source_cache),
            owner,
            attribute,
        )
        if (old_profiles or new_profiles) and old_profiles != new_profiles:
            return True
    return False


def add_record(
    table: str,
    record: dict[str, Any],
    files_by_table: dict[str, set[str]],
    records_by_table: dict[str, list[dict[str, Any]]],
) -> None:
    targets = ascend_target_files(record)
    if targets:
        files_by_table.setdefault(table, set()).update(targets)
        records_by_table.setdefault(table, []).append(record)


def predict(args: argparse.Namespace) -> Prediction:
    vllm_root = Path(args.vllm_root).resolve()
    ascend_root = Path(args.ascend_root).resolve() if getattr(args, "ascend_root", None) else None
    old = args.old
    new = args.new
    profile = resolve_analysis_profile(getattr(args, "profile", None))
    exact_only = profile == DEFAULT_ANALYSIS_PROFILE
    changed = run_git(vllm_root, ["diff", "--name-only", old, new])
    changed_set = set(changed)
    files_by_table: dict[str, set[str]] = {
        "method": set(),
        "field": set(),
        "call_protocol": set(),
        "registration": set(),
        "inheritance": set(),
        "broad": set(),
        "import_contract": set(),
        "copied_contract": set(),
        "release_guard": set(),
    }
    records_by_table: dict[str, list[dict[str, Any]]] = {
        "method": [],
        "field": [],
        "call_protocol": [],
        "registration": [],
        "inheritance": [],
        "broad": [],
        "import_contract": [],
        "copied_contract": [],
        "release_guard": [],
    }
    diff_cache: dict[str, str] = {}
    source_cache: dict[tuple[str, str], str] = {}
    signature_cache: dict[tuple[str, str], dict[str, Any]] = {}
    call_profile_cache: dict[tuple[str, str], dict[str, set[tuple[str, ...]]]] = {}
    range_prediction = Prediction(
        files_by_table,
        records_by_table,
        changed,
        vllm_root,
        old,
        new,
        ascend_root,
        profile,
    )

    specs = enabled_table_specs(profile)
    for table in ("method", "field", "call_protocol", "inheritance", "broad"):
        spec = specs.get(table)
        if not spec:
            continue
        for source_record in active_mappings(str(spec["file"])):
            record = dict(source_record)
            if not record_targets_current_ascend(record, ascend_root):
                continue
            if table == "method":
                relationship_evidence = verify_current_method_relationship(
                    record, ascend_root
                )
                if relationship_evidence:
                    record["_relationship_verified_current"] = True
                    record["_relationship_evidence"] = relationship_evidence
                else:
                    relation = str(record.get("relation") or "")
                    ascend = record.get("ascend") or {}
                    method = ascend.get("method") or ascend.get("function")
                    if relation == "override" and method == "__init__":
                        record["_relationship_rejected_current"] = True
                        continue
                    if exact_only:
                        continue
            if not any(is_changed(up_file, changed_set) for up_file in upstream_files(record)):
                continue

            if table == "method":
                delta = upstream_callable_contract_delta(
                    range_prediction,
                    record,
                    signature_cache,
                    source_cache,
                )
                if delta:
                    record.setdefault("_contract_delta_name", delta)
                else:
                    if exact_only:
                        continue
                    if not token_touched(
                        vllm_root,
                        old,
                        new,
                        record,
                        table,
                        diff_cache,
                        source_cache,
                    ):
                        continue
            elif table == "call_protocol":
                delta = call_keyword_delta(
                    vllm_root,
                    old,
                    new,
                    record,
                    source_cache,
                    call_profile_cache,
                )
                if delta:
                    record["_contract_delta"] = delta
                # Without a real delta, retain only exact changed-line callee
                # matches as low-confidence review evidence.
                upstream = record.get("upstream") or {}
                callee = upstream.get("callee") or upstream.get("function")
                touched = False
                if isinstance(callee, str) and callee:
                    for file_path in upstream_files(record):
                        lines = changed_diff_lines(diff_text(vllm_root, old, new, file_path, diff_cache))
                        if token_matches_changed_lines(callee, lines):
                            touched = True
                            break
                if not delta and not touched:
                    continue
            elif not token_touched(
                vllm_root,
                old,
                new,
                record,
                table,
                diff_cache,
                source_cache,
            ):
                continue
            add_record(table, record, files_by_table, records_by_table)

    if ascend_root:
        for record in discover_changed_override_candidates(
            vllm_root, ascend_root, old, new
        ):
            add_record("method", record, files_by_table, records_by_table)
        for record in discover_patch_binding_candidates(
            vllm_root, ascend_root, old, new, changed_set
        ):
            add_record("method", record, files_by_table, records_by_table)
        for record in discover_import_contract_candidates(
            vllm_root,
            ascend_root,
            old,
            new,
            changed_set,
            exact_only=exact_only,
        ):
            add_record("import_contract", record, files_by_table, records_by_table)
        if exact_only:
            for record in discover_exact_adapted_callable_candidates(
                vllm_root, ascend_root, old, new, changed_set
            ):
                add_record("method", record, files_by_table, records_by_table)
        else:
            for record in discover_copied_contract_candidates(ascend_root, changed_set):
                add_record("copied_contract", record, files_by_table, records_by_table)

    base_files = set().union(
        files_by_table["method"],
        files_by_table["field"],
        files_by_table["call_protocol"],
        files_by_table["inheritance"],
        files_by_table["import_contract"],
        files_by_table["copied_contract"],
    )
    registration_spec = specs.get("registration")
    registration_records = (
        active_mappings(str(registration_spec["file"])) if registration_spec else []
    )
    for record in registration_records:
        if not record_targets_current_ascend(record, ascend_root):
            continue
        ascend = record.get("ascend") or {}
        registered_file = normalize_ascend_file(ascend.get("registered_file"))
        direct_trigger = is_changed(upstream_file(record), changed_set)
        closure_trigger = bool(registered_file and registered_file in base_files)
        if direct_trigger or closure_trigger:
            add_record("registration", record, files_by_table, records_by_table)

    ascend_root_text = getattr(args, "ascend_root", None)
    if ascend_root_text and not exact_only:
        for record in release_guard_records(
            Path(ascend_root_text).resolve(),
            old,
            new,
            getattr(args, "ascend_commit", None),
        ):
            add_record("release_guard", record, files_by_table, records_by_table)

    return Prediction(
        files_by_table,
        records_by_table,
        changed,
        vllm_root,
        old,
        new,
        ascend_root,
        profile,
    )


def actual_ascend_files(ascend_root: Path, commit: str) -> set[str]:
    if ".." in commit:
        files = run_git(ascend_root, ["diff", "--name-only", commit, "--", "vllm_ascend"])
        return {
            item
            for item in files
            if item.startswith("vllm_ascend/") and file_has_runtime_diff(ascend_root, commit, item)
        }
    files = run_git(ascend_root, ["show", "--name-only", "--format=", commit, "--", "vllm_ascend"])
    return {
        item
        for item in files
        if item.startswith("vllm_ascend/") and file_has_runtime_diff(ascend_root, commit, item)
    }


def file_has_runtime_diff(ascend_root: Path, commit: str, file_path: str) -> bool:
    if not file_path.endswith(".py"):
        return True
    if ".." in commit:
        diff_lines = run_git(ascend_root, ["diff", "--unified=0", commit, "--", file_path])
    else:
        diff_lines = run_git(
            ascend_root,
            ["show", "--format=", "--unified=0", commit, "--", file_path],
        )
    for line in diff_lines:
        if not line or line.startswith(("+++", "---")):
            continue
        if not (line.startswith("+") or line.startswith("-")):
            continue
        text = line[1:].strip()
        if not text or text.startswith("#"):
            continue
        return True
    return False


def actual_changed_files(ascend_root: Path, commit: str) -> set[str]:
    if ".." in commit:
        return set(run_git(ascend_root, ["diff", "--name-only", commit]))
    return set(run_git(ascend_root, ["show", "--name-only", "--format=", commit]))


def metrics(predicted: set[str], actual: set[str]) -> dict[str, Any]:
    hits = sorted(predicted & actual)
    misses = sorted(actual - predicted)
    false_positives = sorted(predicted - actual)
    precision = len(hits) / len(predicted) if predicted else 0.0
    recall = len(hits) / len(actual) if actual else 0.0
    return {
        "predicted": len(predicted),
        "actual": len(actual),
        "hits": hits,
        "misses": misses,
        "false_positives": false_positives,
        "precision": precision,
        "recall": recall,
    }


def threshold_sensitivity_summary(
    evidence: dict[str, dict[str, Any]],
    actual: set[str],
    *,
    current_threshold: int = CORE_REVIEW_SCORE_THRESHOLD,
) -> str:
    if not actual:
        return ""
    thresholds = [value for value in (55, 60, 70, 80, 90) if value > current_threshold]
    first_miss: tuple[int, dict[str, Any]] | None = None
    last_safe: tuple[int, dict[str, Any]] | None = None
    for threshold in thresholds:
        files = {
            file_path
            for file_path, info in evidence.items()
            if info.get("core_eligible") and int(info.get("score") or 0) >= threshold
        }
        result = metrics(files, actual)
        if result["misses"]:
            first_miss = (threshold, result)
            break
        last_safe = (threshold, result)
    if first_miss:
        threshold, result = first_miss
        if last_safe:
            safe_threshold, safe_result = last_safe
            return (
                f"阈值说明: 提高到 {safe_threshold} 仍不漏报，候选 {safe_result['predicted']} 个；"
                f"再提高到 {threshold} 会漏报 {len(result['misses'])} 个历史实际修改。"
            )
        return (
            f"阈值说明: 当前阈值以召回优先；提高到 {threshold} 会漏报 "
            f"{len(result['misses'])} 个历史实际修改，因此暂不建议上调。"
        )
    if last_safe:
        threshold, result = last_safe
        return (
            f"阈值说明: 提高到 {threshold} 在本样例中仍不漏报，候选可降到 "
            f"{result['predicted']} 个；是否上调需要继续看更多历史样例。"
        )
    return ""


def core_review_files(
    evidence: dict[str, dict[str, Any]],
    threshold: int = CORE_REVIEW_SCORE_THRESHOLD,
) -> set[str]:
    return {
        file_path
        for file_path, info in evidence.items()
        if info.get("core_eligible") and int(info.get("score") or 0) >= threshold
    }


def parsed_file_at_commit(
    vllm_root: Path,
    commit: str,
    file_path: str,
    cache: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    key = (commit, file_path)
    if key not in cache:
        text = run_git_text(vllm_root, ["show", f"{commit}:{file_path}"])
        cache[key] = parse_python_text(text) if text else {"classes": {}, "functions": {}}
    return cache[key]


def parsed_signature(parsed: dict[str, Any], class_name: str | None, method_name: str | None) -> str:
    if not method_name:
        return ""
    if class_name:
        return (
            parsed.get("classes", {})
            .get(class_name, {})
            .get("methods", {})
            .get(method_name, {})
            .get("signature", "")
        )
    return parsed.get("functions", {}).get(method_name, {}).get("signature", "")


def upstream_callable_contract_delta(
    prediction: Prediction,
    record: dict[str, Any],
    cache: dict[tuple[str, str], dict[str, Any]],
    source_cache: dict[tuple[str, str], str] | None = None,
) -> str | None:
    if not prediction.vllm_root or not prediction.old or not prediction.new:
        return None
    upstream = record.get("upstream") or {}
    method_name = upstream.get("method") or upstream.get("function")
    if not method_name:
        return None
    class_name = upstream.get("class")
    sources = source_cache if source_cache is not None else {}
    for file_path in upstream_files(record):
        old_text = source_at_commit(
            prediction.vllm_root, prediction.old, file_path, sources
        )
        new_text = source_at_commit(
            prediction.vllm_root, prediction.new, file_path, sources
        )
        old_shape = callable_parameter_shape(old_text, class_name, method_name)
        new_shape = callable_parameter_shape(new_text, class_name, method_name)
        if old_shape is not None and new_shape is None:
            if class_name and not owner_class_exists(new_text, str(class_name)):
                record["_contract_delta"] = {
                    "kind": "callable_owner_removed_or_consolidated",
                    "old_owner": class_name,
                    "name": method_name,
                    "file": file_path,
                    "destination": None,
                }
                return "owner_removed"
            renamed_to = exact_callable_rename(
                old_text, new_text, class_name, str(method_name)
            )
            if renamed_to:
                record["_contract_delta"] = {
                    "kind": "callable_renamed",
                    "old_name": method_name,
                    "new_name": renamed_to,
                    "owner": class_name,
                    "file": file_path,
                    "rename_verified": True,
                }
                return "renamed"
            return "removed_or_renamed"
        if old_shape is None and new_shape is not None:
            record["_contract_delta"] = {
                "kind": "callable_introduced",
                "new_shape": new_shape,
                "owner": class_name,
                "name": method_name,
                "file": file_path,
            }
            return "introduced"
        if old_shape is not None and new_shape is not None:
            if old_shape != new_shape:
                record["_contract_delta"] = {
                    "kind": "callable_signature_changed",
                    "old_shape": old_shape,
                    "new_shape": new_shape,
                    "owner": class_name,
                    "name": method_name,
                    "file": file_path,
                }
                return "signature_changed"
            continue
        old_parsed = parsed_file_at_commit(prediction.vllm_root, prediction.old, file_path, cache)
        new_parsed = parsed_file_at_commit(prediction.vllm_root, prediction.new, file_path, cache)
        old_sig = parsed_signature(old_parsed, class_name, method_name)
        new_sig = parsed_signature(new_parsed, class_name, method_name)
        if old_sig and not new_sig:
            return "removed_or_renamed"
        if old_sig and new_sig and old_sig != new_sig:
            return "signature_changed"
    return None


def callable_parameter_shape(
    text: str,
    class_name: str | None,
    method_name: str | None,
) -> tuple[Any, ...] | None:
    """Return a stable callable parameter contract, excluding annotations."""

    if not text or not method_name:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    scope: list[ast.stmt] = tree.body
    if class_name:
        owner = next(
            (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name),
            None,
        )
        if owner is None:
            return None
        scope = owner.body
    node = next(
        (
            item
            for item in scope
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == method_name
        ),
        None,
    )
    if node is None:
        return None
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    if class_name and positional and positional[0].arg in {"self", "cls"}:
        positional = positional[1:]
    required_from = len(positional) - len(args.defaults)
    positional_shape = tuple(
        (arg.arg, index < required_from) for index, arg in enumerate(positional)
    )
    kwonly_shape = tuple(
        (arg.arg, default is None)
        for arg, default in zip(args.kwonlyargs, args.kw_defaults)
    )
    return (
        positional_shape,
        kwonly_shape,
        args.vararg.arg if args.vararg else None,
        args.kwarg.arg if args.kwarg else None,
    )


def callable_nodes(
    text: str, class_name: str | None
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    if not text:
        return {}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    scope: list[ast.stmt] = tree.body
    if class_name:
        owner = next(
            (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name),
            None,
        )
        if owner is None:
            return {}
        scope = owner.body
    return {
        node.name: node
        for node in scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def callable_implementation_fingerprint(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    """Fingerprint an implementation while deliberately excluding its name."""

    return repr(
        (
            isinstance(node, ast.AsyncFunctionDef),
            ast.dump(node.args, annotate_fields=True, include_attributes=False),
            ast.dump(
                ast.Module(body=node.body, type_ignores=[]),
                annotate_fields=True,
                include_attributes=False,
            ),
        )
    )


def exact_callable_rename(
    old_text: str,
    new_text: str,
    class_name: str | None,
    old_name: str,
) -> str | None:
    """Resolve a rename only when one new callable has an identical AST body."""

    old_node = callable_nodes(old_text, class_name).get(old_name)
    if old_node is None:
        return None
    fingerprint = callable_implementation_fingerprint(old_node)
    matches = [
        name
        for name, node in callable_nodes(new_text, class_name).items()
        if name != old_name and callable_implementation_fingerprint(node) == fingerprint
    ]
    return matches[0] if len(matches) == 1 else None


def owner_class_exists(text: str, class_name: str | None) -> bool:
    if not text or not class_name:
        return False
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    return any(
        isinstance(node, ast.ClassDef) and node.name == class_name
        for node in tree.body
    )


def ascend_callable_matches_new_contract(
    prediction: Prediction,
    record: dict[str, Any],
    source_cache: dict[tuple[str, str], str],
) -> bool:
    """Confirm that the current Ascend override already matches the new API."""

    if not (
        prediction.ascend_root
        and prediction.vllm_root
        and prediction.new
    ):
        return False
    upstream = record.get("upstream") or {}
    ascend = record.get("ascend") or {}
    upstream_name = upstream.get("method") or upstream.get("function")
    ascend_name = ascend.get("method") or ascend.get("function") or upstream_name
    upstream_class = upstream.get("class")
    ascend_class = ascend.get("class")
    ascend_path = normalize_ascend_file(ascend.get("file"))
    if not ascend_path or not (prediction.ascend_root / ascend_path).exists():
        return False
    old_shape = None
    new_shape = None
    for file_path in upstream_files(record):
        old_shape = callable_parameter_shape(
            source_at_commit(
                prediction.vllm_root, prediction.old, file_path, source_cache
            ),
            upstream_class if isinstance(upstream_class, str) else None,
            upstream_name if isinstance(upstream_name, str) else None,
        )
        new_shape = callable_parameter_shape(
            source_at_commit(
                prediction.vllm_root, prediction.new, file_path, source_cache
            ),
            upstream_class if isinstance(upstream_class, str) else None,
            upstream_name if isinstance(upstream_name, str) else None,
        )
        if new_shape is not None:
            break
    ascend_text = (prediction.ascend_root / ascend_path).read_text(
        encoding="utf-8", errors="ignore"
    )
    ascend_shape = callable_parameter_shape(
        ascend_text,
        ascend_class if isinstance(ascend_class, str) else None,
        ascend_name if isinstance(ascend_name, str) else None,
    )
    if new_shape is not None and parameter_shape_accepts(new_shape, ascend_shape):
        return True
    return bool(
        ascend_name == "__init__"
        and isinstance(ascend_name, str)
        and callable_forwards_kwargs_to_super(
            ascend_text,
            ascend_class if isinstance(ascend_class, str) else None,
            ascend_name,
        )
        and optional_parameter_additions_only(old_shape, new_shape)
    )


def parameter_shape_accepts(
    expected: tuple[Any, ...], actual: tuple[Any, ...] | None
) -> bool:
    """Whether ``actual`` accepts ``expected`` plus only optional extensions."""

    if actual is None:
        return False
    expected_pos, expected_kw, expected_vararg, expected_kwarg = expected
    actual_pos, actual_kw, actual_vararg, actual_kwarg = actual
    if len(actual_pos) < len(expected_pos):
        return False
    for (expected_name, expected_required), (actual_name, actual_required) in zip(
        expected_pos, actual_pos
    ):
        if expected_name != actual_name or (not expected_required and actual_required):
            return False
    if any(required for _, required in actual_pos[len(expected_pos) :]):
        return False
    actual_kw_map = dict(actual_kw)
    for name, required in expected_kw:
        if name not in actual_kw_map:
            # ``**kwargs`` proves only that the call is syntactically accepted.
            # It does not prove that the implementation consumes or forwards
            # a renamed keyword, so it is insufficient for a compatibility
            # dismissal. The narrowly safe constructor-forwarding case is
            # handled separately by ``ascend_callable_matches_new_contract``.
            return False
        if not required and actual_kw_map[name]:
            return False
    expected_kw_names = {name for name, _ in expected_kw}
    if any(required for name, required in actual_kw if name not in expected_kw_names):
        return False
    if expected_vararg is not None and actual_vararg is None:
        return False
    if expected_kwarg is not None and actual_kwarg is None:
        return False
    return True


def upstream_callable_signature_changed(
    prediction: Prediction,
    record: dict[str, Any],
    cache: dict[tuple[str, str], dict[str, Any]],
) -> bool:
    """Backward-compatible predicate used by external imports/tests."""

    return upstream_callable_contract_delta(prediction, record, cache) is not None


def records_for_file(prediction: Prediction, file_path: str) -> list[tuple[str, dict[str, Any]]]:
    records: list[tuple[str, dict[str, Any]]] = []
    for table, table_records in prediction.records_by_table.items():
        for record in table_records:
            if file_path in ascend_target_files(record):
                records.append((table, record))
    return records


def changed_upstream_file_for_record(prediction: Prediction, record: dict[str, Any]) -> str:
    changed = set(prediction.changed_vllm_files)
    for file_path in upstream_files(record):
        if is_changed(file_path, changed):
            return file_path
    upstream = record.get("upstream") or {}
    return str(upstream.get("file") or upstream.get("registry_file") or "")


def _legacy_review_evidence_for_file(
    prediction: Prediction,
    file_path: str,
    signature_cache: dict[tuple[str, str], dict[str, Any]],
    diff_cache: dict[str, str],
) -> dict[str, Any]:
    score = 0
    labels: list[str] = []
    examples: list[str] = []
    proximities: list[str] = []
    tables: set[str] = set()

    def add(points: int, label: str, record: dict[str, Any]) -> None:
        nonlocal score
        score = max(score, points)
        if label not in labels:
            labels.append(label)
        if len(examples) < 3:
            upstream = record.get("upstream") or {}
            method_name = (
                upstream.get("method")
                or upstream.get("function")
                or upstream.get("callee")
                or upstream.get("attribute")
                or upstream.get("field")
            )
            upstream_file = changed_upstream_file_for_record(prediction, record)
            if upstream_file or method_name:
                example = "::".join(part for part in [upstream_file, method_name] if part)
                if example not in examples:
                    examples.append(example)

    for table, record in records_for_file(prediction, file_path):
        tables.add(table)
        proximity = record_diff_proximity_label(prediction, record, table, diff_cache)
        if proximity not in proximities:
            proximities.append(proximity)
        relation = str(record.get("relation") or "")
        risk = str(record.get("risk") or "")
        if table == "method" and upstream_callable_signature_changed(prediction, record, signature_cache):
            add(100, "上游函数签名已变化", record)
        elif table == "call_protocol":
            add(90 if risk == "critical" else 80, "调用协议变化", record)
        elif table == "method" and relation in {
            "same_name_protocol",
            "monkey_patch",
            "override",
            "patch_call_closure",
            "adapted_free_function",
        }:
            add(60 if risk in {"critical", "high"} else 50, "函数实现需要对比", record)
        elif table == "field" and is_low_signal_imported_attribute(record):
            add(20, "通用模块包装观察", record)
        elif table == "field":
            add(45 if risk == "critical" else 35, "字段/注册需要确认", record)
        elif table == "registration":
            add(25, "注册入口需要确认", record)
        elif table == "release_guard":
            add(50, "版本兼容判断需要确认", record)
        else:
            add(20, "保留观察", record)

    tier = review_tier_zh({"labels": labels, "score": score})
    return {
        "score": score,
        "tier": tier,
        "labels": labels,
        "examples": examples,
        "proximities": proximities,
        "tables": sorted(tables),
    }


def review_evidence_for_file(
    prediction: Prediction,
    file_path: str,
    signature_cache: dict[tuple[str, str], dict[str, Any]],
    diff_cache: dict[str, str],
) -> dict[str, Any]:
    """Rank causal evidence without promoting unverified table guesses."""

    score = 0
    labels: list[str] = []
    examples: list[str] = []
    proximities: list[str] = []
    tables: set[str] = set()
    core_eligible = False
    relationship_seen = False
    contract_seen = False
    example_score = -1
    source_cache: dict[tuple[str, str], str] = {}

    def add(points: int, label: str, record: dict[str, Any], *, core: bool = False) -> None:
        nonlocal score, core_eligible, relationship_seen, contract_seen, example_score
        score = max(score, points)
        core_eligible = core_eligible or core
        relationship_seen = relationship_seen or relationship_verified(record)
        contract_seen = contract_seen or bool(record.get("_contract_delta")) or core
        if label not in labels:
            labels.append(label)
        if points < example_score:
            return
        if points > example_score:
            examples.clear()
            example_score = points
        if len(examples) >= 3:
            return
        upstream = record.get("upstream") or {}
        contract = record.get("_contract_delta") or {}
        if contract.get("kind") == "imported_module_removed_or_relocated":
            old_module = contract.get("module")
            new_module = contract.get("new_module")
            symbol = contract.get("symbol")
            relocation = f"{old_module} -> {new_module}" if new_module else str(old_module)
            example = "::".join(str(part) for part in (relocation, symbol) if part)
            if example and example not in examples:
                examples.append(example)
            return
        if contract.get("kind") in {
            "imported_symbol_exactly_renamed",
            "imported_symbol_removed_without_exact_destination",
        }:
            old_symbol = contract.get("old_symbol")
            new_symbol = contract.get("new_symbol")
            rename = f"{old_symbol} -> {new_symbol}" if new_symbol else str(old_symbol)
            example = "::".join(
                str(part)
                for part in (contract.get("module"), rename)
                if part
            )
            if example and example not in examples:
                examples.append(example)
            return
        name = (
            upstream.get("method")
            or upstream.get("function")
            or upstream.get("callee")
            or upstream.get("attribute")
            or upstream.get("field")
            or upstream.get("symbol")
            or record.get("upstream_class")
        )
        upstream_path = changed_upstream_file_for_record(prediction, record)
        example = "::".join(str(part) for part in (upstream_path, name) if part)
        if example and example not in examples:
            examples.append(example)

    for table, record in records_for_file(prediction, file_path):
        tables.add(table)
        proximity = record_diff_proximity_label(prediction, record, table, diff_cache)
        if proximity not in proximities:
            proximities.append(proximity)
        relation = str(record.get("relation") or "")
        risk = str(record.get("risk") or "")

        if table == "method":
            dynamic_delta = record.get("_contract_delta")
            delta = upstream_callable_contract_delta(
                prediction, record, signature_cache, source_cache
            )
            if isinstance(dynamic_delta, dict):
                kind = str(dynamic_delta.get("kind") or "")
                if kind == "override_signature_delta":
                    delta = "signature_changed"
                elif kind == "overridden_method_introduced":
                    delta = "introduced"
                elif kind in {
                    "base_class_removed_or_relocated",
                    "overridden_method_removed_or_relocated",
                }:
                    delta = "removed_or_renamed"
            contract_seen = contract_seen or bool(delta)
            if relation in {"same_name_protocol", "same_name_candidate"}:
                add(20, "same-name candidate only", record)
            elif (
                delta
                and relation
                in {
                    "override",
                    "override_candidate",
                    "monkey_patch",
                    "patch_call_closure",
                    "adapted_free_function",
                }
                and relationship_verified(record)
            ):
                if delta in {"signature_changed", "introduced"} and ascend_callable_matches_new_contract(
                    prediction, record, source_cache
                ):
                    add(25, "already compatible override signature", record)
                else:
                    label = (
                        "upstream callable signature changed"
                        if delta == "signature_changed"
                        else "upstream callable introduced with incompatible override"
                        if delta == "introduced"
                        else "upstream callable exactly renamed"
                        if delta == "renamed"
                        else "upstream callable owner removed or consolidated"
                        if delta == "owner_removed"
                        else "upstream callable removed or renamed"
                    )
                    add(100, label, record, core=True)
            elif delta and relation in {
                "override",
                "override_candidate",
                "monkey_patch",
                "patch_call_closure",
                "adapted_free_function",
            }:
                add(40, "unverified method contract delta", record)
            elif relation in {
                "override",
                "override_candidate",
                "monkey_patch",
                "patch_call_closure",
                "adapted_free_function",
            }:
                add(40 if risk in {"critical", "high"} else 30, "method relationship review", record)
            else:
                add(20, "unverified method candidate", record)
        elif table == "call_protocol":
            has_delta = isinstance(record.get("_contract_delta"), dict)
            if has_delta and relationship_verified(record):
                add(90 if risk == "critical" else 80, "verified call keyword delta", record, core=True)
            elif has_delta:
                add(40, "unverified call keyword delta", record)
            else:
                add(20, "unverified call protocol candidate", record)
        elif table == "field":
            if is_low_signal_imported_attribute(record):
                add(10, "low-signal imported attribute", record)
                continue
            has_delta = field_contract_delta(prediction, record, source_cache)
            if has_delta and relationship_verified(record):
                add(70 if risk == "critical" else 60, "verified upstream field contract delta", record, core=True)
            elif has_delta:
                add(40, "unverified upstream field contract delta", record)
            else:
                add(25, "field relationship review", record)
        elif table == "registration":
            add(25, "registration closure review", record)
        elif table == "import_contract":
            contract = record.get("_contract_delta") or {}
            kind = str(contract.get("kind") or "")
            if kind == "imported_module_removed_or_relocated" and relationship_verified(record):
                label = (
                    "verified imported module relocated"
                    if contract.get("relocation_verified")
                    else "verified imported module removed without exact destination"
                )
                add(95, label, record, core=True)
            elif kind == "imported_symbol_exactly_renamed" and relationship_verified(record):
                add(95, "verified imported callable exactly renamed", record, core=True)
            elif (
                kind == "imported_symbol_removed_without_exact_destination"
                and relationship_verified(record)
            ):
                add(
                    95,
                    "verified imported symbol removed without exact destination",
                    record,
                    core=True,
                )
            elif kind == "imported_owner_changed" and relationship_verified(record):
                add(50, "changed directly imported upstream owner", record)
            else:
                add(30, "module path literal review", record)
        elif table == "copied_contract":
            add(45, "changed explicitly copied upstream source", record)
        elif table == "inheritance":
            add(30 if record.get("overridden_methods") else 20, "inheritance secondary review", record)
        elif table == "broad":
            add(10, "broad mapping observation", record)
        elif table == "release_guard":
            add(50, "release compatibility guard", record, core=True)
        else:
            add(20, "review candidate", record)

    # A file may contain both an already-compatible constructor and a separate
    # incompatible callable. Do not render the compatible label beside the
    # high-confidence contract break: without symbol association that wording
    # is contradictory and can be mistaken as dismissing the real finding.
    if core_eligible and "already compatible override signature" in labels:
        labels.remove("already compatible override signature")

    tier = review_tier_zh({"labels": labels, "score": score})
    compatible_only = "already compatible override signature" in labels and not core_eligible
    action = "dismiss" if compatible_only else "review"
    confidence = "high" if core_eligible else "medium" if score >= 40 else "low"
    return {
        "score": score,
        "core_eligible": core_eligible,
        "tier": tier,
        "labels": labels,
        "examples": examples,
        "proximities": proximities,
        "tables": sorted(tables),
        "confidence": confidence,
        "action": action,
        "action_gate": {
            "relationship_verified": relationship_seen,
            "contract_changed": contract_seen,
            "runtime_reachable": "unknown",
            "version_lane_matches": "unknown",
        },
    }


def review_evidence_by_file(prediction: Prediction) -> dict[str, dict[str, Any]]:
    signature_cache: dict[tuple[str, str], dict[str, Any]] = {}
    diff_cache: dict[str, str] = {}
    return {
        file_path: review_evidence_for_file(prediction, file_path, signature_cache, diff_cache)
        for file_path in sorted(prediction.files)
    }


def format_proximity_summary(info: dict[str, Any], limit: int = 3) -> str:
    values = [
        str(value)
        for value in info.get("proximities", [])
        if str(value).strip()
    ]
    if not values:
        return ""
    visible = values[:limit]
    summary = "、".join(visible)
    if len(values) > limit:
        summary += f" 等 {len(values)} 类"
    return f"命中方式：{summary}"


def render_review_rows(
    rows: list[tuple[str, dict[str, Any]]],
    actual: set[str] | None,
    limit: int,
    heading_prefix: str = "###",
) -> tuple[list[str], int]:
    lines: list[str] = []
    current_tier = ""
    shown = 0
    for file_path, info in rows:
        if shown >= limit:
            break
        tier = str(info["tier"])
        if tier != current_tier:
            current_tier = tier
            if lines and lines[-1]:
                lines.append("")
            lines.append(f"{heading_prefix} {tier}")
            lines.append("")
        hit = ""
        if actual is not None:
            hit = "；历史 PR 实际修改" if file_path in actual else "；补充线索，历史 PR 未改"
        labels = "、".join(info["labels"])
        examples = "；".join(info["examples"])
        detail = f"{labels}{hit}"
        if examples:
            detail += f"；证据：{examples}"
        proximity = format_proximity_summary(info)
        if proximity:
            detail += f"；{proximity}"
        gate = info.get("action_gate") or {}
        detail += (
            f"；置信度：{info.get('confidence', 'low')}；action：{info.get('action', 'review')}"
            f"；gate[关系={gate.get('relationship_verified', False)},"
            f"契约={gate.get('contract_changed', False)},"
            f"运行={gate.get('runtime_reachable', 'unknown')},"
            f"版本={gate.get('version_lane_matches', 'unknown')}]"
        )
        lines.append(f"- `{file_path}` - {detail}")
        shown += 1
    return lines, shown


def render_review_order(prediction: Prediction, actual: set[str] | None = None, limit: int = 18) -> str:
    evidence = review_evidence_by_file(prediction)
    rows = sorted(evidence.items(), key=lambda item: (-item[1]["score"], item[0]))
    lines: list[str] = []
    lines.append("## 建议处理顺序")
    lines.append("")

    if actual is not None:
        lines.append(
            f"先看分数不低于 {CORE_REVIEW_SCORE_THRESHOLD} 的优先复核项；"
            "低分项保留为补充线索，用于排查遗漏或继续完善映射表。"
        )
        lines.append("")
        hit_rows = [row for row in rows if row[0] in actual]
        extra_rows = [row for row in rows if row[0] not in actual]
        lines.append("### 历史 PR 实际改过，脚本也命中的文件")
        lines.append("")
        if hit_rows:
            rendered, _ = render_review_rows(hit_rows, actual, len(hit_rows), heading_prefix="####")
            lines.extend(rendered)
        else:
            lines.append("- 无")
        lines.append("")
        lines.append("### 补充线索，历史 PR 未改")
        lines.append("")
        if extra_rows:
            rendered, shown = render_review_rows(
                extra_rows,
                actual,
                max(0, limit - min(len(hit_rows), limit)),
                heading_prefix="####",
            )
            if not rendered:
                rendered, shown = render_review_rows(
                    extra_rows,
                    actual,
                    min(8, len(extra_rows)),
                    heading_prefix="####",
                )
            lines.extend(rendered)
            if len(extra_rows) > shown:
                lines.append("")
                lines.append(f"- 其余 {len(extra_rows) - shown} 个候选保留在后面的候选清单中。")
        else:
            lines.append("- 无")
        lines.append("")
        return "\n".join(lines)

    rendered, shown = render_review_rows(rows, actual, limit)
    lines.extend(rendered)
    if len(rows) > shown:
        lines.append("")
        lines.append(f"- 其余 {len(rows) - shown} 个低优先级候选保留在候选清单中。")
    lines.append("")
    return "\n".join(lines)


def render_prediction(prediction: Prediction) -> str:
    lines: list[str] = []
    lines.append("# vLLM Ascend Main2Main 风险预测")
    lines.append("")
    lines.append(f"- analysis profile: `{prediction.profile}`")
    lines.append(f"- 上游变更文件数: {len(prediction.changed_vllm_files)}")
    lines.append(f"- 预测需关注的 vllm-ascend 文件: {len(prediction.files)}")
    lines.append("")
    lines.append("## 证据来源统计")
    lines.append("")
    for table, files in prediction.files_by_table.items():
        if not files and not prediction.records_by_table.get(table):
            continue
        lines.append(
            f"- {table_label_zh(table)}: "
            f"{len(files)} 个文件，{len(prediction.records_by_table[table])} 条证据"
        )
    lines.append("")
    lines.append(render_review_order(prediction).rstrip())
    lines.append("")
    lines.append("## 候选文件清单")
    lines.append("")
    for file_path in sorted(prediction.files):
        sources = [
            table_label_zh(table)
            for table, files in prediction.files_by_table.items()
            if file_path in files
        ]
        lines.append(f"- `{file_path}` ({', '.join(sources)})")
    lines.append("")
    return "\n".join(lines)


def table_label_zh(table: str) -> str:
    return {
        "method": "函数/方法映射",
        "field": "字段/注册映射",
        "call_protocol": "调用协议映射",
        "registration": "注册入口映射",
        "import_contract": "导入/符号契约发现",
        "copied_contract": "复制代码契约发现",
        "release_guard": "版本兼容判断",
    }.get(table, table)


def candidate_bucket_zh(info: dict[str, Any]) -> str:
    return review_tier_zh(info)


def candidate_bucket_rank(bucket: str) -> int:
    return {
        "接口签名优先": 0,
        "调用协议优先": 1,
        "函数实现复核": 2,
        "字段/注册确认": 3,
        "版本兼容跟随": 4,
        "注册入口确认": 5,
        "观察": 6,
    }.get(bucket, 9)


def candidate_bucket_counts(
    evidence: dict[str, dict[str, Any]],
    files: list[str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for file_path in files:
        info = evidence.get(file_path)
        if not info:
            continue
        bucket = candidate_bucket_zh(info)
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def candidate_bucket_summary(
    evidence: dict[str, dict[str, Any]],
    files: list[str],
) -> str:
    counts = candidate_bucket_counts(evidence, files)
    if not counts:
        return "无"
    parts = [
        f"{bucket} {counts[bucket]}"
        for bucket in sorted(counts, key=candidate_bucket_rank)
        if counts.get(bucket)
    ]
    return "；".join(parts)


def candidate_proximity_summary(
    evidence: dict[str, dict[str, Any]],
    files: list[str],
) -> str:
    counts: dict[str, int] = {}
    rank = {
        "映射里的函数/字段出现在变更行": 0,
        "变更位于映射函数附近": 1,
        "映射符号出现在变更行": 0,
        "变更落在映射函数附近": 1,
        "上游文件有改动": 2,
        "仅同文件相关": 3,
        "同文件变更上下文命中": 3,
    }
    for file_path in files:
        info = evidence.get(file_path) or {}
        seen: set[str] = set()
        for value in info.get("proximities", []):
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            counts[text] = counts.get(text, 0) + 1
    if not counts:
        return "无"
    return "；".join(
        f"{label} {counts[label]}"
        for label in sorted(counts, key=lambda item: (rank.get(item, 99), item))
    )


def candidate_noise_guidance(
    evidence: dict[str, dict[str, Any]],
    files: list[str],
) -> list[str]:
    if not files:
        return []
    direct_or_nearby = 0
    same_file_only = 0
    no_proximity = 0
    strong_labels = {
        "映射里的函数/字段出现在变更行",
        "变更位于映射函数附近",
        "映射符号出现在变更行",
        "变更落在映射函数附近",
    }
    same_file_labels = {
        "仅同文件相关",
        "同文件变更上下文命中",
        "上游文件有改动",
    }
    for file_path in files:
        info = evidence.get(file_path) or {}
        proximities = {
            str(item).strip()
            for item in info.get("proximities", [])
            if str(item).strip()
        }
        if proximities & strong_labels:
            direct_or_nearby += 1
        elif proximities and proximities <= same_file_labels:
            same_file_only += 1
        else:
            no_proximity += 1
    lines = [
        f"- 降噪摘要: 补充线索中 {direct_or_nearby} 个直接碰到映射符号或映射函数附近，"
        f"{same_file_only} 个只是同文件相关，{no_proximity} 个缺少更细粒度命中方式。"
    ]
    if direct_or_nearby:
        lines.append(
            "- 复核建议: 直接碰到映射符号或映射函数附近的线索优先看；"
            "如果多次出现在历史样例但最终未修改，需要把映射收窄到更具体的函数、字段或调用协议。"
        )
    if same_file_only:
        lines.append(
            "- 降级建议: 仅同文件相关的线索不建议直接分配修改任务；"
            "先保留为观察或映射表维护项，等找到具体函数、字段、注册入口或调用链后再升级。"
        )
    return lines


def evidence_family_zh(info: dict[str, Any]) -> str:
    labels = set(info.get("labels") or [])
    if labels & {
        "upstream callable signature changed",
        "upstream callable introduced with incompatible override",
        "upstream callable exactly renamed",
        "upstream callable owner removed or consolidated",
        "upstream callable removed or renamed",
        "verified imported module relocated",
        "verified imported module removed without exact destination",
        "verified call keyword delta",
        "unverified call keyword delta",
        "unverified call protocol candidate",
    }:
        return "接口/调用协议"
    if labels & {
        "method relationship review",
        "same-name candidate only",
        "unverified method candidate",
        "already compatible override signature",
        "changed directly imported upstream owner",
        "changed explicitly copied upstream source",
    }:
        return "函数实现"
    if labels & {
        "verified upstream field contract delta",
        "unverified upstream field contract delta",
        "field relationship review",
        "low-signal imported attribute",
    }:
        return "字段/注册"
    if "release compatibility guard" in labels:
        return "版本兼容"
    if "registration closure review" in labels:
        return "注册入口"
    if labels & {"上游函数签名已变化", "调用协议变化"}:
        return "接口/调用协议"
    if labels & {"函数实现需要对比", "函数实现需要复核"}:
        return "函数实现"
    if labels & {"字段/注册需要确认", "字段/配置需要确认"}:
        return "字段/注册"
    if "版本兼容判断需要确认" in labels:
        return "版本兼容"
    if labels & {"注册入口需要确认", "补丁注册链需要确认"}:
        return "注册入口"
    return "观察"


def evidence_family_rank(family: str) -> int:
    return {
        "接口/调用协议": 0,
        "函数实现": 1,
        "字段/注册": 2,
        "版本兼容": 3,
        "注册入口": 4,
        "观察": 5,
    }.get(family, 9)


def review_tier_zh(info: dict[str, Any]) -> str:
    family = evidence_family_zh(info)
    if family == "接口/调用协议":
        score = int(info.get("score") or 0)
        return "接口签名优先" if score >= 95 else "调用协议优先"
    if family == "函数实现":
        return "函数实现复核"
    if family == "字段/注册":
        return "字段/注册确认"
    if family == "版本兼容":
        return "版本兼容跟随"
    if family == "注册入口":
        return "注册入口确认"
    return "观察"


def evidence_family_summary(
    evidence: dict[str, dict[str, Any]],
    files: list[str] | set[str],
) -> str:
    counts: dict[str, int] = {}
    for file_path in files:
        info = evidence.get(file_path) or {}
        family = evidence_family_zh(info)
        counts[family] = counts.get(family, 0) + 1
    if not counts:
        return "无"
    return "；".join(
        f"{family} {counts[family]}"
        for family in sorted(counts, key=evidence_family_rank)
    )


INTERFACE_FUNCTION_FAMILIES = {"接口/调用协议", "函数实现"}
VERSION_FOLLOWUP_FAMILIES = {"版本兼容"}


def files_for_evidence_families(
    evidence: dict[str, dict[str, Any]],
    files: set[str],
    families: set[str],
) -> set[str]:
    return {
        file_path
        for file_path in files
        if evidence_family_zh(evidence.get(file_path) or {}) in families
    }


def review_files_for_evidence_families(
    evidence: dict[str, dict[str, Any]],
    families: set[str],
    threshold: int = CORE_REVIEW_SCORE_THRESHOLD,
) -> set[str]:
    return {
        file_path
        for file_path, info in evidence.items()
        if evidence_family_zh(info) in families
        and info.get("core_eligible")
        and int(info.get("score") or 0) >= threshold
    }


def render_grouped_candidate_files(
    evidence: dict[str, dict[str, Any]],
    files: list[str],
    *,
    per_group_limit: int = 12,
) -> list[str]:
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for file_path in sorted(files):
        info = evidence.get(file_path)
        if not info:
            grouped.setdefault("注册链或观察", []).append((file_path, {}))
            continue
        grouped.setdefault(candidate_bucket_zh(info), []).append((file_path, info))

    lines: list[str] = []
    for bucket in sorted(grouped, key=candidate_bucket_rank):
        rows = sorted(grouped[bucket], key=lambda item: (-(item[1].get("score") or 0), item[0]))
        lines.append(f"### {bucket}")
        lines.append("")
        shown = 0
        for file_path, info in rows[:per_group_limit]:
            labels = "、".join(info.get("labels") or [])
            examples = "；".join(info.get("examples") or [])
            detail = labels or "保留观察"
            if examples:
                detail += f"；证据：{examples}"
            proximity = format_proximity_summary(info)
            if proximity:
                detail += f"；{proximity}"
            lines.append(f"- `{file_path}` - {detail}")
            shown += 1
        if len(rows) > shown:
            lines.append(f"- 其余 {len(rows) - shown} 个同类候选暂不展开。")
        lines.append("")
    return lines


def render_evaluation(prediction: Prediction, actual: set[str], changed_files: set[str] | None = None) -> str:
    result = metrics(prediction.files, actual)
    changed_files = changed_files or set()
    non_runtime_sample = bool(changed_files and not actual)
    evidence = review_evidence_by_file(prediction)
    core_files = core_review_files(evidence)
    core_result = metrics(core_files, actual)
    interface_function_files = review_files_for_evidence_families(
        evidence,
        INTERFACE_FUNCTION_FAMILIES,
    )
    interface_function_actual = files_for_evidence_families(
        evidence,
        actual,
        INTERFACE_FUNCTION_FAMILIES,
    )
    interface_function_result = metrics(interface_function_files, interface_function_actual)
    version_followup_files = review_files_for_evidence_families(
        evidence,
        VERSION_FOLLOWUP_FAMILIES,
    )
    version_followup_actual = files_for_evidence_families(
        evidence,
        actual,
        VERSION_FOLLOWUP_FAMILIES,
    )
    version_followup_result = metrics(version_followup_files, version_followup_actual)
    lines: list[str] = []
    lines.append("# vLLM Ascend Main2Main 映射评估")
    lines.append("")
    lines.append(f"- 上游变更文件数: {len(prediction.changed_vllm_files)}")
    lines.append(f"- 预测需关注的 vllm-ascend 文件: {result['predicted']}")
    lines.append(f"- 历史 PR 实际修改的运行时代码文件: {result['actual']}")
    if non_runtime_sample:
        lines.append("- 评估状态: 准确率/召回率不适用")
        lines.append(
            "- 原因: 这个历史 PR 只修改 CI、文档或工具配置，没有修改 vllm_ascend 运行时代码。"
        )
        lines.append(f"- 未验证候选: {result['predicted']}")
    else:
        lines.append(f"- 已命中历史修改: {len(result['hits'])}")
        lines.append(f"- 漏报历史修改: {len(result['misses'])}")
        lines.append(f"- 补充线索，历史 PR 未改: {len(result['false_positives'])}")
        lines.append(f"- 准确率: {result['precision']:.3f}")
        lines.append(f"- 召回率: {result['recall']:.3f}")
        lines.append(
            f"- 优先复核项: {len(core_files)} "
            f"(阈值 {CORE_REVIEW_SCORE_THRESHOLD}；命中 {len(core_result['hits'])}；"
            f"漏报 {len(core_result['misses'])}；"
            f"准确率 {core_result['precision']:.3f}；召回率 {core_result['recall']:.3f})"
        )
        lines.append(
            f"- 优先复核类型: {evidence_family_summary(evidence, core_files)}"
        )
        if core_result["hits"]:
            lines.append(
                f"- 优先复核命中类型: {evidence_family_summary(evidence, core_result['hits'])}"
            )
        core_side_files = set(core_result["false_positives"])
        if core_side_files:
            lines.append(
                f"- 优先复核中的补充线索类型: {evidence_family_summary(evidence, core_side_files)}"
            )
        if interface_function_files or interface_function_actual:
            lines.append(
                f"- 接口/函数优先复核: {len(interface_function_files)} "
                f"(历史接口/函数命中 {len(interface_function_result['hits'])}；"
                f"漏报 {len(interface_function_result['misses'])}；"
                f"准确率 {interface_function_result['precision']:.3f}；"
                f"召回率 {interface_function_result['recall']:.3f})"
            )
        if version_followup_files or version_followup_actual:
            lines.append(
                f"- 版本兼容跟随: {len(version_followup_files)} "
                f"(历史版本跟随命中 {len(version_followup_result['hits'])}；"
                f"漏报 {len(version_followup_result['misses'])})"
            )
        if result["false_positives"]:
            lines.append(
                f"- 补充线索分层: {candidate_bucket_summary(evidence, result['false_positives'])}"
            )
            lines.append(
                f"- 变更命中方式: {candidate_proximity_summary(evidence, result['false_positives'])}"
            )
            lines.extend(candidate_noise_guidance(evidence, result["false_positives"]))
            lines.append(
                "- 结果解读: 召回率优先看历史实际修改是否被抓住；"
                "“补充线索”是同一批上游改动下的相关复核项，"
                "不等同于已经确认需要改代码。"
            )
        if core_result["misses"]:
            lines.append(
                "- 优先复核解读: 当前阈值会漏掉历史实际修改，"
                "需要补映射或降低该类证据的降级规则。"
            )
        elif actual:
            lines.append(
                "- 优先复核解读: 当前阈值没有漏掉历史实际修改，"
                "开发者可先看优先复核项，再按需看补充线索。"
            )
        threshold_summary = threshold_sensitivity_summary(evidence, actual)
        if threshold_summary:
            lines.append(f"- {threshold_summary}")
        if "版本兼容" in evidence_family_summary(evidence, core_files):
            lines.append(
                "- 类型解读: 版本兼容项用于提示 release tag 或版本判断需要跟随，"
                "不等同于接口签名或函数实现 break；首轮修复可先看接口/函数优先复核。"
            )
    lines.append("")
    if non_runtime_sample:
        lines.append("## 历史 PR 改动文件")
        lines.append("")
        for item in sorted(changed_files):
            lines.append(f"- `{item}`")
        lines.append("")
    lines.append(render_review_order(prediction, actual).rstrip())
    lines.append("")
    sections = (
        (("已命中历史修改", "hits"), ("漏报历史修改", "misses"), ("未验证候选", "false_positives"))
        if non_runtime_sample
        else (("已命中历史修改", "hits"), ("漏报历史修改", "misses"), ("补充线索，历史 PR 未改", "false_positives"))
    )
    for title, key in sections:
        lines.append(f"## {title}")
        lines.append("")
        values = result[key]
        if values:
            if key == "false_positives":
                if not non_runtime_sample:
                    lines.append(
                        "这些文件没有出现在该历史 PR 的实际修改中，"
                        "但和同一批上游改动存在映射关系；先看分层和命中方式，再决定是否需要继续复核。"
                    )
                    lines.append("")
                lines.extend(render_grouped_candidate_files(evidence, values))
            else:
                for item in values:
                    lines.append(f"- `{item}`")
        else:
            lines.append("- 无")
        lines.append("")
    return "\n".join(lines)


def validate(args: argparse.Namespace) -> Validation:
    vllm_root = Path(args.vllm_root).resolve()
    ascend_root = Path(args.ascend_root).resolve()
    profile = resolve_analysis_profile(getattr(args, "profile", None))
    specs = enabled_table_specs(profile)
    method_total, method_stale = validate_method_table(vllm_root, ascend_root)
    if "field" in specs:
        field_total, field_stale = validate_field_table(vllm_root, ascend_root)
    else:
        field_total, field_stale = 0, []
    if "call_protocol" in specs:
        call_total, call_stale = validate_call_protocol_table(vllm_root, ascend_root)
    else:
        call_total, call_stale = 0, []
    candidates = (
        discover_missing_method_candidates(
            vllm_root,
            ascend_root,
            limit=args.max_candidates,
        )
        if profile != DEFAULT_ANALYSIS_PROFILE
        else []
    )
    return Validation(
        method_total=method_total,
        method_stale=method_stale,
        field_total=field_total,
        field_stale=field_stale,
        call_protocol_total=call_total,
        call_protocol_stale=call_stale,
        missing_method_candidates=candidates,
    )


def endpoint_label(endpoint: dict[str, Any]) -> str:
    file_path = endpoint.get("file")
    class_name = endpoint.get("class")
    callable_name = (
        endpoint.get("method")
        or endpoint.get("function")
        or endpoint.get("callee")
        or endpoint.get("symbol")
        or endpoint.get("attribute")
        or endpoint.get("field")
    )
    if class_name and callable_name:
        return f"{file_path}::{class_name}.{callable_name}"
    if callable_name:
        return f"{file_path}::{callable_name}"
    return str(file_path)


def unique_validation_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}
    for item in items:
        key = (
            endpoint_label(item.get("upstream") or {}),
            endpoint_label(item.get("ascend") or {}),
            tuple(item.get("problems") or []),
        )
        if key not in grouped:
            grouped[key] = dict(item, _duplicate_count=1)
        else:
            grouped[key]["_duplicate_count"] += 1
    return list(grouped.values())


def duplicate_suffix(item: dict[str, Any]) -> str:
    count = int(item.get("_duplicate_count") or 1)
    return f"; 同类 {count} 条" if count > 1 else ""


def field_validation_maintenance_kind(item: dict[str, Any]) -> str:
    problems = "；".join(str(problem) for problem in item.get("problems") or [])
    if "Ascend 文件不存在" in problems:
        return "mapping_cleanup"
    if "上游文件不存在" in problems:
        return "relocate_upstream"
    return "review"


def render_validation_items(items: list[dict[str, Any]], limit: int = 40) -> list[str]:
    lines: list[str] = []
    for item in unique_validation_items(items)[:limit]:
        upstream = item.get("upstream") or {}
        ascend = item.get("ascend") or {}
        lines.append(
            "- "
            f"`{endpoint_label(upstream)}` -> "
            f"`{endpoint_label(ascend)}`; "
            f"{'; '.join(item.get('problems') or [])}"
            f"{duplicate_suffix(item)}"
        )
    if not lines:
        lines.append("- 无")
    return lines


def render_validation(validation: Validation) -> str:
    lines: list[str] = []
    lines.append("# vLLM Ascend Main2Main 映射表校验")
    lines.append("")
    lines.append("## 摘要")
    lines.append("")
    field_cleanup_items = [
        item
        for item in validation.field_stale
        if field_validation_maintenance_kind(item) == "mapping_cleanup"
    ]
    field_relocate_items = [
        item
        for item in validation.field_stale
        if field_validation_maintenance_kind(item) == "relocate_upstream"
    ]
    field_review_items = [
        item
        for item in validation.field_stale
        if field_validation_maintenance_kind(item) == "review"
    ]
    lines.append(f"- 函数/方法映射: {validation.method_total}")
    lines.append(f"- 失效的函数/方法映射: {len(validation.method_stale)}")
    lines.append(f"- 字段/注册映射: {validation.field_total}")
    lines.append(f"- 字段/注册映射维护项: {len(validation.field_stale)}")
    if validation.field_stale:
        lines.append(
            f"- 其中: 可清理记录 {len(field_cleanup_items)}；"
            f"需重新定位上游路径 {len(field_relocate_items)}；"
            f"其他需复核 {len(field_review_items)}"
        )
    lines.append(f"- 调用协议映射: {validation.call_protocol_total}")
    lines.append(f"- 失效的调用协议映射: {len(validation.call_protocol_stale)}")
    lines.append(f"- 待补方法映射候选: {len(validation.missing_method_candidates)}")
    if validation.field_stale:
        lines.append(
            "- 说明: 字段/注册维护项多为已删除或移动的文件；预测阶段会跳过不存在的 Ascend 目标，"
            "不会把这些记录直接当成当前风险。"
        )
    lines.append("")
    lines.append("## 失效的函数/方法映射")
    lines.append("")
    if validation.method_stale:
        lines.extend(render_validation_items(validation.method_stale))
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 字段/注册映射维护项")
    lines.append("")
    if validation.field_stale:
        lines.append("这些记录需要后续清理或重新定位；当前预测会自动跳过不存在的 Ascend 目标。")
        lines.append("")
        if field_relocate_items:
            lines.append("### 需要重新定位上游路径")
            lines.append("")
            lines.append(
                "Ascend 文件仍存在，但映射表指向的上游文件不存在或已移动；"
                "需要确认当前代码是否已经改用新路径，并同步更新映射表。"
            )
            lines.append("")
            lines.extend(render_validation_items(field_relocate_items))
            lines.append("")
        if field_cleanup_items:
            lines.append("### 可清理记录")
            lines.append("")
            lines.append(
                "Ascend 目标文件已经不存在或已移动；这些记录通常可以从映射表删除，"
                "或按新的 Ascend 文件重新建映射。"
            )
            lines.append("")
            lines.extend(render_validation_items(field_cleanup_items))
            lines.append("")
        if field_review_items:
            lines.append("### 其他需复核")
            lines.append("")
            lines.extend(render_validation_items(field_review_items))
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 失效的调用协议映射")
    lines.append("")
    if validation.call_protocol_stale:
        lines.extend(render_validation_items(validation.call_protocol_stale))
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 待补方法映射候选")
    lines.append("")
    if validation.missing_method_candidates:
        for item in validation.missing_method_candidates[:80]:
            upstream = item.get("upstream") or {}
            ascend = item.get("ascend") or {}
            lines.append(
                "- "
                f"`{endpoint_label(upstream)}` -> "
                f"`{endpoint_label(ascend)}` "
                f"(line {ascend.get('line')})"
            )
    else:
        lines.append("- 无")
    lines.append("")
    return "\n".join(lines)


def write_or_print(text: str, output: str | None) -> None:
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        print(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vllm-root", required=True)
    common.add_argument("--ascend-root", required=True)
    common.add_argument("--old", required=True)
    common.add_argument("--new", required=True)
    common.add_argument("--output")
    common.add_argument(
        "--profile",
        choices=sorted(analysis_profiles()),
        default=default_analysis_profile(),
        help="Analysis scope; exact-contracts is the low-noise default.",
    )

    predict_parser = sub.add_parser("predict", parents=[common])
    predict_parser.set_defaults(func=lambda ns: write_or_print(render_prediction(predict(ns)), ns.output))

    evaluate_parser = sub.add_parser("evaluate", parents=[common])
    evaluate_parser.add_argument("--ascend-commit", required=True)

    def evaluate_command(ns: argparse.Namespace) -> None:
        prediction = predict(ns)
        ascend_root = Path(ns.ascend_root).resolve()
        actual = actual_ascend_files(ascend_root, ns.ascend_commit)
        changed = actual_changed_files(ascend_root, ns.ascend_commit)
        write_or_print(render_evaluation(prediction, actual, changed), ns.output)

    evaluate_parser.set_defaults(func=evaluate_command)

    validate_parser = sub.add_parser(
        "validate",
        help="Validate mapping tables against current vLLM and vllm-ascend checkouts.",
    )
    validate_parser.add_argument("--vllm-root", required=True)
    validate_parser.add_argument("--ascend-root", required=True)
    validate_parser.add_argument("--output")
    validate_parser.add_argument("--json-output")
    validate_parser.add_argument("--max-candidates", type=int, default=200)
    validate_parser.add_argument(
        "--profile",
        choices=sorted(analysis_profiles()),
        default=default_analysis_profile(),
    )

    def validate_command(ns: argparse.Namespace) -> None:
        result = validate(ns)
        if ns.json_output:
            Path(ns.json_output).write_text(
                json.dumps(
                    {
                        "method_total": result.method_total,
                        "method_stale": result.method_stale,
                        "field_total": result.field_total,
                        "field_stale": result.field_stale,
                        "call_protocol_total": result.call_protocol_total,
                        "call_protocol_stale": result.call_protocol_stale,
                        "missing_method_candidates": result.missing_method_candidates,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        write_or_print(render_validation(result), ns.output)

    validate_parser.set_defaults(func=validate_command)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
