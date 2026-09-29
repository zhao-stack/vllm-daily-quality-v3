from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import optimize_mappings as optimizer  # noqa: E402
import publish_curated_method_candidates as publisher  # noqa: E402


def test_method_same_name_is_review_only_and_deduplicated() -> None:
    source = {
        "relation": "same_name_protocol",
        "confidence": "medium",
        "risk": "high",
        "upstream": {
            "file": "vllm/a.py",
            "class": None,
            "method": "run",
            "line": 10,
        },
        "ascend": {
            "file": "vllm_ascend/a.py",
            "class": None,
            "method": "run",
            "line": 20,
        },
    }
    duplicate = json.loads(json.dumps(source))
    duplicate["upstream"]["line"] = 11
    duplicate["ascend"]["line"] = 21

    mappings, stats = optimizer.optimize_method_mappings([source, duplicate])

    assert len(mappings) == 1
    record = mappings[0]
    assert record["relation"] == "same_name_candidate"
    assert record["risk"] == "low"
    assert record["confidence"] == "low"
    assert record["action"] == "review"
    assert record["relationship_verified"] is False
    assert record["verification"]["status"] == "candidate"
    assert record["provenance"]["source_relations"] == ["same_name_protocol"]
    assert len(record["occurrences"]) == 2
    assert stats["deduplicated"] == 1
    assert stats["same_name_candidates_downgraded"] == 2


def test_method_mapping_emits_owner_scoped_trigger() -> None:
    source = {
        "relation": "monkey_patch",
        "risk": "critical",
        "upstream": {
            "file": "vllm/config.py",
            "class": "HybridAttentionMambaModelConfig",
            "method": "verify_and_update_config",
        },
        "ascend": {
            "file": "vllm_ascend/patch_mamba.py",
            "method": "verify_and_update_config",
        },
    }

    mappings, stats = optimizer.optimize_method_mappings([source])

    assert mappings[0]["trigger"] == {
        "kind": "owner_scoped_callable",
        "owner": "HybridAttentionMambaModelConfig",
        "callable": "verify_and_update_config",
        "qualified_name": (
            "HybridAttentionMambaModelConfig.verify_and_update_config"
        ),
    }
    assert stats["owner_scoped_triggers_added"] == 1


def test_method_dedup_prefers_verified_binding_at_equal_risk() -> None:
    candidate = {
        "relation": "override",
        "risk": "critical",
        "upstream": {
            "file": "vllm/coordinator.py",
            "class": "Coordinator",
            "method": "__init__",
        },
        "ascend": {
            "file": "vllm_ascend/patch_coordinator.py",
            "class": "AscendCoordinator",
            "method": "__init__",
        },
    }
    verified = json.loads(json.dumps(candidate))
    verified.update(
        {
            "relationship_verified": True,
            "verification": {"status": "verified", "reason": "exact call path"},
            "binding": {
                "kind": "patch_constructor_binding",
                "construction_binding_verified": True,
            },
        }
    )

    mappings, stats = optimizer.optimize_method_mappings([candidate, verified])

    assert len(mappings) == 1
    assert mappings[0]["relationship_verified"] is True
    assert mappings[0]["binding"]["construction_binding_verified"] is True
    assert stats["deduplicated"] == 1


def test_field_filters_external_noise_rewrites_owner_and_aggregates() -> None:
    noisy = {
        "relation": "imported_upstream_attribute",
        "risk": "low",
        "upstream": {
            "file": "vllm/triton_utils.py",
            "module": "vllm.triton_utils",
            "attribute": "tl.load",
        },
        "ascend": {"file": "vllm_ascend/k.py", "line": 1, "expression": "tl.load"},
    }
    field = {
        "relation": "protocol_field_access",
        "risk": "critical",
        "upstream": {
            "file": "vllm/v1/attention/backends/utils.py",
            "owner": "CommonAttentionMetadata",
            "attribute": "seq_lens",
        },
        "ascend": {
            "file": "vllm_ascend/attention.py",
            "line": 10,
            "expression": "metadata.seq_lens",
        },
        "context": "use metadata.seq_lens",
    }
    duplicate = json.loads(json.dumps(field))
    duplicate["ascend"]["line"] = 12
    config = {
        "relation": "config_field_access",
        "risk": "high",
        "upstream": {
            "file": "vllm/config/__init__.py",
            "owner": "VllmConfig",
            "attribute": "scheduler_config",
        },
        "ascend": {
            "file": "vllm_ascend/config.py",
            "line": 3,
            "expression": "maybe_config.scheduler_config",
        },
    }

    mappings, stats = optimizer.optimize_field_mappings(
        [noisy, field, duplicate, config]
    )

    assert len(mappings) == 2
    by_owner = {record["upstream"]["owner"]: record for record in mappings}
    common = by_owner["CommonAttentionMetadata"]
    assert common["upstream"]["file"] == "vllm/v1/attention/backend.py"
    assert common["risk"] == "medium"
    assert common["confidence"] == "low"
    assert len(common["occurrences"]) == 2
    assert by_owner["VllmConfig"]["upstream"]["file"] == "vllm/config/vllm.py"
    assert stats["external_imported_attributes_removed"] == {"tl": 1}
    assert stats["deduplicated"] == 1
    assert stats["canonical_owner_rewrites"] == {
        "CommonAttentionMetadata": 2,
        "VllmConfig": 1,
    }


