"""Convert an isolated prepared SAMPLE to a synthetic zero-record test (never live data)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from handover import read, write

run = Path(sys.argv[1]).resolve()
if (run / 'pipeline-execution-receipt.json').exists():
    raise SystemExit('Refuse to modify a run that already executed the pipeline')
cfg = read(run / 'report_config.json')
cfg['totalPrs'] = 0
cfg['outputName'] = 'synthetic-zero-test.xlsx'
cfg['synthetic_test_only'] = True
write(run / 'report_config.json', cfg)
write(run / 'ai_semantic_analysis.json', {})
write(run / 'evidence/merged_prs.json', [])
write(run / 'evidence/pr_review_context.json', {'records': {}})
write(run / 'direct-source-review.json', {'metadata': {'gate': '合成零记录测试；不表示真实窗口无PR。', 'full_analyzer_executed': False}, 'direct_findings': []})
(run / 'analysis_data.mjs').write_text('''import fs from 'node:fs';
export const PERIOD=JSON.parse(fs.readFileSync(new URL('./report_config.json',import.meta.url),'utf8'));
export const ALL_PR_ANALYSIS={};
export const BUGFIX_CONFIG={},FEATURE_CONFIG={};
export const BUGFIX_IDS=[],FEATURE_IDS=[],OPTIMIZATIONS=[];
export const localTimestamp=s=>new Date(Date.parse(s)+8*3600000).toISOString().slice(0,23).replace('T',' ');
export const localDate=s=>localTimestamp(s).slice(0,10);
''', encoding='utf-8')
print('Prepared synthetic zero-record test; no production state touched')
