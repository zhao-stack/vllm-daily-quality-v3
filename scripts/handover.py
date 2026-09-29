"""Portable execution helpers. Never writes the production watermark or credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PIN = '2c815be15c4e094447c38d67f666e590492f4c28'
SHEETS = ['优化点总表', '行动清单(P0-P1)', '跳过清单', '精确代码复核', '测试与收益', 'Review证据', '流水线与版本', '统计看板', '上游Bugfix', '全部PR分析', '上游功能接口']
FIVE = ['直接继承', '收益明确待适配', '收益不明确待验证', '平台差异可借鉴', '不可用']
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def read(file):
    return json.loads(Path(file).read_text(encoding='utf-8-sig'))


def write(file, obj):
    file = Path(file)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(file):
    return hashlib.sha256(Path(file).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None, 'Timestamp must carry timezone')
    return result


def validate_inputs(run):
    cfg = read(run / 'report_config.json')
    require(cfg['timezone'] == 'Asia/Hong_Kong', 'Timezone mismatch')
    require(Path(cfg['outputName']).name == cfg['outputName'] and '/' not in cfg['outputName'] and '\\' not in cfg['outputName'], 'outputName must be a basename')
    require(cfg['outputName'].endswith('.xlsx'), 'XLSX required')
    for key in ['vllmOld', 'vllmNew', 'ascend', 'verified', 'mainLys']:
        require(re.fullmatch('[0-9a-f]{40}', cfg[key]) is not None, f'Bad SHA: {key}')
    start, end = stamp(cfg['startIso']), stamp(cfg['endIso'])
    require(start < end, 'Empty/reversed time interval')
    prs = read(run / 'evidence/merged_prs.json')
    ids = [p['number'] for p in prs]
    require(len(ids) == len(set(ids)) == cfg['totalPrs'], 'PR count or duplicates')
    semantic = read(run / 'ai_semantic_analysis.json')
    require(set(semantic) == set(map(str, ids)), 'AI semantic coverage mismatch')
    reviews = read(run / 'evidence/pr_review_context.json')['records']
    for p in prs:
        require(start <= stamp(p['merged_at']) < end, f'Outside exact window: {p["number"]}')
        require(str(p['number']) in reviews, f'Missing Review: {p["number"]}')
        require((run / f'evidence/diffs/merge-{p["number"]}.diff').is_file(), 'Missing exact merge diff')
        for value in [p['merge_sha'], p['head_sha'], *p['parent_shas']]:
            require(re.fullmatch('[0-9a-f]{40}', value) is not None, 'Bad PR SHA')
        require(bool(p['parent_shas']), 'Missing parent')
        s = semantic[str(p['number'])]
        require(len(s) == 4 and type(s[3]) is bool and all(isinstance(x, str) and x.strip() for x in s[:3]), 'Malformed AI semantic row')
    return cfg, prs, semantic


def prepare_sample(args):
    run = Path(args.run_dir).resolve()
    require(not run.exists(), 'Run directory already exists; choose a NEW directory')
    shutil.copytree(ROOT / 'example/input', run)
    shutil.copy2(ROOT / 'runtime/build_report.mjs', run / 'build_report.mjs')
    shutil.copy2(ROOT / 'templates/main-lys-v3-11-sheets.xlsx', run / 'template.xlsx')
    if args.node_modules:
        modules = Path(args.node_modules).resolve()
        require((modules / '@oai/artifact-tool').exists(), 'artifact-tool missing from supplied bundled node_modules')
        if os.name == 'nt':
            # No shell interpolation; PowerShell receives literal single-quoted paths.
            quoted = lambda s: "'" + str(s).replace("'", "''") + "'"
            command = f"New-Item -ItemType Junction -Path {quoted(run / 'node_modules')} -Target {quoted(modules)} | Out-Null"
            subprocess.run(['powershell', '-NoProfile', '-Command', command], check=True)
        else:
            (run / 'node_modules').symlink_to(modules, target_is_directory=True)
    print(json.dumps({'prepared': str(run), 'offline_sample': True, 'production_state_modified': False}, ensure_ascii=False))


def pipeline(args):
    run = Path(args.run_dir).resolve()
    cfg, prs, _ = validate_inputs(run)
    workflow = Path(args.workflow_dir).resolve() if args.workflow_dir else ROOT / 'vendor/main-lys'
    if args.workflow_dir:
        git = lambda *a: subprocess.check_output(['git', '-C', str(workflow), *a], text=True).strip()
        require(git('rev-parse', 'HEAD') == cfg['mainLys'], 'Workflow HEAD != config mainLys')
        require(not git('status', '--porcelain', '--', 'scripts', 'templates', 'data/vllm/context'), 'Dirty workflow sources')
    else:
        require(cfg['mainLys'] == PIN, 'Config requests another workflow; provide --workflow-dir')
        equivalence = read(ROOT / 'example/input/evidence/main_lys_source_equivalence.json')
        for item in equivalence['sources']:
            require(digest(workflow / item['file']) == item['sha256'].lower(), f'Vendor source modified: {item["file"]}')
    for file, key in [('SKILL.md', 'skillBlob'), ('optimization_prompt.txt', 'promptBlob'), ('optimization_schema.json', 'schemaBlob')]:
        data = (workflow / 'templates' / file).read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        require(blob == cfg[key], f'Workflow blob mismatch: {file}')
        shutil.copy2(workflow / 'templates' / file, run / 'evidence' / ('main_lys_' + file))
    shutil.copy2(workflow / 'data/vllm/context/architecture.json', run / 'evidence/main_lys_architecture.json')
    commits = []
    for p in prs:
        diff = (run / f'evidence/diffs/merge-{p["number"]}.diff').read_text(encoding='utf-8')
        chunks = re.split(r'(?=^diff --git )', diff, flags=re.M)
        patches = {c.split('\n')[0].split(' b/', 1)[1]: c for c in chunks if c.startswith('diff --git ') and ' b/' in c.split('\n')[0]}
        commits.append({'sha': p['merge_sha'], 'pr_number': p['number'], 'base_ref': p['base_ref'], 'author': {'name': p['author'], 'email': ''}, 'date': p['merged_at'], 'parents': p['parent_shas'], 'message': f'{p["title"]} (#{p["number"]})\n\n{p["body"]}', 'stats': {'total_additions': sum(f['additions'] for f in p['files']), 'total_deletions': sum(f['deletions'] for f in p['files']), 'files_changed': len(p['files'])}, 'files': [{'filename': f['path'], 'status': f['changeType'].lower(), 'additions': f['additions'], 'deletions': f['deletions'], 'patch': patches.get(f['path'], '')} for f in p['files']]})
    date = cfg['startIso'][:10]
    data_dir = run / 'pipeline-data'
    input_path = data_dir / f'vllm/commits/{date}.json'
    output = data_dir / f'vllm/analysis/{date}.json'
    require(not output.exists(), 'Pipeline output already exists; use a fresh run to avoid stale receipts')
    write(input_path, {'date': date, 'repo': 'vllm-project/vllm', 'period_start': cfg['startIso'], 'period_end_exclusive': cfg['endIso'], 'commits': commits})
    receipt = {'prepared_at': datetime.now().astimezone().isoformat(), 'semantic_sha256': digest(run / 'ai_semantic_analysis.json'), 'manual_analysis_sha256': digest(run / 'analysis_data.mjs'), 'template_sha256': digest(run / 'template.xlsx'), 'pipeline_not_yet_run': True}
    write(run / 'ai-first-input-receipt.json', receipt)
    cmd = [sys.executable, '-X', 'utf8', str(workflow / 'scripts/deep_analyze.py'), '--repo', 'vllm-project/vllm', '--date', date, '--data-dir', str(data_dir)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
    (run / 'pipeline.log').write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
    empty = not prs and result.returncode == 1 and 'No commits for' in result.stdout
    require(result.returncode == 0 or empty, f'Pipeline failed ({result.returncode}); see pipeline.log')
    if empty:
        write(output, {'date': date, 'repo': 'vllm-project/vllm', 'commits': [], 'empty_input_adapter': True, 'original_exit_code': 1, 'daily_summary': '区间无合入PR；原程序空输入返回1，不作为成功处理记录。'})
    require(len(read(output)['commits']) == len(prs), 'Original pipeline output count mismatch')
    write(run / 'pipeline-execution-receipt.json', {'executed': True, 'source_sha': cfg['mainLys'], 'command': cmd, 'exit_code': result.returncode, 'empty_input': empty, 'program_sha256': digest(workflow / 'scripts/deep_analyze.py'), 'input_sha256': digest(input_path), 'program_output_sha256': digest(output), 'ai_semantic_sha256': receipt['semantic_sha256'], 'manual_analysis_sha256': receipt['manual_analysis_sha256'], 'commit_count': len(prs), 'external_llm_executed': False, 'daily_refresh_executed': False})
    print(json.dumps({'pipeline_executed': True, 'exit_code': result.returncode, 'empty_input': empty, 'count': len(prs)}, ensure_ascii=False))


def workbook_cells(file):
    with zipfile.ZipFile(file) as z:
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            strings = [''.join(t.text or '' for t in si.findall('.//m:t', NS)) for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', NS)]
        sheets = ET.fromstring(z.read('xl/workbook.xml')).findall('m:sheets/m:sheet', NS)
        rels = {x.attrib['Id']: x.attrib['Target'] for x in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        cells, formulas, names = {}, {}, []
        for sheet in sheets:
            name = sheet.attrib['name']; names.append(name)
            target = rels[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
            file = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            cells[name] = {}; formulas[name] = {}
            for c in ET.fromstring(z.read(file)).findall('.//m:sheetData/m:row/m:c', NS):
                require(c.attrib.get('t') != 'e', f'Excel error cell {name}!{c.attrib["r"]}')
                v = c.find('m:v', NS)
                value = v.text if v is not None else ''
                if c.attrib.get('t') == 's': value = strings[int(value)]
                if c.attrib.get('t') == 'inlineStr': value = ''.join(t.text or '' for t in c.findall('.//m:t', NS))
                require('[object Object]' not in str(value), 'Object serialization artifact')
                cells[name][c.attrib['r']] = value
                formula = c.find('m:f', NS)
                if formula is not None:
                    require(v is not None, 'Missing formula cache')
                    formulas[name][c.attrib['r']] = formula.text
        return names, cells, formulas


def audit(args):
    from schema_check import check_schema, validate
    run = Path(args.run_dir).resolve()
    cfg, prs, semantic = validate_inputs(run)
    rec = read(run / 'pipeline-execution-receipt.json')
    require(rec['executed'] and rec['source_sha'] == cfg['mainLys'], 'Missing pipeline execution')
    require(rec['ai_semantic_sha256'] == digest(run / 'ai_semantic_analysis.json') and rec['manual_analysis_sha256'] == digest(run / 'analysis_data.mjs'), 'AI input changed after pipeline; new audited run required')
    require(rec['program_output_sha256'] == digest(run / f'pipeline-data/vllm/analysis/{cfg["startIso"][:10]}.json'), 'Pipeline output modified')
    quality = read(run / 'upstream-quality-records.json')
    v3 = read(run / 'optimization_schema_v3.json')
    schema = read(run / 'evidence/main_lys_optimization_schema.json')
    check_schema(schema)
    for row in v3: validate(row, schema)
    strict = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'skills/vllm-ascend-upstream-quality/scripts/validate_records.py'), '--input', str(run / 'upstream-quality-records.json'), '--strict'], capture_output=True, text=True, encoding='utf-8')
    require(strict.returncode == 0, strict.stdout + strict.stderr)
    write(run / 'strict-validation.json', json.loads(strict.stdout))
    ids = {p['number'] for p in prs}
    getids = lambda rows, prefix: [int(r['record_id'].removeprefix(prefix)) for r in rows]
    bugs = getids(quality['bugfix_rows'], 'BUG-')
    opts = getids(quality['optimization_rows'], 'OPT-')
    feats = getids(quality['feature_interface_rows'], 'FEAT-')
    for values in [bugs, opts, feats]: require(len(values) == len(set(values)) and set(values) <= ids, 'Duplicate/unknown classified PR')
    require(set(bugs) == {int(k) for k, v in semantic.items() if v[3]}, 'Bugfix semantic coverage mismatch')
    require({int(r['pr_number']) for r in v3} == set(opts), 'Optimization schema coverage mismatch')
    allrows = read(run / 'all_pr_rows.json')
    require(len(allrows) == len(prs) and {r['number'] for r in allrows} == ids, 'All PR page coverage mismatch')
    counts = {'all_prs': len(prs), 'bugfix': len(bugs), 'optimization': len(opts), 'feature_interface': len(feats)}
    if args.records_only:
        print(json.dumps({'records_valid': True, 'counts': counts}, ensure_ascii=False)); return
    build = read(run / 'build-verification.json')
    require(build['valid'] and all(build['counts'][k] == v for k, v in counts.items()), 'Build counts mismatch')
    for key in ['formula_error_scan_pre', 'formula_error_scan_saved']:
        require('matched 0 entries' in build[key], f'Formula scan not clean: {key}')
    report = run / cfg['outputName']
    names, cells, formulas = workbook_cells(report)
    template_names, _, _ = workbook_cells(run / 'template.xlsx')
    require(names == template_names == SHEETS, 'Eleven-sheet template order mismatch')
    require(not any('版本分支匹配' in v or '证据等级' in v for a, v in cells['上游Bugfix'].items() if re.fullmatch('[A-Z]+8', a)), 'Forbidden Bugfix column')
    for name, col, start, expected, prefix in [('全部PR分析', 'A', 5, ids, '#'), ('优化点总表', 'B', 5, set(opts), '#'), ('上游Bugfix', 'A', 9, set(bugs), 'BUG-'), ('上游功能接口', 'A', 5, set(feats), 'FEAT-')]:
        actual = [int(v.removeprefix(prefix)) for a, v in cells[name].items() if re.fullmatch(col + r'\d+', a) and int(a[len(col):]) >= start and v and v.startswith(prefix)]
        require(len(actual) == len(expected) and set(actual) == expected, f'Worksheet ID/tail mismatch: {name}')
    priorities = Counter(r['priority'] for r in v3)
    categories = Counter(r['five_category'] for r in quality['optimization_rows'])
    values = [len(opts), *(priorities[k] for k in ['P0','P1','P2','P3','skip']), sum(r['migration_method'] == 'patch-sync' for r in v3), *(categories[k] for k in FIVE)]
    for row, expected in enumerate(values, 5): require(float(cells['统计看板'][f'B{row}']) == expected, 'Dashboard count mismatch')
    probe = read(run / 'recalculation-probe.json')
    require(probe['valid'] and probe['before'] == probe['restored'] and not probe['exported'], 'Recalculation probe failed')
    for name in SHEETS:
        image = run / 'renders' / (re.sub(r'[\\/:*?"<>|]', '_', name) + '.png')
        require(image.is_file() and image.stat().st_size > 100, f'Missing sheet render: {name}')
    if args.compare_example:
        expected = read(ROOT / 'example/expected/final-audit.json')
        require(counts == {'all_prs': expected['all_pr_count'], 'bugfix': expected['bugfix_count'], 'optimization': expected['optimization_count'], 'feature_interface': expected['feature_interface_count']}, 'Sample count mismatch')
        for f in ['all_pr_rows.json', 'bugfix_rows.json', 'optimization_rows.json', 'feature_interface_rows.json']:
            require(read(run / f) == read(ROOT / 'example/expected' / f), f'Sample semantic field changed: {f}')
        old = next(f for f in (ROOT / 'example/expected').glob('vLLM*.xlsx'))
        _, expected_cells, expected_formulas = workbook_cells(old)
        # Metadata wording is deliberately parameterized. Business headers/rows/formulas must match.
        for name in SHEETS:
            if name == '流水线与版本': continue
            start = 8 if name == '上游Bugfix' else 4
            subset = lambda d: {a: v for a, v in d.items() if int(re.search(r'\d+$', a)[0]) >= start and v != ''}
            require(subset(cells[name]) == subset(expected_cells[name]), f'Sample workbook content changed: {name}')
            require(formulas[name] == expected_formulas[name], f'Sample formula changed: {name}')
    result = {'structural_valid': True, 'visual_review_required': True, 'source_semantic_review_required_for_live_run': True, 'report_sha256': digest(report), 'counts': counts, 'five_category': dict(categories), 'sample_comparison': args.compare_example, 'production_state_modified': False}
    write(run / 'portable-audit.json', result)
    print(json.dumps(result, ensure_ascii=False))


def verify(args):
    manifest = read(ROOT / 'manifest.json')
    for item in manifest['files']:
        file = (ROOT / item['path']).resolve()
        require(file.is_relative_to(ROOT), 'Unsafe manifest path')
        require(file.is_file() and digest(file) == item['sha256'], f'Package changed/missing: {item["path"]}')
    require(workbook_cells(ROOT / 'templates/main-lys-v3-11-sheets.xlsx')[0] == SHEETS, 'Template mismatch')
    seed = read(ROOT / 'state/resume-seed.json')
    require(digest(ROOT / 'templates/main-lys-v3-11-sheets.xlsx') == seed['source_report_sha256'].lower(), 'State/template hash mismatch')
    print(json.dumps({'package_valid': True, 'files': len(manifest['files']), 'template_sheets': 11, 'watermark_snapshot': seed['last_successful_cutoff'], 'production_state_modified': False}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('verify')
    p = sub.add_parser('prepare-sample'); p.add_argument('--run-dir', required=True); p.add_argument('--node-modules')
    p = sub.add_parser('pipeline'); p.add_argument('--run-dir', required=True); p.add_argument('--workflow-dir')
    p = sub.add_parser('audit'); p.add_argument('--run-dir', required=True); p.add_argument('--records-only', action='store_true'); p.add_argument('--compare-example', action='store_true')
    args = parser.parse_args()
    {'verify': verify, 'prepare-sample': prepare_sample, 'pipeline': pipeline, 'audit': audit}[args.command](args)


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