def test_call_removes_constructor_member_fanout_and_clears_missing_keywords() -> None:
    base = {
        "relation": "upstream_call_keyword_protocol",
        "risk": "critical",
        "upstream": {
            "file": "vllm/layer.py",
            "callee": "FusedMoE",
            "keywords": ["top_k"],
            "line": 10,
        },
        "ascend": {
            "file": "vllm_ascend/layer.py",
            "symbol": "AscendFusedMoE",
            "line": 20,
            "missing_keywords": ["top_k"],
        },
    }
    member_fanout = json.loads(json.dumps(base))
    member_fanout["ascend"]["symbol"] = "AscendFusedMoE.forward"
    duplicate = json.loads(json.dumps(base))
    duplicate["upstream"]["line"] = 11
    duplicate["upstream"]["keywords"] = ["quant_config"]
    duplicate["ascend"]["missing_keywords"] = ["quant_config"]

    mappings, stats = optimizer.optimize_call_mappings(
        [base, member_fanout, duplicate]
    )

    assert len(mappings) == 1
    record = mappings[0]
    assert record["ascend"]["symbol"] == "AscendFusedMoE"
    assert record["ascend"]["missing_keywords"] == []
    assert record["upstream"]["keywords"] == ["top_k", "quant_config"]
    assert record["risk"] == "medium"
    assert record["confidence"] == "low"
    assert record["action"] == "review"
    assert record["relationship_verified"] is False
    assert len(record["occurrences"]) == 2
    assert stats["constructor_member_fanout_removed"] == 1
    assert stats["deduplicated"] == 1
    assert stats["missing_keyword_values_cleared"] == 2


def test_registration_removes_stale_and_separates_build_references(
    tmp_path: Path,
) -> None:
    (tmp_path / "vllm_ascend").mkdir()
    (tmp_path / "vllm_ascend" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "setup.py").write_text("", encoding="utf-8")
    stale = {
        "relation": "patch_module_registered",
        "risk": "high",
        "ascend": {
            "registry_file": "vllm_ascend/__init__.py",
            "registered_file": "vllm_ascend/missing.py",
            "expression": "import vllm_ascend.missing",
        },
    }
    build_ref = {
        "relation": "package_entrypoint",
        "risk": "medium",
        "ascend": {
            "registry_file": "setup.py",
            "registered_file": None,
            "expression": "os.path.join(ROOT_DIR, 'vllm_ascend')",
        },
    }
    entrypoint = {
        "relation": "package_entrypoint",
        "risk": "medium",
        "ascend": {
            "registry_file": "setup.py",
            "registered_file": None,
            "expression": "ascend = vllm_ascend:register",
        },
    }

    mappings, stats = optimizer.optimize_registration_mappings(
        [stale, build_ref, entrypoint], tmp_path
    )

    assert len(mappings) == 2
    by_relation = {record["relation"]: record for record in mappings}
    assert by_relation["package_build_reference"]["action"] == "dismiss"
    assert by_relation["package_build_reference"]["verification"]["status"] == "dismissed"
    assert by_relation["package_entrypoint"]["action"] == "review"
    assert stats["stale_records_removed"] == 1
    assert stats["package_build_references_downgraded"] == 1


def test_pinned_source_roots_remove_definitively_missing_files(tmp_path: Path) -> None:
    vllm_root = tmp_path / "vllm"
    ascend_root = tmp_path / "ascend"
    vllm_root.mkdir()
    ascend_root.mkdir()
    record = {
        "relation": "override",
        "risk": "high",
        "upstream": {
            "file": "vllm/missing.py",
            "class": "Runner",
            "method": "run",
        },
        "ascend": {
            "file": "vllm_ascend/missing.py",
            "class": "AscendRunner",
            "method": "run",
        },
    }

    mappings, stats = optimizer.optimize_method_mappings(
        [record], vllm_root, ascend_root
    )

    assert mappings == []
    assert stats["stale_file_records_removed"] == 1
    assert stats["stale_paths"] == {
        "vllm:vllm/missing.py": 1,
        "vllm_ascend:vllm_ascend/missing.py": 1,
    }


