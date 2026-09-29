"""Offline contract/negative tests. Does not collect GitHub or mutate production state."""
import argparse
import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from handover import read, write, validate_inputs, ROOT as PACKAGE
from schema_check import check_schema, validate, SchemaError


class ContractTests(unittest.TestCase):
    def test_successful_sample(self):
        cfg, prs, semantic = validate_inputs(ROOT / 'example/input')
        self.assertEqual(len(prs), 52)
        self.assertEqual(sum(v[3] for v in semantic.values()), 28)

    def test_schema_negative(self):
        schema = read(ROOT / 'vendor/main-lys/templates/optimization_schema.json')
        check_schema(schema)
        row = read(ROOT / 'example/expected/optimization_schema_v3.json')[0]
        validate(row, schema)
        for key, bad in [('full_sha', 'wrong'), ('category', 'invalid'), ('optimization_point', 'x' * 101), ('migration_steps', []), ('patch_conflict', 'false'), ('commit_url', 'not-a-uri')]:
            with self.subTest(key=key), self.assertRaises(SchemaError):
                validate({**row, key: bad}, schema)
        with self.assertRaises(RuntimeError): check_schema({'unknown-new-keyword': True})
        with self.assertRaises(SchemaError): validate({k: v for k, v in row.items() if k != 'migration_steps'}, schema)
        trial = {**row, 'migration_method': 'skip', 'ascend_value': 'high'}
        with self.assertRaises(SchemaError): validate(trial, schema)

    def test_duplicate_and_boundary(self):
        source = ROOT / 'example/input'
        with tempfile.TemporaryDirectory(prefix='vllm-handover-contract-') as d:
            run = Path(d)
            cfg = read(source / 'report_config.json')
            prs = read(source / 'evidence/merged_prs.json')
            for f in ['report_config.json', 'ai_semantic_analysis.json', 'evidence/pr_review_context.json']:
                write(run / f, read(source / f))
            write(run / 'evidence/merged_prs.json', prs + [prs[0]])
            with self.assertRaisesRegex(ValueError, 'count or duplicates'): validate_inputs(run)
            prs[0]['merged_at'] = cfg['endIso']
            write(run / 'evidence/merged_prs.json', prs)
            with self.assertRaisesRegex(ValueError, 'Outside exact window'): validate_inputs(run)
            prs[0]['merged_at'] = cfg['startIso']
            # Exact start is included, so validation progresses to missing-diff gate.
            write(run / 'evidence/merged_prs.json', prs)
            with self.assertRaisesRegex(ValueError, 'Missing exact merge diff'): validate_inputs(run)


if __name__ == '__main__': unittest.main()
