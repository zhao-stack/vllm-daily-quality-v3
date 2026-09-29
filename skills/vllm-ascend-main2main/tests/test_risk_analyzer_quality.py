from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from legacy import main2main_risk_analyzer as analyzer


class MappingQualityTests(unittest.TestCase):
    def prediction(self, table: str, record: dict) -> analyzer.Prediction:
        target = analyzer.ascend_target_files(record)[0]
        return analyzer.Prediction(
            files_by_table={table: {target}},
            records_by_table={table: [record]},
            changed_vllm_files=[analyzer.upstream_files(record)[0]],
            vllm_root=Path("."),
            old="old",
            new="new",
        )

    def evidence(self, table: str, record: dict) -> dict:
        prediction = self.prediction(table, record)
        target = analyzer.ascend_target_files(record)[0]
        with mock.patch.object(analyzer, "record_diff_proximity_label", return_value="changed line"):
            return analyzer.review_evidence_for_file(prediction, target, {}, {})

    def test_manifest_final_schema_and_legacy_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / analyzer.MANIFEST_FILE).write_text(
                json.dumps(
                    {
                        "tables": [
                            {
                                "name": "method",
                                "file": "methods.json",
                                "role": "primary",
                                "enabled_by_default": True,
                                "minimum_evidence": "contract_delta",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(analyzer, "SKILL_DIR", root):
                self.assertEqual(analyzer.enabled_table_specs()["method"]["file"], "methods.json")
            (root / analyzer.MANIFEST_FILE).unlink()
            with mock.patch.object(analyzer, "SKILL_DIR", root):
                self.assertIn("method", analyzer.enabled_table_specs())

    def test_token_touch_requires_exact_changed_line_match(self) -> None:
        record = {
            "upstream": {"file": "vllm/x.py", "method": "target"},
            "ascend": {"file": "vllm_ascend/x.py"},
        }
        hunk_only = "@@ -1 +1 @@ def target\n-target_extra = 1\n+target_extra = 2\n"
        with mock.patch.object(analyzer, "diff_text", return_value=hunk_only):
            self.assertFalse(analyzer.token_touched(Path("."), "old", "new", record, "method", {}))
        exact = "@@ -1 +1 @@\n-target()\n+target(value)\n"
        with mock.patch.object(analyzer, "diff_text", return_value=exact):
            self.assertTrue(analyzer.token_touched(Path("."), "old", "new", record, "method", {}))

    def test_owner_scoped_method_ignores_same_name_in_another_class(self) -> None:
        record = {
            "upstream": {
                "file": "vllm/config.py",
                "class": "HybridAttentionMambaModelConfig",
                "method": "verify_and_update_config",
            },
            "ascend": {"file": "vllm_ascend/patch_mamba.py"},
            "trigger": {
                "kind": "owner_scoped_callable",
                "owner": "HybridAttentionMambaModelConfig",
                "callable": "verify_and_update_config",
            },
        }
        old_source = (
            "class HybridAttentionMambaModelConfig:\n"
            "    def verify_and_update_config(self, config):\n"
            "        return config\n"
        )
        new_source = old_source + (
            "\nclass LongcatFlashNgramForCausalLMConfig:\n"
            "    def verify_and_update_config(self, config):\n"
            "        config.mode = 'full'\n"
        )

        def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
            return old_source if commit == "old" else new_source

        with mock.patch.object(analyzer, "source_at_commit", side_effect=source):
            self.assertFalse(
                analyzer.token_touched(
                    Path("."), "old", "new", record, "method", {}, {}
                )
            )

    def test_owner_scoped_method_detects_body_change_without_signature_change(self) -> None:
        record = {
            "upstream": {
                "file": "vllm/config.py",
                "class": "HybridAttentionMambaModelConfig",
                "method": "verify_and_update_config",
            },
            "ascend": {"file": "vllm_ascend/patch_mamba.py"},
            "trigger": {
                "kind": "owner_scoped_callable",
                "owner": "HybridAttentionMambaModelConfig",
                "callable": "verify_and_update_config",
            },
        }

        def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
            value = "old" if commit == "old" else "new"
            return (
                "class HybridAttentionMambaModelConfig:\n"
                "    def verify_and_update_config(self, config):\n"
                f"        config.mode = '{value}'\n"
            )

        with mock.patch.object(analyzer, "source_at_commit", side_effect=source):
            self.assertTrue(
                analyzer.token_touched(
                    Path("."), "old", "new", record, "method", {}, {}
                )
            )

    def test_same_name_candidate_never_becomes_core(self) -> None:
        record = {
            "relation": "same_name_candidate",
            "risk": "critical",
            "upstream": {"file": "vllm/x.py", "function": "run"},
            "ascend": {"file": "vllm_ascend/x.py", "function": "run"},
        }
        with mock.patch.object(
            analyzer, "upstream_callable_contract_delta", return_value="signature_changed"
        ):
            info = self.evidence("method", record)
        self.assertEqual(info["score"], 20)
        self.assertFalse(info["core_eligible"])
        self.assertFalse(analyzer.core_review_files({"vllm_ascend/x.py": info}))

    def test_current_direct_override_relationship_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "runner.py"
            path.parent.mkdir()
            path.write_text(
                "class AscendRunner(Runner):\n"
                "    def execute(self, value):\n"
                "        return value\n",
                encoding="utf-8",
            )
            record = {
                "relation": "override",
                "base_expression": "Runner",
                "upstream": {"class": "Runner"},
                "ascend": {
                    "file": "vllm_ascend/runner.py",
                    "class": "AscendRunner",
                    "method": "execute",
                },
            }
            evidence = analyzer.verify_current_method_relationship(record, root)
        self.assertIn("directly inherits Runner", evidence or "")
        record["relation"] = "override_candidate"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "runner.py"
            path.parent.mkdir()
            path.write_text(
                "class AscendRunner(Runner):\n"
                "    def execute(self, value):\n"
                "        return value\n",
                encoding="utf-8",
            )
            evidence = analyzer.verify_current_method_relationship(record, root)
        self.assertIn("directly inherits Runner", evidence or "")

    def test_call_keyword_delta_needs_verified_relationship_for_core(self) -> None:
        base = {
            "relation": "upstream_call_keyword_protocol",
            "risk": "critical",
            "upstream": {"file": "vllm/x.py", "callee": "Runner"},
            "ascend": {"file": "vllm_ascend/x.py", "symbol": "AscendRunner"},
            "_contract_delta": {"added_keywords": ["is_padding"]},
        }
        unverified = self.evidence("call_protocol", base)
        self.assertEqual(unverified["score"], 40)
        self.assertFalse(unverified["core_eligible"])
        verified_record = {**base, "verification": {"status": "verified"}}
        verified = self.evidence("call_protocol", verified_record)
        self.assertEqual(verified["score"], 90)
        self.assertTrue(verified["core_eligible"])

    def test_real_callee_keyword_delta_uses_ast_calls(self) -> None:
        record = {
            "upstream": {"file": "vllm/x.py", "callee": "Runner"},
            "ascend": {"file": "vllm_ascend/x.py"},
        }

        def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
            return "Runner(device=device)" if commit == "old" else "Runner(device=device, is_padding=True)"

        with mock.patch.object(analyzer, "source_at_commit", side_effect=source):
            delta = analyzer.call_keyword_delta(Path("."), "old", "new", record, {})
        self.assertEqual(delta["added_keywords"], ["is_padding"])

    def test_call_delta_does_not_bind_attribute_by_leaf_name(self) -> None:
        record = {
            "upstream": {"file": "vllm/x.py", "callee": "Runner"},
            "ascend": {"file": "vllm_ascend/x.py"},
        }

        def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
            if commit == "old":
                return "other.Runner(device=device)"
            return "other.Runner(device=device, is_padding=True)"

        with mock.patch.object(analyzer, "source_at_commit", side_effect=source):
            self.assertIsNone(analyzer.call_keyword_delta(Path("."), "old", "new", record, {}))

    def test_removed_callable_is_a_contract_delta(self) -> None:
        record = {
            "upstream": {"file": "vllm/x.py", "class": "Runner", "method": "execute"},
            "ascend": {"file": "vllm_ascend/x.py"},
        }
        old = {"classes": {"Runner": {"methods": {"execute": {"signature": "(self, x)"}}}}, "functions": {}}
        new = {"classes": {"Runner": {"methods": {}}}, "functions": {}}

        def parsed(_root: Path, commit: str, _file: str, _cache: dict) -> dict:
            return old if commit == "old" else new

        prediction = self.prediction("method", record)
        with mock.patch.object(analyzer, "parsed_file_at_commit", side_effect=parsed):
            self.assertEqual(
                analyzer.upstream_callable_contract_delta(prediction, record, {}),
                "removed_or_renamed",
            )

    def test_removed_imported_module_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "offload.py"
            path.parent.mkdir()
            path.write_text(
                "from vllm.v1.kv_offload.abstract import LoadStoreSpec\n",
                encoding="utf-8",
            )

            def files(_root: Path, commit: str) -> set[str]:
                return (
                    {"vllm/v1/kv_offload/abstract.py"}
                    if commit == "old"
                    else set()
                )

            with (
                mock.patch.object(analyzer, "python_files_at_commit", side_effect=files),
                mock.patch.object(analyzer, "exact_file_renames", return_value={}),
            ):
                records = analyzer.discover_import_contract_candidates(
                    Path("."), root, "old", "new", {"vllm/v1/kv_offload/abstract.py"}
                )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["relation"], "import_contract")
        self.assertEqual(
            records[0]["_contract_delta"]["kind"],
            "imported_module_removed_or_relocated",
        )

    def test_import_relocation_reports_exact_new_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "fla.py"
            path.parent.mkdir()
            path.write_text(
                "from vllm.model_executor.layers.fla.ops.utils import contiguous\n",
                encoding="utf-8",
            )

            def files(_root: Path, commit: str) -> set[str]:
                return (
                    {"vllm/model_executor/layers/fla/ops/utils.py"}
                    if commit == "old"
                    else {"vllm/third_party/flash_linear_attention/ops/utils.py"}
                )

            with (
                mock.patch.object(analyzer, "python_files_at_commit", side_effect=files),
                mock.patch.object(
                    analyzer,
                    "exact_file_renames",
                    return_value={
                        "vllm/model_executor/layers/fla/ops/utils.py":
                        "vllm/third_party/flash_linear_attention/ops/utils.py"
                    },
                ),
            ):
                records = analyzer.discover_import_contract_candidates(
                    Path("."), root, "old", "new", set()
                )
        delta = records[0]["_contract_delta"]
        self.assertTrue(delta["relocation_verified"])
        self.assertEqual(
            delta["new_module"], "vllm.third_party.flash_linear_attention.ops.utils"
        )

    def test_attribute_chain_import_relocation_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "patch.py"
            path.parent.mkdir()
            path.write_text(
                "import vllm\n"
                "vllm.model_executor.layers.fla.ops.index.prepare_chunk_indices = replacement\n",
                encoding="utf-8",
            )
            old_file = "vllm/model_executor/layers/fla/ops/index.py"
            new_file = "vllm/third_party/flash_linear_attention/ops/index.py"

            def files(_root: Path, commit: str) -> set[str]:
                return {old_file} if commit == "old" else {new_file}

            with (
                mock.patch.object(analyzer, "python_files_at_commit", side_effect=files),
                mock.patch.object(
                    analyzer, "exact_file_renames", return_value={old_file: new_file}
                ),
            ):
                records = analyzer.discover_import_contract_candidates(
                    Path("."), root, "old", "new", set()
                )
        relocation = next(
            record
            for record in records
            if record["_contract_delta"].get("relocation_verified")
        )
        self.assertEqual(
            relocation["_contract_delta"]["module"],
            "vllm.model_executor.layers.fla.ops.index",
        )
        self.assertEqual(
            relocation["_contract_delta"]["symbol"], "prepare_chunk_indices"
        )

    def test_range_specific_override_is_discovered_without_table_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "runner.py"
            path.parent.mkdir()
            path.write_text(
                "from vllm.worker import Runner\n\n"
                "class AscendRunner(Runner):\n"
                "    def execute(self, value):\n"
                "        return value\n",
                encoding="utf-8",
            )

            def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
                suffix = "" if commit == "old" else ", mode=None"
                return f"class Runner:\n    def execute(self, value{suffix}):\n        return value\n"

            with (
                mock.patch.object(analyzer, "active_mappings", return_value=[]),
                mock.patch.object(
                    analyzer,
                    "python_files_at_commit",
                    return_value={"vllm/worker.py"},
                ),
                mock.patch.object(analyzer, "exact_file_renames", return_value={}),
                mock.patch.object(analyzer, "source_at_commit", side_effect=source),
            ):
                records = analyzer.discover_changed_override_candidates(
                    Path("."), root, "old", "new"
                )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["relation"], "override_candidate")
        self.assertTrue(records[0]["relationship_verified"])

    def test_inherited_override_contract_is_resolved_through_upstream_mro(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "runner.py"
            path.parent.mkdir()
            path.write_text(
                "from vllm.child import Child\n\n"
                "class AscendRunner(Child):\n"
                "    def execute(self, value):\n"
                "        return value\n",
                encoding="utf-8",
            )
            files = {"vllm/child.py", "vllm/base.py"}

            def source(_root: Path, commit: str, file: str, _cache: dict) -> str:
                if file == "vllm/child.py":
                    return "from vllm.base import Base\n\nclass Child(Base):\n    pass\n"
                suffix = "" if commit == "old" else ", mode=None"
                return (
                    "class Base:\n"
                    f"    def execute(self, value{suffix}):\n"
                    "        return value\n"
                )

            with (
                mock.patch.object(analyzer, "active_mappings", return_value=[]),
                mock.patch.object(
                    analyzer, "python_files_at_commit", return_value=files
                ),
                mock.patch.object(analyzer, "exact_file_renames", return_value={}),
                mock.patch.object(analyzer, "source_at_commit", side_effect=source),
            ):
                records = analyzer.discover_changed_override_candidates(
                    Path("."), root, "old", "new"
                )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["upstream"]["class"], "Base")
        self.assertEqual(
            records[0]["_contract_delta"]["kind"], "override_signature_delta"
        )
        self.assertTrue(records[0]["_contract_delta"]["mro_verified"])

    def test_module_patch_alias_is_aggregated_and_closes_local_call_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            patch = root / "vllm_ascend" / "patch.py"
            helper = root / "vllm_ascend" / "core" / "helper.py"
            helper.parent.mkdir(parents=True)
            patch.write_text(
                "import vllm\n"
                "import vllm.mod as mod_alias\n"
                "from vllm_ascend.core.helper import helper\n\n"
                "class Local:\n"
                "    def __init__(self):\n"
                "        helper(1)\n\n"
                "def replacement(value):\n"
                "    return Local()\n\n"
                "vllm.mod.target = replacement\n"
                "mod_alias.target = replacement\n",
                encoding="utf-8",
            )
            helper.write_text("def helper(value):\n    return value\n", encoding="utf-8")
            files = {"vllm/mod.py", "vllm/core/helper.py"}

            def source(_root: Path, commit: str, file: str, _cache: dict) -> str:
                suffix = "" if commit == "old" else ", mode=None"
                name = "target" if file == "vllm/mod.py" else "helper"
                return f"def {name}(value{suffix}):\n    return value\n"

            with (
                mock.patch.object(analyzer, "active_mappings", return_value=[]),
                mock.patch.object(analyzer, "python_files_at_commit", return_value=files),
                mock.patch.object(analyzer, "exact_file_renames", return_value={}),
                mock.patch.object(analyzer, "source_at_commit", side_effect=source),
            ):
                records = analyzer.discover_patch_binding_candidates(
                    Path("."), root, "old", "new", files
                )
        direct = next(record for record in records if record["relation"] == "monkey_patch")
        closure = next(
            record for record in records if record["relation"] == "patch_call_closure"
        )
        self.assertEqual(len(direct["occurrences"]), 2)
        self.assertEqual(direct["binding"]["kind"], "module_attribute_assignment")
        self.assertEqual(closure["upstream"]["method"], "helper")
        self.assertEqual(
            closure["binding"]["kind"], "verified_patch_call_closure"
        )

    def test_independent_constructor_is_not_a_verified_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "worker.py"
            path.parent.mkdir()
            path.write_text(
                "class Child(Base):\n"
                "    def __init__(self, device):\n"
                "        self.device = device\n",
                encoding="utf-8",
            )
            record = {
                "relation": "override",
                "upstream": {"class": "Base", "method": "__init__"},
                "ascend": {
                    "file": "vllm_ascend/worker.py",
                    "class": "Child",
                    "method": "__init__",
                },
                "base_expression": "Base",
            }
            self.assertIsNone(analyzer.verify_current_method_relationship(record, root))

    def test_constructor_calling_super_is_a_verified_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "worker.py"
            path.parent.mkdir()
            path.write_text(
                "class Child(Base):\n"
                "    def __init__(self, value):\n"
                "        super().__init__(value)\n",
                encoding="utf-8",
            )
            record = {
                "relation": "override",
                "upstream": {"class": "Base", "method": "__init__"},
                "ascend": {
                    "file": "vllm_ascend/worker.py",
                    "class": "Child",
                    "method": "__init__",
                },
                "base_expression": "Base",
            }
            evidence = analyzer.verify_current_method_relationship(record, root)
        self.assertIn("directly inherits Base", evidence or "")

    def test_forwarded_kwargs_accept_optional_upstream_constructor_addition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "worker.py"
            path.parent.mkdir()
            path.write_text(
                "class Child(Base):\n"
                "    def __init__(self, value, **kwargs):\n"
                "        super().__init__(value, **kwargs)\n",
                encoding="utf-8",
            )
            record = {
                "relation": "override",
                "upstream": {
                    "file": "vllm/worker.py",
                    "class": "Base",
                    "method": "__init__",
                },
                "ascend": {
                    "file": "vllm_ascend/worker.py",
                    "class": "Child",
                    "method": "__init__",
                },
            }
            prediction = self.prediction("method", record)
            prediction.ascend_root = root

            def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
                suffix = "" if commit == "old" else ", mode=None"
                return f"class Base:\n    def __init__(self, value{suffix}):\n        pass\n"

            with mock.patch.object(analyzer, "source_at_commit", side_effect=source):
                self.assertTrue(
                    analyzer.ascend_callable_matches_new_contract(
                        prediction, record, {}
                    )
                )

    def test_kwargs_alone_do_not_prove_renamed_keyword_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "manager.py"
            path.parent.mkdir()
            path.write_text(
                "def make(*, old_limit=None, **kwargs):\n"
                "    return consume(limit=old_limit)\n",
                encoding="utf-8",
            )
            record = {
                "relation": "monkey_patch",
                "upstream": {
                    "file": "vllm/manager.py",
                    "method": "make",
                },
                "ascend": {
                    "file": "vllm_ascend/manager.py",
                    "method": "make",
                },
            }
            prediction = self.prediction("method", record)
            prediction.ascend_root = root

            def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
                keyword = "old_limit" if commit == "old" else "new_limit"
                return f"def make(*, {keyword}=None, **kwargs):\n    pass\n"

            with mock.patch.object(analyzer, "source_at_commit", side_effect=source):
                self.assertFalse(
                    analyzer.ascend_callable_matches_new_contract(
                        prediction, record, {}
                    )
                )

    def test_already_compatible_override_is_not_core(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "runner.py"
            path.parent.mkdir()
            path.write_text(
                "class AscendRunner:\n"
                "    def execute(self, value, mode=None, alignment_tokens=None):\n"
                "        return value\n",
                encoding="utf-8",
            )
            record = {
                "relation": "override",
                "relationship_verified": True,
                "risk": "critical",
                "upstream": {
                    "file": "vllm/worker.py",
                    "class": "Runner",
                    "method": "execute",
                },
                "ascend": {
                    "file": "vllm_ascend/runner.py",
                    "class": "AscendRunner",
                    "method": "execute",
                },
            }
            prediction = self.prediction("method", record)
            prediction.ascend_root = root
            source = (
                "class Runner:\n"
                "    def execute(self, value, mode=None):\n"
                "        return value\n"
            )
            with (
                mock.patch.object(
                    analyzer,
                    "upstream_callable_contract_delta",
                    return_value="signature_changed",
                ),
                mock.patch.object(analyzer, "source_at_commit", return_value=source),
                mock.patch.object(
                    analyzer, "record_diff_proximity_label", return_value="changed line"
                ),
            ):
                info = analyzer.review_evidence_for_file(
                    prediction, "vllm_ascend/runner.py", {}, {}
                )
        self.assertEqual(info["score"], 25)
        self.assertFalse(info["core_eligible"])
        self.assertIn("already compatible override signature", info["labels"])
        self.assertEqual(info["action"], "dismiss")
        self.assertTrue(info["action_gate"]["relationship_verified"])
        self.assertTrue(info["action_gate"]["contract_changed"])

    def test_core_break_suppresses_ambiguous_file_compatible_label(self) -> None:
        compatible = {
            "relation": "override",
            "relationship_verified": True,
            "upstream": {
                "file": "vllm/manager.py",
                "class": "Manager",
                "method": "__init__",
            },
            "ascend": {
                "file": "vllm_ascend/manager.py",
                "class": "AscendManager",
                "method": "__init__",
            },
        }
        broken = {
            "relation": "patch_call_closure",
            "relationship_verified": True,
            "upstream": {
                "file": "vllm/manager.py",
                "method": "make_manager",
            },
            "ascend": {
                "file": "vllm_ascend/manager.py",
                "method": "make_manager",
            },
        }
        prediction = self.prediction("method", compatible)
        prediction.records_by_table["method"] = [compatible, broken]

        def matches(_prediction: object, record: dict, _cache: dict) -> bool:
            return (record.get("upstream") or {}).get("method") == "__init__"

        with (
            mock.patch.object(
                analyzer,
                "upstream_callable_contract_delta",
                return_value="signature_changed",
            ),
            mock.patch.object(
                analyzer, "ascend_callable_matches_new_contract", side_effect=matches
            ),
            mock.patch.object(
                analyzer, "record_diff_proximity_label", return_value="changed line"
            ),
        ):
            info = analyzer.review_evidence_for_file(
                prediction, "vllm_ascend/manager.py", {}, {}
            )

        self.assertTrue(info["core_eligible"])
        self.assertIn("upstream callable signature changed", info["labels"])
        self.assertNotIn("already compatible override signature", info["labels"])

    def test_interface_comparison_ignores_formatting_only_changes(self) -> None:
        record = {
            "upstream": {"file": "vllm/x.py", "class": "Runner", "method": "run"},
            "ascend": {"file": "vllm_ascend/x.py"},
        }
        old = "class Runner:\n    def run(\n        self, value, mode=None\n    ):\n        return value\n"
        new = "class Runner:\n    def run(self, value, mode=None):\n        return value\n"
        prediction = self.prediction("method", record)

        def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
            return old if commit == "old" else new

        with mock.patch.object(analyzer, "source_at_commit", side_effect=source):
            self.assertIsNone(
                analyzer.upstream_callable_contract_delta(prediction, record, {}, {})
            )

    def test_exact_callable_rename_is_resolved(self) -> None:
        record = {
            "upstream": {"file": "vllm/x.py", "class": "Runner", "method": "run"},
            "ascend": {"file": "vllm_ascend/x.py"},
        }
        old = "class Runner:\n    def run(self, value):\n        return value\n"
        new = "class Runner:\n    def execute(self, value):\n        return value\n"
        prediction = self.prediction("method", record)

        def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
            return old if commit == "old" else new

        with mock.patch.object(analyzer, "source_at_commit", side_effect=source):
            self.assertEqual(
                analyzer.upstream_callable_contract_delta(prediction, record, {}, {}),
                "renamed",
            )
        self.assertEqual(record["_contract_delta"]["new_name"], "execute")

    def test_ambiguous_callable_rename_is_not_resolved(self) -> None:
        old = "def run(value):\n    return value\n"
        new = (
            "def execute(value):\n"
            "    return value\n\n"
            "def invoke(value):\n"
            "    return value\n"
        )
        self.assertIsNone(analyzer.exact_callable_rename(old, new, None, "run"))

    def test_new_upstream_callable_is_an_exact_contract_delta(self) -> None:
        record = {
            "upstream": {"file": "vllm/x.py", "class": "Runner", "method": "capture"},
            "ascend": {"file": "vllm_ascend/x.py"},
        }
        prediction = self.prediction("method", record)

        def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
            if commit == "old":
                return "class Runner:\n    pass\n"
            return "class Runner:\n    def capture(self, value, mode=None):\n        return value\n"

        with mock.patch.object(analyzer, "source_at_commit", side_effect=source):
            self.assertEqual(
                analyzer.upstream_callable_contract_delta(prediction, record, {}, {}),
                "introduced",
            )
        self.assertEqual(record["_contract_delta"]["kind"], "callable_introduced")

    def test_removed_owner_is_not_reported_as_exact_callable_rename(self) -> None:
        record = {
            "upstream": {"file": "vllm/x.py", "class": "OldRunner", "method": "capture"},
            "ascend": {"file": "vllm_ascend/x.py"},
        }
        prediction = self.prediction("method", record)

        def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
            if commit == "old":
                return "class OldRunner:\n    def capture(self, value):\n        return value\n"
            return "class ConsolidatedRunner:\n    def capture(self, value):\n        return value\n"

        with mock.patch.object(analyzer, "source_at_commit", side_effect=source):
            self.assertEqual(
                analyzer.upstream_callable_contract_delta(prediction, record, {}, {}),
                "owner_removed",
            )
        self.assertEqual(
            record["_contract_delta"]["kind"],
            "callable_owner_removed_or_consolidated",
        )

    def test_explicit_copied_contract_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "copied.py"
            path.parent.mkdir()
            path.write_text(
                "# Adapted from vllm/model_executor/layers/logits_processor.py\n",
                encoding="utf-8",
            )
            records = analyzer.discover_copied_contract_candidates(
                root, {"vllm/model_executor/layers/logits_processor.py"}
            )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["relation"], "copied_contract")

    def test_default_profile_enables_only_exact_method_table(self) -> None:
        self.assertEqual(analyzer.default_analysis_profile(), "exact-contracts")
        self.assertEqual(set(analyzer.enabled_table_specs()), {"method"})
        self.assertEqual(
            set(analyzer.enabled_table_specs("expanded")),
            {"method", "field", "call_protocol", "registration", "inheritance"},
        )

    def test_exact_import_profile_suppresses_changed_owner_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "consumer.py"
            path.parent.mkdir()
            path.write_text(
                "from vllm.api import exported\n",
                encoding="utf-8",
            )

            def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
                suffix = "" if commit == "old" else ", mode=None"
                return f"def exported(value{suffix}):\n    return value\n"

            with (
                mock.patch.object(
                    analyzer,
                    "python_files_at_commit",
                    return_value={"vllm/api.py"},
                ),
                mock.patch.object(analyzer, "exact_file_renames", return_value={}),
                mock.patch.object(analyzer, "source_at_commit", side_effect=source),
            ):
                records = analyzer.discover_import_contract_candidates(
                    Path("."),
                    root,
                    "old",
                    "new",
                    {"vllm/api.py"},
                    exact_only=True,
                )
        self.assertEqual(records, [])

    def test_exact_imported_callable_rename_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "consumer.py"
            path.parent.mkdir()
            path.write_text(
                "from vllm.api import old_name\n",
                encoding="utf-8",
            )

            def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
                name = "old_name" if commit == "old" else "new_name"
                return f"def {name}(value):\n    return value + 1\n"

            with (
                mock.patch.object(
                    analyzer,
                    "python_files_at_commit",
                    return_value={"vllm/api.py"},
                ),
                mock.patch.object(analyzer, "exact_file_renames", return_value={}),
                mock.patch.object(analyzer, "source_at_commit", side_effect=source),
            ):
                records = analyzer.discover_import_contract_candidates(
                    Path("."),
                    root,
                    "old",
                    "new",
                    {"vllm/api.py"},
                    exact_only=True,
                )
        self.assertEqual(len(records), 1)
        self.assertEqual(
            records[0]["_contract_delta"]["kind"],
            "imported_symbol_exactly_renamed",
        )
        self.assertEqual(records[0]["_contract_delta"]["new_symbol"], "new_name")

    def test_exact_import_ignores_version_guarded_attribute_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "patch.py"
            path.parent.mkdir()
            path.write_text(
                "import vllm\n"
                "if vllm_version_is('0.23.0'):\n"
                "    vllm.api.old_name = replacement\n"
                "else:\n"
                "    vllm.api.new_name = replacement\n",
                encoding="utf-8",
            )

            def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
                name = "old_name" if commit == "old" else "new_name"
                return f"def {name}(value):\n    return value\n"

            with (
                mock.patch.object(
                    analyzer,
                    "python_files_at_commit",
                    return_value={"vllm/api.py"},
                ),
                mock.patch.object(analyzer, "exact_file_renames", return_value={}),
                mock.patch.object(analyzer, "source_at_commit", side_effect=source),
            ):
                records = analyzer.discover_import_contract_candidates(
                    Path("."),
                    root,
                    "old",
                    "new",
                    {"vllm/api.py"},
                    exact_only=True,
                )
        self.assertEqual(records, [])

    def test_exact_adapted_free_function_signature_change_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "attn_utils.py"
            path.parent.mkdir()
            path.write_text(
                "# Adapt from https://github.com/vllm-project/vllm/blob/main/"
                "vllm/v1/worker/gpu/attn_utils.py\n\n"
                "def build_attn_metadata(seq_lens, causal):\n"
                "    return seq_lens, causal\n\n"
                "module.build_attn_metadata = build_attn_metadata\n",
                encoding="utf-8",
            )

            def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
                suffix = "" if commit == "old" else ", max_query_len=None"
                return (
                    f"def build_attn_metadata(seq_lens, causal{suffix}):\n"
                    "    return seq_lens, causal\n"
                )

            with mock.patch.object(
                analyzer, "source_at_commit", side_effect=source
            ):
                records = analyzer.discover_exact_adapted_callable_candidates(
                    Path("."),
                    root,
                    "old",
                    "new",
                    {"vllm/v1/worker/gpu/attn_utils.py"},
                )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["relation"], "adapted_free_function")
        self.assertEqual(
            records[0]["_contract_delta"]["kind"], "callable_signature_changed"
        )

    def test_adapted_local_helper_without_upstream_binding_is_not_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "vllm_ascend" / "local_kernel.py"
            path.parent.mkdir()
            path.write_text(
                "# Adapt from vllm/kernels.py\n\n"
                "def kernel(value):\n"
                "    return value\n",
                encoding="utf-8",
            )

            def source(_root: Path, commit: str, _file: str, _cache: dict) -> str:
                suffix = "" if commit == "old" else ", mode=None"
                return f"def kernel(value{suffix}):\n    return value\n"

            with mock.patch.object(
                analyzer, "source_at_commit", side_effect=source
            ):
                records = analyzer.discover_exact_adapted_callable_candidates(
                    Path("."),
                    root,
                    "old",
                    "new",
                    {"vllm/kernels.py"},
                )
        self.assertEqual(records, [])

    def test_imported_owner_profile_ignores_body_only_changes(self) -> None:
        old = "class Owner:\n    def run(self, value):\n        return value\n"
        body_only = "class Owner:\n    def run(self, value):\n        return value + 1\n"
        signature = "class Owner:\n    def run(self, value, mode=None):\n        return value\n"
        old_profile = analyzer.top_level_symbol_profile(old, "Owner")
        self.assertEqual(
            old_profile,
            analyzer.top_level_symbol_profile(body_only, "Owner"),
        )
        self.assertNotEqual(
            old_profile,
            analyzer.top_level_symbol_profile(signature, "Owner"),
        )

    def test_dismissed_and_stale_records_are_skipped(self) -> None:
        self.assertFalse(analyzer.record_actionable({"action": "dismiss"}))
        self.assertFalse(analyzer.record_actionable({"action": "stale"}))
        self.assertFalse(analyzer.record_actionable({"verification": {"status": "stale"}}))
        self.assertTrue(analyzer.record_actionable({"action": "review"}))


if __name__ == "__main__":
    unittest.main()