def test_pinned_source_roots_remove_missing_method_symbols(tmp_path: Path) -> None:
    vllm_root = tmp_path / "vllm"
    ascend_root = tmp_path / "ascend"
    upstream_path = vllm_root / "vllm" / "runner.py"
    ascend_path = ascend_root / "vllm_ascend" / "runner.py"
    upstream_path.parent.mkdir(parents=True)
    ascend_path.parent.mkdir(parents=True)
    upstream_path.write_text(
        "class Runner:\n    def other(self):\n        pass\n", encoding="utf-8"
    )
    ascend_path.write_text(
        "class AscendRunner:\n    def run(self):\n        pass\n", encoding="utf-8"
    )
    record = {
        "relation": "override",
        "risk": "high",
        "upstream": {
            "file": "vllm/runner.py",
            "class": "Runner",
            "method": "run",
        },
        "ascend": {
            "file": "vllm_ascend/runner.py",
            "class": "AscendRunner",
            "method": "run",
        },
    }

    mappings, stats = optimizer.optimize_method_mappings(
        [record], vllm_root, ascend_root
    )

    assert mappings == []
    assert stats["stale_callable_records_removed"] == 1
    assert stats["stale_callables"] == {"vllm:run": 1}


def test_generation_is_repeatable_and_preserves_canonical_source(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    source_path = raw_dir / "main2main_method_mapping.json"
    source_document = {
        "schema_version": 1,
        "generated_from": {"scope": "test"},
        "summary": {"total": 1},
        "mappings": [
            {
                "relation": "same_name_protocol",
                "risk": "high",
                "upstream": {"file": "vllm/a.py", "method": "f"},
                "ascend": {"file": "vllm_ascend/a.py", "method": "f"},
            }
        ],
    }
    canonical = json.dumps(source_document, indent=2) + "\n"
    source_path.write_text(canonical, encoding="utf-8")
    manifest = {
        "tables": [
            {
                "name": "method",
                "file": "main2main_method_mapping.json",
                "source_file": "raw/main2main_method_mapping.json",
                "additional_source_files": [
                    "curated/main2main_method_additions.json"
                ],
                "optimized_output": "main2main_method_mapping.optimized.json",
                "enabled_by_default": True,
                "optimizer": "method",
            }
        ]
    }
    curated_dir = tmp_path / "curated"
    curated_dir.mkdir()
    (curated_dir / "main2main_method_additions.json").write_text(
        json.dumps(
            {
                "mappings": [
                    {
                        "relation": "override",
                        "risk": "high",
                        "upstream": {
                            "file": "vllm/b.py",
                            "class": "Base",
                            "method": "g",
                        },
                        "ascend": {
                            "file": "vllm_ascend/b.py",
                            "class": "Child",
                            "method": "g",
                        },
                        "relationship_verified": True,
                        "verification": {"status": "verified"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "main2main_mapping_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    reports, current = optimizer.optimize_all(
        mapping_dir=tmp_path,
        manifest_path=manifest_path,
        ascend_root=None,
        vllm_root=None,
        generated_at="2026-07-14T00:00:00Z",
        vllm_sha="a" * 40,
        ascend_sha="b" * 40,
        check=False,
    )
    assert current is True
    assert reports[0]["output_total"] == 2
    assert source_path.read_text(encoding="utf-8") == canonical
    generated = json.loads(
        (tmp_path / "main2main_method_mapping.optimized.json").read_text(
            encoding="utf-8"
        )
    )
    verified = next(
        record
        for record in generated["mappings"]
        if record["upstream"].get("method") == "g"
    )
    assert verified["relationship_verified"] is True
    assert verified["verification"]["status"] == "verified"

    _, check_current = optimizer.optimize_all(
        mapping_dir=tmp_path,
        manifest_path=manifest_path,
        ascend_root=None,
        vllm_root=None,
        generated_at="2026-07-14T00:00:00Z",
        vllm_sha="a" * 40,
        ascend_sha="b" * 40,
        check=True,
    )
    assert check_current is True


def test_publish_curated_candidates_pins_provenance(tmp_path: Path) -> None:
    validation_path = tmp_path / "validate.json"
    validation_path.write_text(
        json.dumps(
            {
                "missing_method_candidates": [
                    {
                        "relation": "override_candidate",
                        "upstream": {
                            "file": "vllm/runner.py",
                            "class": "Runner",
                            "method": "run",
                        },
                        "ascend": {
                            "file": "vllm_ascend/runner.py",
                            "class": "AscendRunner",
                            "method": "run",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "curated.json"
    payload = publisher.publish(
        validation_path,
        output_path,
        "a" * 40,
        "b" * 40,
        "2026-07-22T00:00:00Z",
    )
    assert payload["summary"]["total"] == 1
    assert payload["generated_from"]["source_sha"]["vllm"] == "a" * 40
    record = payload["mappings"][0]
    assert record["relationship_verified"] is True
    assert record["verification_status"] == "verified"
