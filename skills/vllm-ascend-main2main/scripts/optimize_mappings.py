#!/usr/bin/env python3
"""Deterministically clean the main2main mapping tables.

The canonical source JSON files are intentionally never overwritten.  The
manifest declares a separate ``*.optimized.json`` output for every table that
this script owns.  Use ``--check`` in CI to verify that those generated files
are current.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


JsonObject = dict[str, Any]
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
EXTERNAL_ATTRIBUTE_PREFIXES = {
    "F",
    "logger",
    "math",
    "nn",
    "np",
    "numpy",
    "tl",
    "torch",
    "triton",
}
CANONICAL_OWNER_FILES = {
    "CommonAttentionMetadata": "vllm/v1/attention/backend.py",
    "VllmConfig": "vllm/config/vllm.py",
}
CONSTRUCTOR_METHODS = {"__init__", "__new__"}


def read_json(path: Path) -> JsonObject:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def render_json(value: JsonObject) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_sha(root: Path | None) -> str | None:
    if root is None or not root.is_dir():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip()
    return value if len(value) == 40 else None


def _risk(record: Mapping[str, Any]) -> int:
    return RISK_ORDER.get(str(record.get("risk", "low")), -1)


def _method_rank(record: Mapping[str, Any]) -> tuple[int, int]:
    """Prefer exact verified evidence when duplicate method mappings tie."""

    verification = record.get("verification")
    verified = (
        record.get("relationship_verified") is True
        and isinstance(verification, Mapping)
        and verification.get("status") == "verified"
    )
    return (_risk(record), int(verified))


def _cap_risk(risk: str, maximum: str) -> str:
    if RISK_ORDER.get(risk, 0) > RISK_ORDER[maximum]:
        return maximum
    return risk


def _unique(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _counter(items: Iterable[str | None]) -> dict[str, int]:
    return dict(sorted(Counter(item for item in items if item).items()))


def _candidate(
    record: JsonObject,
    *,
    confidence: str,
    reason: str,
    action: str = "review",
    status: str = "candidate",
) -> None:
    record["relationship_verified"] = False
    record["action"] = action
    record["confidence"] = confidence
    record["verification"] = {"status": status, "reason": reason}


def _missing_mapping_files(
    record: Mapping[str, Any],
    vllm_root: Path | None,
    ascend_root: Path | None,
) -> list[str]:
    """Return definitively missing source files for a pinned validation pair."""

    missing: list[str] = []
    upstream_file = record.get("upstream", {}).get("file")
    ascend_file = record.get("ascend", {}).get("file")
    if (
        isinstance(upstream_file, str)
        and upstream_file
        and vllm_root is not None
        and vllm_root.is_dir()
        and not (vllm_root / upstream_file).exists()
    ):
        missing.append(f"vllm:{upstream_file}")
    if (
        isinstance(ascend_file, str)
        and ascend_file
        and ascend_root is not None
        and ascend_root.is_dir()
        and not (ascend_root / ascend_file).exists()
    ):
        missing.append(f"vllm_ascend:{ascend_file}")
    return missing


def _callable_exists(
    root: Path | None,
    side: Mapping[str, Any],
    cache: dict[Path, tuple[dict[str, set[str]], set[str]] | None],
) -> bool | None:
    """Return a tri-state exact callable lookup for a pinned Python source."""

    if root is None or not root.is_dir():
        return None
    file_name = side.get("file")
    callable_name = side.get("method") or side.get("function")
    if not isinstance(file_name, str) or not isinstance(callable_name, str):
        return None
    path = root / file_name
    if not path.exists() or path.suffix not in {".py", ".pyi"}:
        return False
    if path not in cache:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            cache[path] = None
        else:
            classes: dict[str, set[str]] = {}
            symbols: set[str] = set()

            def assignment_names(target: ast.expr) -> set[str]:
                if isinstance(target, ast.Name):
                    return {target.id}
                if isinstance(target, ast.Attribute):
                    return {target.attr}
                if isinstance(target, (ast.Tuple, ast.List)):
                    return set().union(
                        *(assignment_names(item) for item in target.elts)
                    )
                return set()

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.add(node.name)
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    symbols.add(node.name)
                    members = {
                        child.name
                        for child in node.body
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    }
                    for child in ast.walk(node):
                        if isinstance(child, ast.Assign):
                            for target in child.targets:
                                members.update(assignment_names(target))
                        elif isinstance(child, (ast.AnnAssign, ast.AugAssign)):
                            members.update(assignment_names(child.target))
                        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            members.add(child.name)
                    classes[node.name] = members
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.add(node.name)
                elif isinstance(node, ast.ImportFrom):
                    symbols.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name != "*"
                    )
                elif isinstance(node, ast.Import):
                    symbols.update(
                        alias.asname or alias.name.split(".", 1)[0]
                        for alias in node.names
                    )
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        symbols.update(assignment_names(target))
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                    symbols.update(assignment_names(node.target))
            cache[path] = (classes, symbols)
    parsed = cache[path]
    if parsed is None:
        return None
    classes, symbols = parsed
    class_name = side.get("class")
    if isinstance(class_name, str) and class_name:
        return callable_name in classes.get(class_name, set())
    return callable_name in symbols


def _provenance(
    record: JsonObject,
    *,
    table: str,
    indexes: Sequence[int],
    relations: Sequence[str],
) -> None:
    record["provenance"] = {
        "source_table": table,
        "source_record_indexes": list(indexes),
        "source_relations": sorted(set(relations)),
    }


def _method_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    upstream = record.get("upstream", {})
    ascend = record.get("ascend", {})
    return (
        record.get("relation"),
        upstream.get("file"),
        upstream.get("class"),
        upstream.get("method"),
        ascend.get("file"),
        ascend.get("class"),
        ascend.get("method"),
    )


def _method_occurrence(record: Mapping[str, Any], index: int) -> JsonObject:
    upstream = record.get("upstream", {})
    ascend = record.get("ascend", {})
    return {
        "source_record_index": index,
        "source_relation": record.get("relation"),
        "original_risk": record.get("risk"),
        "upstream_line": upstream.get("line"),
        "ascend_line": ascend.get("line"),
        "base_expression": record.get("base_expression"),
    }


def _method_trigger(record: Mapping[str, Any]) -> JsonObject | None:
    """Build an owner-scoped trigger for class method relationships.

    A bare method name such as ``verify_and_update_config`` is not a stable
    trigger because unrelated classes in the same upstream file can define the
    same method. Preserve the class owner in the published mapping so the
    predictor can compare the exact callable rather than OR-matching class and
    method tokens across the whole file diff.
    """

    upstream = record.get("upstream", {})
    owner = upstream.get("class")
    callable_name = upstream.get("method")
    if not isinstance(owner, str) or not owner:
        return None
    if not isinstance(callable_name, str) or not callable_name:
        return None
    return {
        "kind": "owner_scoped_callable",
        "owner": owner,
        "callable": callable_name,
        "qualified_name": f"{owner}.{callable_name}",
    }


def optimize_method_mappings(
    mappings: Sequence[Mapping[str, Any]],
    vllm_root: Path | None = None,
    ascend_root: Path | None = None,
) -> tuple[list[JsonObject], JsonObject]:
    groups: dict[tuple[Any, ...], list[tuple[int, JsonObject, str]]] = defaultdict(list)
    converted = 0
    stale_records = 0
    stale_file_records = 0
    stale_callable_records = 0
    stale_paths: Counter[str] = Counter()
    stale_callables: Counter[str] = Counter()
    callable_cache: dict[Path, tuple[dict[str, set[str]], set[str]] | None] = {}

    for index, source in enumerate(mappings):
        missing = _missing_mapping_files(source, vllm_root, ascend_root)
        if missing:
            stale_records += 1
            stale_file_records += 1
            stale_paths.update(missing)
            continue
        missing_callables: list[str] = []
        upstream_exists = _callable_exists(
            vllm_root, source.get("upstream", {}), callable_cache
        )
        ascend_exists = _callable_exists(
            ascend_root, source.get("ascend", {}), callable_cache
        )
        if upstream_exists is False:
            upstream = source.get("upstream", {})
            missing_callables.append(
                "vllm:"
                + str(upstream.get("method") or upstream.get("function"))
            )
        if ascend_exists is False:
            ascend = source.get("ascend", {})
            missing_callables.append(
                "vllm_ascend:"
                + str(ascend.get("method") or ascend.get("function"))
            )
        if missing_callables:
            stale_records += 1
            stale_callable_records += 1
            stale_callables.update(missing_callables)
            continue
        record = copy.deepcopy(dict(source))
        source_relation = str(record.get("relation", ""))
        if source_relation == "same_name_protocol":
            record["relation"] = "same_name_candidate"
            record["risk"] = "low"
            _candidate(
                record,
                confidence="low",
                reason=(
                    "name equality alone does not prove inheritance, patching, "
                    "call binding, or semantic compatibility"
                ),
            )
            converted += 1
        elif (
            record.get("relationship_verified") is True
            and isinstance(record.get("verification"), dict)
            and record["verification"].get("status") == "verified"
        ):
            record["action"] = "review"
            record["confidence"] = "high"
        else:
            confidence = (
                "high"
                if source_relation in {"override", "monkey_patch"}
                else "medium"
            )
            _candidate(
                record,
                confidence=confidence,
                reason="static relationship candidate requires validation at the selected source SHAs",
            )
        trigger = _method_trigger(record)
        if trigger is not None:
            record["trigger"] = trigger
        groups[_method_key(record)].append((index, record, source_relation))

    optimized: list[JsonObject] = []
    for entries in groups.values():
        winner_index, winner, _ = max(entries, key=lambda item: _method_rank(item[1]))
        indexes = [index for index, _, _ in entries]
        relations = [relation for _, _, relation in entries]
        winner["occurrences"] = _unique(
            _method_occurrence(record, index) for index, record, _ in entries
        )
        _provenance(
            winner,
            table="main2main_method_mapping.json",
            indexes=indexes,
            relations=relations,
        )
        # Keep the selected record's line/signature while preserving every
        # original source row in occurrences/provenance.
        if winner_index not in indexes:  # pragma: no cover - defensive only
            raise AssertionError("deduplication selected an unknown record")
        optimized.append(winner)

    optimized.sort(key=lambda record: tuple(str(v or "") for v in _method_key(record)))
    stats = {
        "input_total": len(mappings),
        "output_total": len(optimized),
        "removed_total": stale_records,
        "deduplicated": len(mappings) - stale_records - len(optimized),
        "stale_file_records_removed": stale_file_records,
        "stale_callable_records_removed": stale_callable_records,
        "stale_paths": dict(sorted(stale_paths.items())),
        "stale_callables": dict(sorted(stale_callables.items())),
        "same_name_candidates_downgraded": converted,
        "owner_scoped_triggers_added": sum(
            record.get("trigger", {}).get("kind") == "owner_scoped_callable"
            for record in optimized
        ),
    }
    return optimized, stats


def _field_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    upstream = record.get("upstream", {})
    ascend = record.get("ascend", {})
    return (
        record.get("relation"),
        upstream.get("file"),
        upstream.get("attribute"),
        ascend.get("file"),
    )


def _field_occurrence(
    source: Mapping[str, Any], index: int, original_upstream_file: str | None
) -> JsonObject:
    upstream = source.get("upstream", {})
    ascend = source.get("ascend", {})
    return {
        "source_record_index": index,
        "original_risk": source.get("risk"),
        "original_upstream_file": original_upstream_file,
        "upstream_owner": upstream.get("owner"),
        "upstream_module": upstream.get("module"),
        "ascend_line": ascend.get("line"),
        "ascend_expression": ascend.get("expression"),
        "context": source.get("context"),
    }


def _is_external_imported_attribute(record: Mapping[str, Any]) -> str | None:
    if record.get("relation") != "imported_upstream_attribute":
        return None
    attribute = str(record.get("upstream", {}).get("attribute", ""))
    prefix = attribute.split(".", 1)[0]
    return prefix if prefix in EXTERNAL_ATTRIBUTE_PREFIXES else None


def optimize_field_mappings(
    mappings: Sequence[Mapping[str, Any]],
    vllm_root: Path | None = None,
    ascend_root: Path | None = None,
) -> tuple[list[JsonObject], JsonObject]:
    groups: dict[
        tuple[Any, ...], list[tuple[int, JsonObject, str, str | None]]
    ] = defaultdict(list)
    external_removed: Counter[str] = Counter()
    canonical_rewrites: Counter[str] = Counter()
    stale_records = 0
    stale_paths: Counter[str] = Counter()
    downgraded = 0

    for index, source in enumerate(mappings):
        external_prefix = _is_external_imported_attribute(source)
        if external_prefix is not None:
            external_removed[external_prefix] += 1
            continue

        record = copy.deepcopy(dict(source))
        upstream = record.setdefault("upstream", {})
        original_upstream_file = upstream.get("file")
        owner = upstream.get("owner")
        canonical_file = CANONICAL_OWNER_FILES.get(str(owner))
        if canonical_file and canonical_file != original_upstream_file:
            upstream["file"] = canonical_file
            canonical_rewrites[str(owner)] += 1

        missing = _missing_mapping_files(record, vllm_root, ascend_root)
        if missing:
            stale_records += 1
            stale_paths.update(missing)
            continue

        relation = str(record.get("relation", ""))
        original_risk = str(record.get("risk", "low"))
        if relation == "direct_upstream_attribute":
            confidence = "high"
            reason = "direct module attribute reference requires source-SHA validation"
        elif relation == "imported_upstream_attribute":
            confidence = "medium"
            reason = "imported attribute reference requires owner and runtime-reachability validation"
        else:
            confidence = "low"
            reason = "field owner was inferred statically and requires type/owner validation"
            record["risk"] = _cap_risk(original_risk, "medium")
            if record["risk"] != original_risk:
                downgraded += 1
        _candidate(record, confidence=confidence, reason=reason)
        groups[_field_key(record)].append(
            (index, record, relation, original_upstream_file)
        )

    optimized: list[JsonObject] = []
    for entries in groups.values():
        _, winner, _, _ = max(entries, key=lambda item: _risk(item[1]))
        winner["occurrences"] = _unique(
            _field_occurrence(record, index, original_file)
            for index, record, _, original_file in entries
        )
        _provenance(
            winner,
            table="main2main_field_mapping.json",
            indexes=[entry[0] for entry in entries],
            relations=[entry[2] for entry in entries],
        )
        optimized.append(winner)

    optimized.sort(key=lambda record: tuple(str(v or "") for v in _field_key(record)))
    retained_input = len(mappings) - sum(external_removed.values()) - stale_records
    stats = {
        "input_total": len(mappings),
        "output_total": len(optimized),
        "removed_total": sum(external_removed.values()) + stale_records,
        "deduplicated": retained_input - len(optimized),
        "external_imported_attributes_removed": dict(sorted(external_removed.items())),
        "stale_file_records_removed": stale_records,
        "stale_paths": dict(sorted(stale_paths.items())),
        "canonical_owner_rewrites": dict(sorted(canonical_rewrites.items())),
        "uncertain_type_bindings_downgraded": downgraded,
    }
    return optimized, stats


def _looks_like_constructor(callee: Any) -> bool:
    name = str(callee or "").rsplit(".", 1)[-1]
    return bool(name and name[0].isupper())


def _is_constructor_member_fanout(record: Mapping[str, Any]) -> bool:
    if record.get("relation") != "upstream_call_keyword_protocol":
        return False
    if not _looks_like_constructor(record.get("upstream", {}).get("callee")):
        return False
    symbol = str(record.get("ascend", {}).get("symbol", ""))
    if "." not in symbol:
        return False
    member = symbol.rsplit(".", 1)[-1]
    return member not in CONSTRUCTOR_METHODS


def _call_endpoint(record: Mapping[str, Any]) -> str | None:
    upstream = record.get("upstream", {})
    if upstream.get("callee"):
        return str(upstream["callee"])
    class_name = upstream.get("class")
    method = upstream.get("method")
    if class_name and method:
        return f"{class_name}.{method}"
    return str(method) if method else None


def _call_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    upstream = record.get("upstream", {})
    ascend = record.get("ascend", {})
    return (
        record.get("relation"),
        upstream.get("file"),
        _call_endpoint(record),
        ascend.get("file"),
        ascend.get("symbol"),
    )


def _call_occurrence(source: Mapping[str, Any], index: int) -> JsonObject:
    upstream = source.get("upstream", {})
    ascend = source.get("ascend", {})
    return {
        "source_record_index": index,
        "original_risk": source.get("risk"),
        "upstream_line": upstream.get("line"),
        "keywords": list(upstream.get("keywords") or []),
        "ascend_line": ascend.get("line"),
        "ascend_expression": ascend.get("expression"),
        "historical_missing_keywords": list(ascend.get("missing_keywords") or []),
    }


def optimize_call_mappings(
    mappings: Sequence[Mapping[str, Any]],
    vllm_root: Path | None = None,
    ascend_root: Path | None = None,
) -> tuple[list[JsonObject], JsonObject]:
    groups: dict[tuple[Any, ...], list[tuple[int, JsonObject, str]]] = defaultdict(list)
    fanout_removed = 0
    missing_keyword_records = 0
    missing_keyword_values = 0
    downgraded = 0
    stale_records = 0
    stale_paths: Counter[str] = Counter()

    for index, source in enumerate(mappings):
        if _is_constructor_member_fanout(source):
            fanout_removed += 1
            continue

        missing = _missing_mapping_files(source, vllm_root, ascend_root)
        if missing:
            stale_records += 1
            stale_paths.update(missing)
            continue

        record = copy.deepcopy(dict(source))
        relation = str(record.get("relation", ""))
        original_risk = str(record.get("risk", "low"))
        record["risk"] = _cap_risk(original_risk, "medium")
        if record["risk"] != original_risk:
            downgraded += 1
        ascend = record.setdefault("ascend", {})
        old_missing = list(ascend.get("missing_keywords") or [])
        if old_missing:
            missing_keyword_records += 1
            missing_keyword_values += len(old_missing)
        ascend["missing_keywords"] = []
        record["keyword_compatibility"] = {
            "status": "unverified",
            "reason": (
                "historical missing_keywords was discarded; recompute from the exact "
                "callsite delta after verifying the effective callee relationship"
            ),
        }
        confidence = "medium" if relation == "upstream_call_protocol" else "low"
        _candidate(
            record,
            confidence=confidence,
            reason="callee/counterpart binding and signature compatibility require AST validation",
        )
        groups[_call_key(record)].append((index, record, relation))

    optimized: list[JsonObject] = []
    for entries in groups.values():
        _, winner, _ = max(entries, key=lambda item: _risk(item[1]))
        keywords = _unique(
            keyword
            for _, record, _ in entries
            for keyword in (record.get("upstream", {}).get("keywords") or [])
        )
        if keywords:
            winner.setdefault("upstream", {})["keywords"] = keywords
        winner["occurrences"] = _unique(
            _call_occurrence(record, index) for index, record, _ in entries
        )
        _provenance(
            winner,
            table="main2main_call_protocol_mapping.json",
            indexes=[entry[0] for entry in entries],
            relations=[entry[2] for entry in entries],
        )
        optimized.append(winner)

    optimized.sort(key=lambda record: tuple(str(v or "") for v in _call_key(record)))
    retained_input = len(mappings) - fanout_removed - stale_records
    stats = {
        "input_total": len(mappings),
        "output_total": len(optimized),
        "removed_total": fanout_removed + stale_records,
        "deduplicated": retained_input - len(optimized),
        "constructor_member_fanout_removed": fanout_removed,
        "stale_file_records_removed": stale_records,
        "stale_paths": dict(sorted(stale_paths.items())),
        "records_with_untrusted_missing_keywords": missing_keyword_records,
        "missing_keyword_values_cleared": missing_keyword_values,
        "unverified_records_downgraded": downgraded,
    }
    return optimized, stats


def _registration_paths(record: Mapping[str, Any]) -> list[str]:
    ascend = record.get("ascend", {})
    return [
        str(path)
        for path in (ascend.get("registry_file"), ascend.get("registered_file"))
        if path
    ]


def _is_true_package_entrypoint(record: Mapping[str, Any]) -> bool:
    expression = str(record.get("ascend", {}).get("expression", ""))
    return "vllm_ascend:" in expression or "vllm." in expression and "entry" in expression


def _registration_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    ascend = record.get("ascend", {})
    return (
        record.get("relation"),
        ascend.get("registry_file"),
        ascend.get("registered_file"),
        ascend.get("expression"),
    )


def _registration_occurrence(source: Mapping[str, Any], index: int) -> JsonObject:
    ascend = source.get("ascend", {})
    return {
        "source_record_index": index,
        "source_relation": source.get("relation"),
        "original_risk": source.get("risk"),
        "registry_line": ascend.get("line"),
        "expression": ascend.get("expression"),
    }


def optimize_registration_mappings(
    mappings: Sequence[Mapping[str, Any]], ascend_root: Path | None
) -> tuple[list[JsonObject], JsonObject]:
    groups: dict[tuple[Any, ...], list[tuple[int, JsonObject, str]]] = defaultdict(list)
    stale_removed = 0
    stale_paths: Counter[str] = Counter()
    build_refs = 0

    validate_paths = ascend_root is not None and ascend_root.is_dir()
    for index, source in enumerate(mappings):
        paths = _registration_paths(source)
        missing = (
            [path for path in paths if not (ascend_root / Path(path)).exists()]
            if validate_paths and ascend_root is not None
            else []
        )
        if missing:
            stale_removed += 1
            stale_paths.update(missing)
            continue

        record = copy.deepcopy(dict(source))
        source_relation = str(record.get("relation", ""))
        if source_relation == "package_entrypoint" and not _is_true_package_entrypoint(record):
            record["relation"] = "package_build_reference"
            record["risk"] = "low"
            _candidate(
                record,
                confidence="low",
                action="dismiss",
                status="dismissed",
                reason="ordinary setup/build reference is not a vLLM runtime entrypoint",
            )
            build_refs += 1
        else:
            _candidate(
                record,
                confidence="medium",
                reason=(
                    "declared lifecycle edge requires import/entrypoint and process-scope validation"
                    if validate_paths
                    else "source root unavailable; lifecycle edge and target existence require validation"
                ),
            )
        groups[_registration_key(record)].append((index, record, source_relation))

    optimized: list[JsonObject] = []
    for entries in groups.values():
        _, winner, _ = max(entries, key=lambda item: _risk(item[1]))
        winner["occurrences"] = _unique(
            _registration_occurrence(record, index) for index, record, _ in entries
        )
        _provenance(
            winner,
            table="main2main_registration_mapping.json",
            indexes=[entry[0] for entry in entries],
            relations=[entry[2] for entry in entries],
        )
        optimized.append(winner)

    optimized.sort(
        key=lambda record: tuple(str(v or "") for v in _registration_key(record))
    )
    retained_input = len(mappings) - stale_removed
    stats = {
        "input_total": len(mappings),
        "output_total": len(optimized),
        "removed_total": stale_removed,
        "deduplicated": retained_input - len(optimized),
        "stale_records_removed": stale_removed,
        "stale_paths": dict(sorted(stale_paths.items())),
        "package_build_references_downgraded": build_refs,
        "target_existence_checked": validate_paths,
    }
    return optimized, stats


def build_summary(table_id: str, mappings: Sequence[Mapping[str, Any]]) -> JsonObject:
    summary: JsonObject = {
        "total": len(mappings),
        "by_relation": _counter(str(record.get("relation")) for record in mappings),
        "by_risk": _counter(str(record.get("risk")) for record in mappings),
        "by_action": _counter(str(record.get("action")) for record in mappings),
        "by_confidence": _counter(str(record.get("confidence")) for record in mappings),
    }
    upstream_files = {
        record.get("upstream", {}).get("file")
        for record in mappings
        if record.get("upstream", {}).get("file")
    }
    ascend_files = {
        record.get("ascend", {}).get("file")
        or record.get("ascend", {}).get("registry_file")
        for record in mappings
        if record.get("ascend", {}).get("file")
        or record.get("ascend", {}).get("registry_file")
    }
    summary["upstream_files"] = len(upstream_files)
    summary["ascend_files"] = len(ascend_files)
    if table_id == "method":
        summary["signature_mismatch"] = sum(
            bool(record.get("signature_changed"))
            or (
                bool(record.get("upstream", {}).get("signature"))
                and bool(record.get("ascend", {}).get("signature"))
                and record["upstream"]["signature"] != record["ascend"]["signature"]
            )
            for record in mappings
        )
    if table_id == "field":
        summary["total_mappings"] = len(mappings)
    return summary


Optimizer = Callable[..., tuple[list[JsonObject], JsonObject]]
OPTIMIZERS: dict[str, Optimizer] = {
    "method": optimize_method_mappings,
    "field": optimize_field_mappings,
    "call_protocol": optimize_call_mappings,
    "registration": optimize_registration_mappings,
}


def _generated_at_from_epoch(epoch: str) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def resolve_generated_at(
    mapping_dir: Path, output_names: Sequence[str], requested: str | None
) -> str:
    if requested:
        return requested
    if os.environ.get("SOURCE_DATE_EPOCH"):
        return _generated_at_from_epoch(os.environ["SOURCE_DATE_EPOCH"])
    for output_name in output_names:
        output_path = mapping_dir / output_name
        if output_path.exists():
            existing = read_json(output_path)
            timestamp = existing.get("generated_from", {}).get("generated_at")
            if timestamp:
                return str(timestamp)
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _autodetect_sibling(mapping_dir: Path, name: str) -> Path | None:
    candidate = mapping_dir.parent / name
    return candidate if candidate.is_dir() else None


def optimize_all(
    *,
    mapping_dir: Path,
    manifest_path: Path,
    ascend_root: Path | None,
    vllm_root: Path | None,
    generated_at: str | None,
    vllm_sha: str | None,
    ascend_sha: str | None,
    check: bool,
) -> tuple[list[JsonObject], bool]:
    manifest = read_json(manifest_path)
    table_specs = [
        spec
        for spec in manifest.get("tables", [])
        if spec.get("enabled_by_default")
        and spec.get("optimizer")
        and spec.get("optimized_output")
    ]
    output_names = [str(spec["optimized_output"]) for spec in table_specs]
    timestamp = resolve_generated_at(mapping_dir, output_names, generated_at)
    source_shas = {
        "vllm": vllm_sha or git_sha(vllm_root),
        "vllm_ascend": ascend_sha or git_sha(ascend_root),
    }
    reports: list[JsonObject] = []
    all_current = True

    for spec in table_specs:
        # ``file`` is the published production table. Once an optimized table
        # is promoted, optimizing it again would discard the original
        # occurrence history. Keep immutable generator input in ``source_file``
        # and write only the manifest-declared staging output here.
        source_path = mapping_dir / str(spec.get("source_file") or spec["file"])
        output_path = mapping_dir / str(spec["optimized_output"])
        source_document = read_json(source_path)
        mappings = list(source_document.get("mappings", []))
        additional_paths = [
            mapping_dir / str(path)
            for path in spec.get("additional_source_files", [])
        ]
        additional_hashes: dict[str, str] = {}
        for additional_path in additional_paths:
            additional_document = read_json(additional_path)
            mappings.extend(additional_document.get("mappings", []))
            relative = additional_path.relative_to(mapping_dir).as_posix()
            additional_hashes[relative] = file_sha256(additional_path)
        optimizer = OPTIMIZERS[str(spec["optimizer"])]
        if spec["optimizer"] == "registration":
            optimized, optimization = optimizer(mappings, ascend_root)
        elif spec["optimizer"] in {"method", "field", "call_protocol"}:
            optimized, optimization = optimizer(mappings, vllm_root, ascend_root)
        else:
            optimized, optimization = optimizer(mappings)

        generated_from = copy.deepcopy(source_document.get("generated_from", {}))
        generated_from.update(
            {
                "optimized_by": "scripts/optimize_mappings.py",
                "generated_at": timestamp,
                "source_table": source_path.relative_to(mapping_dir).as_posix(),
                "source_table_sha256": file_sha256(source_path),
                "additional_source_tables_sha256": additional_hashes,
                "source_sha": source_shas,
            }
        )
        output_document: JsonObject = {
            "schema_version": 2,
            "generated_from": generated_from,
            "summary": build_summary(str(spec["name"]), optimized),
            "optimization": {
                **optimization,
                "rules_version": 2,
                "canonical_source_preserved": True,
            },
            "mappings": optimized,
        }
        rendered = render_json(output_document)
        current = output_path.exists() and output_path.read_text(
            encoding="utf-8-sig"
        ) == rendered
        if check:
            all_current = all_current and current
        else:
            output_path.write_text(rendered, encoding="utf-8", newline="\n")
            current = True
        reports.append(
            {
                "table": spec["name"],
                "source": source_path.relative_to(mapping_dir).as_posix(),
                "additional_sources": [
                    path.relative_to(mapping_dir).as_posix()
                    for path in additional_paths
                ],
                "published": str(spec["file"]),
                "output": output_path.name,
                "current": current,
                **optimization,
            }
        )
    return reports, all_current


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    script_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping-dir", type=Path, default=script_root)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--ascend-root", type=Path)
    parser.add_argument("--vllm-root", type=Path)
    parser.add_argument("--ascend-sha")
    parser.add_argument("--vllm-sha")
    parser.add_argument("--generated-at")
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail without writing when an optimized output is missing or stale",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    mapping_dir = args.mapping_dir.resolve()
    manifest_path = (
        args.manifest.resolve()
        if args.manifest
        else mapping_dir / "main2main_mapping_manifest.json"
    )
    ascend_root = (
        args.ascend_root.resolve()
        if args.ascend_root
        else _autodetect_sibling(mapping_dir, "vllm-ascend")
    )
    vllm_root = (
        args.vllm_root.resolve()
        if args.vllm_root
        else _autodetect_sibling(mapping_dir, "vllm")
    )
    reports, current = optimize_all(
        mapping_dir=mapping_dir,
        manifest_path=manifest_path,
        ascend_root=ascend_root,
        vllm_root=vllm_root,
        generated_at=args.generated_at,
        vllm_sha=args.vllm_sha,
        ascend_sha=args.ascend_sha,
        check=args.check,
    )
    print(json.dumps({"tables": reports, "all_current": current}, indent=2))
    return 0 if current else 1


if __name__ == "__main__":
    sys.exit(main())
