import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
import { ALL_PR_ANALYSIS, BUGFIX_CONFIG, BUGFIX_IDS, FEATURE_CONFIG, FEATURE_IDS, OPTIMIZATIONS, PERIOD, localDate, localTimestamp } from "./analysis_data.mjs";

const RUN_DIR = path.dirname(fileURLToPath(import.meta.url));
const TEMPLATE = path.join(RUN_DIR, "template.xlsx");
const OUTPUT = path.join(RUN_DIR, PERIOD.outputName);
const EVIDENCE = path.join(RUN_DIR, "evidence");
const DATA_START = 5;
const SHEETS = ["优化点总表", "行动清单(P0-P1)", "跳过清单", "精确代码复核", "测试与收益", "Review证据", "流水线与版本", "统计看板", "上游Bugfix", "上游功能接口", "全部PR分析"];
const TABLE_SPECS = {
  "优化点总表": ["OptimizationV3Table0825", "TableStyleMedium2"],
  "行动清单(P0-P1)": ["ActionV3Table0825", "TableStyleMedium9"],
  "跳过清单": ["SkipV3Table0825", "TableStyleMedium4"],
  "精确代码复核": ["ExactReviewTable0825", "TableStyleMedium2"],
  "测试与收益": ["TestBenefitTable0825", "TableStyleMedium7"],
  "Review证据": ["ReviewEvidenceTable0825", "TableStyleMedium2"],
  "流水线与版本": ["PipelineInfoTable0825", "TableStyleMedium2"],
  "上游功能接口": ["FeatureInterfaceTable0827", "TableStyleMedium2"],
  "全部PR分析": ["AllPRAnalysisTable0825", "TableStyleMedium2"],
};
const EXPECTED_FINAL = { "auto-inherit": "理论直接继承", "cherry-pick": "理论直接继承", "patch-sync": "需要适配", "idea-copy": "待POC", skip: "当前不适用" };

const readJson = async (file) => JSON.parse(await fs.readFile(file, "utf8"));
const PROGRAM_ANALYSIS = await readJson(path.join(RUN_DIR,"pipeline-data","vllm","analysis",`${PERIOD.startIso.slice(0,10)}.json`));
const countBy = (rows, key) => rows.reduce((acc, row) => ((acc[row[key]] = (acc[row[key]] || 0) + 1), acc), {});
const excelDate = (yyyyMmDd) => new Date(`${yyyyMmDd}T00:00:00Z`);
const excelLocalTime = value => new Date(value.replace(" ", "T")+"Z");
const keyFiles = (pr, limit = 5) => [...pr.files].sort((a,b)=>Number(!/^(vllm\/|csrc\/)/.test(a.path))-Number(!/^(vllm\/|csrc\/)/.test(b.path))).slice(0, limit).map((f) => f.path).join("; ");
const allFiles = (pr) => pr.files.map((f) => f.path).join("; ");

function deleteTables(sheet) {
  for (const table of [...sheet.tables.items]) table.delete();
}
function nonEmptyValues(values, columns) {
  // Null placeholders preserve legal ranges without adding a counted record.
  return values.length ? values : [Array(columns).fill(null)];
}
function addTable(sheet, range, name, style) {
  const table = sheet.tables.add(range, true, name);
  table.style = style;
  table.showHeaders = true;
  table.showFilterButton = true;
  return table;
}
function styleDataRange(sheet, range, rowHeight = 80) {
  const body = sheet.getRange(range);
  body.format.wrapText = true;
  body.format.verticalAlignment = "top";
  body.format.font = { name: "Carlito", size: 9, color: "#333333" };
  body.format.rowHeight = rowHeight;
}
function fitWrittenRows(workbook, ends) {
  const specs = {'优化点总表':[5,'P'],'行动清单(P0-P1)':[5,'K'],'跳过清单':[5,'H'],'精确代码复核':[5,'I'],'测试与收益':[5,'F'],'Review证据':[5,'G'],'流水线与版本':[5,'C'],'上游Bugfix':[9,'U'],'上游功能接口':[5,'P'],'全部PR分析':[5,'J']};
  const widthFixes = {'优化点总表':{D:20,E:52,K:24,N:18},'行动清单(P0-P1)':{F:42,G:48,K:54},'跳过清单':{E:22,H:58},'精确代码复核':{B:22,D:22,E:34,F:70,H:54,I:45}};
  for (const [name, widths] of Object.entries(widthFixes)) for (const [col,width] of Object.entries(widths)) workbook.worksheets.getItem(name).getRange(`${col}4:${col}${ends[name]}`).format.columnWidth=width;
  for (const [name,[start,lastCol]] of Object.entries(specs)) {
    const sheet=workbook.worksheets.getItem(name);
    const cols='ABCDEFGHIJKLMNOPQRSTU'.slice(0,lastCol.charCodeAt(0)-64).split('');
    for(let row=start;row<=ends[name];row++) {
      const values=sheet.getRange(`A${row}:${lastCol}${row}`).values[0];
      const lines=values.map((value,index)=>{
        if(value===null||value===undefined||typeof value!=='string')return 1;
        const width=sheet.getRange(`${cols[index]}${row}`).format.columnWidth||20;
        return value.split('\n').reduce((sum,line)=>sum+Math.max(1,Math.ceil([...line].reduce((n,ch)=>n+(/[^\x00-\xff]/.test(ch)?1.75:1),0)/Math.max(8,width*1.13-2))),0);
      });
      sheet.getRange(`A${row}:${lastCol}${row}`).format.rowHeight=Math.min(330,Math.max(name==='流水线与版本'?46:70,Math.max(...lines)*13.5+12));
    }
    const tail=sheet.getRange(`A${ends[name]+1}:${lastCol}${Math.max(120,ends[name]+1)}`);
    tail.unmerge();
    tail.clear({applyTo:'all'});
  }
}
function setHeader(sheet, range) {
  const header = sheet.getRange(range);
  header.format.wrapText = true;
  header.format.verticalAlignment = "center";
  header.format.horizontalAlignment = "center";
  header.format.font = { name: "Carlito", bold: true, size: 10, color: "#FFFFFF" };
  header.format.fill = "#4472C4";
  header.format.rowHeight = 34;
}
function applyAlternatingFill(sheet, startRow, endRow, lastColumn, odd = "#D9EAF7", even = "#FFFFFF") {
  for (let row = startRow; row <= endRow; row += 1) sheet.getRange(`A${row}:${lastColumn}${row}`).format.fill = row % 2 === startRow % 2 ? odd : even;
}
function isDifference(v3, report) {
  const allowed = {
    "auto-inherit": ["直接继承"],
    "cherry-pick": ["直接继承"],
    "patch-sync": ["收益明确待适配"],
    "idea-copy": ["收益不明确待验证", "平台差异可借鉴"],
    skip: ["不可用"],
  };
  return !(allowed[v3.migration_method] || []).includes(report.five_category);
}
function exactExecution(report) {
  if (report.five_category === "收益明确待适配") return `同步适配：${report.extra_work}；按验收方案完成NPU验证`;
  if (report.five_category === "收益不明确待验证" && FEATURE_CONFIG[Number(report.record_id.slice(4))]?.[1] === "需要适配") return "先完成已确认的接口适配与正确性回归；再独立验证性能收益，详见功能接口页";
  if (report.five_category === "收益不明确待验证") return "先做NPU profiling/最小POC，收益成立后再实现";
  if (report.five_category === "直接继承") return "直接继承；做NPU回归与收益确认";
  if (report.five_category === "平台差异可借鉴") return "不移植上游平台代码；按NPU有效路径借鉴方法并验证";
  if (FEATURE_CONFIG[Number(report.record_id.slice(4))]?.[1] === "需要适配") return "新增收益不计；已确认的配置/接口适配仍须执行，详见功能接口页";
  return "无本次新增可迁移收益；保留NPU既有实现和能力门禁";
}
function reviewCounts(review) {
  const inline = (review.reviewThreads?.nodes || []).reduce((sum, thread) => sum + (thread.comments?.totalCount || 0), 0);
  return { reviews: review.reviews?.totalCount || 0, inline, comments: review.comments?.totalCount || 0 };
}
async function findMain2MainReport() {
  const directReview = path.join(RUN_DIR, "direct-source-review.json");
  try { return { file: directReview, report: await readJson(directReview) }; } catch {}
  const upstreamExact = path.join(RUN_DIR, "vllm-interface", "vllm-interface-pr-report.json");
  try {
    const report = await readJson(upstreamExact);
    report.findings = [...(report.findings || []), ...(report.review_findings || [])];
    return { file: upstreamExact, report };
  } catch {}
  const stack = [path.join(RUN_DIR, "main2main-cache-ascii"), path.join(RUN_DIR, "main2main-cache")];
  while (stack.length) {
    const current = stack.pop();
    let entries = [];
    try { entries = await fs.readdir(current, { withFileTypes: true }); } catch { continue; }
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.name === "main2main-range-report.json") return { file: full, report: await readJson(full) };
    }
  }
  throw new Error("pinned source relationship review is required before workbook authoring");
}

function makeV3(pr, opt) {
  const fullSha = pr.merge_sha;
  const migrationSteps = opt.method === "skip" ? undefined : opt.steps;
  const testVerification = ["P0", "P1", "P2"].includes(opt.priority) ? opt.tests : undefined;
  return {
    _thought_process: `公开判定摘要：${opt.point}。${opt.valueReason} 最终路由：${opt.value}/${opt.method}/${opt.priority}；源码依据与局限见精确复核及测试页。`,
    sha: fullSha.slice(0, 8), full_sha: fullSha, date: localDate(pr.merged_at), repo: "vllm-project/vllm",
    title: pr.title, pr_number: String(pr.number), pr_url: pr.url, commit_url: `https://github.com/vllm-project/vllm/commit/${fullSha}`,
    upstream_status: "merged", merged_at: pr.merged_at, author: { name: pr.author, email: "" }, labels: pr.labels,
    category: opt.category, target_ascend_module: opt.target, change_type: opt.changeType, optimization_point: opt.point,
    architecture_candidate_keys: opt.architectureHintKeys || [],
    verified_patch_bindings: opt.verifiedPatchBindings || [],
    affected_files: pr.files.map((f) => f.path), ascend_value: opt.value, ascend_value_reason: opt.valueReason,
    ascend_impact_ref: { ascend_affected: opt.final !== "当前不适用", functionality: opt.relation, source: `pinned vllm-ascend ${PERIOD.ascend}` },
    migration_method: opt.method, ...(migrationSteps ? { migration_steps: migrationSteps } : {}), patch_conflict: opt.patch,
    ...(opt.patch ? { patch_conflict_detail: opt.relation, patch_matched_keys: opt.patchKeys || [] } : { patch_matched_keys: [] }),
    priority: opt.priority, priority_reason: opt.valueReason.slice(0, 80), effort_estimate: opt.effort, risk_level: opt.risk,
    ...(testVerification ? { test_verification: testVerification } : {}), status: opt.method === "skip" ? "skipped" : "pending",
    assignee: opt.owner, key_files: allFiles(pr), blocking_issues: opt.blockingIssues || [], review_notes: opt.review, low_confidence: opt.lowConfidence || false, confidence_reason: opt.confidenceReason || null,
  };
}
function makeReport(pr, opt) {
  const effort = { S: "0.5-1人天", M: "1-3人天", L: "3-5人天", XL: "5-10人天" }[opt.effort];
  return {
    record_id: `OPT-${pr.number}`, upstream_pr: pr.url, title: pr.title, merged_at: pr.merged_at, merge_sha: pr.merge_sha,
    pr_parent_sha: pr.parent_shas?.[0], pr_head_sha: pr.head_sha,
    description: opt.description, mechanism: opt.mechanism, change_point: allFiles(pr), upstream_metric: opt.metric,
    upstream_result: opt.result, upstream_test_conditions: opt.conditions, evidence_url: pr.url, affected_code: allFiles(pr),
    ascend_relation: opt.relation, npu_applicability: opt.valueReason, benefit_acquisition: opt.final,
    five_category: opt.fiveCategory, secondary_category: opt.secondaryCategory, reason: opt.relation,
    evidence_level: opt.evidenceLevel || "E2",
    expected_npu_kpi: `${opt.metric}；NPU收益待实测，不预填。`, extra_work: opt.final === "需要适配" ? "Adapter/实现同步+NPU回归" : opt.final === "待POC" ? "NPU profiling+最小POC" : opt.final === "理论直接继承" ? "NPU最小回归" : "无移植；保留方法",
    estimated_effort: effort, verification_priority: ["P0", "P1"].includes(opt.priority) ? "高" : opt.priority === "P2" ? "中" : "低",
    adaptation_priority: opt.final === "需要适配" ? "高" : opt.final === "待POC" ? "中" : "低", verification_plan: opt.plan,
    npu_result: "未验证", action: opt.final === "需要适配" ? "modify" : opt.final === "当前不适用" ? "dismiss" : "review",
    ...(opt.final === "需要适配" ? { action_gate: { relationship_verified: true, contract_changed: true, runtime_reachable: true, version_lane_matches: true } } : {}),
    status: opt.final === "需要适配" ? "待适配" : opt.final === "待POC" ? "待POC" : opt.final === "理论直接继承" ? "待回归" : "静态闭环",
    owner: opt.owner, review_notes: opt.review, notes: `main-lys@${PERIOD.mainLys.slice(0, 8)}；migration=${opt.method}；target=${opt.target}`,
    ...(opt.borrow ? { borrow_point: opt.borrow } : {}),
    ...(BUGFIX_IDS.includes(pr.number) ? { cross_table_reason: opt.crossReason || "同一PR既修复缺陷，也具备独立的资源/性能收益机制，分别进入两张管理表。" } : {}),
  };
}
function validateV3(records) {
  const required = ["_thought_process", "sha", "full_sha", "date", "repo", "title", "commit_url", "category", "target_ascend_module", "change_type", "optimization_point", "affected_files", "ascend_value", "ascend_value_reason", "migration_method", "patch_conflict", "priority", "priority_reason", "effort_estimate", "risk_level", "status", "key_files"];
  const enums = {
    ascend_value: ["high", "medium", "low", "none"], migration_method: ["cherry-pick", "patch-sync", "idea-copy", "config-align", "auto-inherit", "skip"],
    priority: ["P0", "P1", "P2", "P3", "skip"], effort_estimate: ["S", "M", "L", "XL"], risk_level: ["high-risk", "medium-risk", "low-risk"],
  };
  const errors = [];
  for (const rec of records) {
    for (const key of required) if (!(key in rec)) errors.push(`#${rec.pr_number || "?"} missing ${key}`);
    if (!/^[0-9a-f]{40}$/.test(rec.full_sha)) errors.push(`#${rec.pr_number} bad full_sha`);
    if (!Array.isArray(rec.affected_files) || rec.affected_files.length === 0) errors.push(`#${rec.pr_number} empty files`);
    for (const [key, values] of Object.entries(enums)) if (!values.includes(rec[key])) errors.push(`#${rec.pr_number} invalid ${key}`);
    if (rec.migration_method !== "skip" && !rec.migration_steps?.length) errors.push(`#${rec.pr_number} missing migration_steps`);
    if (["P0", "P1", "P2"].includes(rec.priority) && !rec.test_verification?.length) errors.push(`#${rec.pr_number} missing test_verification`);
    if (rec.migration_method === "skip" && !(rec.ascend_value === "none" && rec.priority === "skip" && rec.target_ascend_module === "none")) errors.push(`#${rec.pr_number} invalid skip tuple`);
  }
  return { valid: errors.length === 0, errors, record_count: records.length, schema: "UpstreamOptimizationRecordV3" };
}

function bugfixRow(pr) {
  const [category, conclusion, owner, relation, overrides = {}] = BUGFIX_CONFIG[pr.number];
  const [problem, solution] = ALL_PR_ANALYSIS[pr.number];
  const reachable = overrides.reachable ?? (conclusion === "待确认" ? "未知" : conclusion === "不受影响" ? "否" : "是");
  const enters = overrides.enters ?? (conclusion === "直接继承修复" ? "是" : conclusion === "待确认" ? "未知" : "否");
  const equivalent = overrides.equivalent ?? (conclusion === "需要适配" ? "否" : conclusion === "待确认" ? "未知" : "不适用");
  const priority = overrides.priority ?? (conclusion === "需要适配" ? "P1" : conclusion === "直接继承修复" ? "P2" : "P3");
  const action = conclusion === "需要适配" ? "modify" : ["直接继承修复", "待确认"].includes(conclusion) ? "review" : "dismiss";
  const status = conclusion === "需要适配" ? "待适配" : conclusion === "直接继承修复" ? "待最小回归" : conclusion === "待确认" ? "待确认" : "静态闭环";
  const scenario = overrides.scenario || `${category}对应的实际功能配置。`;
  const verification = overrides.verification ?? (conclusion === "需要适配" ? `在NPU复现“${problem}”，同步${keyFiles(pr, 3)}的等价语义后做修复前后A/B。` : conclusion === "直接继承修复" ? `在NPU最小复现并回归${keyFiles(pr, 3)}，确认共享修复已进入且输出/错误符合预期。` : `维持当前有效实现；若启用该平台/模型路径，再以PR #${pr.number}建立专项回归。`);
  return {
    record_id: `BUG-${pr.number}`, upstream_pr: pr.url, url: pr.url, title: pr.title, merged_at: pr.merged_at, merged_local: localTimestamp(pr.merged_at), merge_sha: pr.merge_sha,
    pr_parent_sha: pr.parent_shas?.[0], pr_head_sha: pr.head_sha,
    summary: problem, category, fix: `修改 ${keyFiles(pr, 6)}；${solution}`, scenario,
    relation, reachable, enters, equivalent, conclusion, causal: relation, action, priority, verification, status, owner,
    upstream_fix_point: `修改 ${keyFiles(pr, 6)}；${solution}`, impact_scenario: scenario,
    ascend_relation: relation, source_reachable: reachable, fix_enters_ascend: enters, equivalent_handling: equivalent,
    lane_matches: overrides.lane_matches || "是", basis: relation, validation_advice: verification, evidence_level: overrides.evidenceLevel || "E2",
    ...(action === "modify" ? { action_gate: { relationship_verified: true, contract_changed: true, runtime_reachable: true, version_lane_matches: true } } : {}),
    ...(OPTIMIZATIONS.some((item) => item.pr === pr.number) ? { cross_table_reason: OPTIMIZATIONS.find((item) => item.pr === pr.number).crossReason || "同一PR既修复缺陷，也具备独立的资源/性能收益机制，分别进入两张管理表。" } : {}),
    notes: `分支=${pr.base_ref}；GraphQL mergedAt=${pr.merged_at}；head=${pr.head_sha}；files=${pr.files.length}`,
  };
}

function featureRow(pr) {
  const [category, conclusion, owner, relation, action] = FEATURE_CONFIG[pr.number];
  const [problem, solution, why] = ALL_PR_ANALYSIS[pr.number];
  const priority = conclusion === "需要适配" ? "P1" : conclusion.includes("直接继承") ? "P2" : "P3";
  const status = conclusion === "需要适配" ? "待适配" : conclusion.includes("直接继承") ? "待回归" : conclusion === "当前不适用" ? "静态闭环" : "待POC";
  return {
    record_id: `FEAT-${pr.number}`, url: pr.url, title: pr.title,
    merged_local: localTimestamp(pr.merged_at), merge_sha: pr.merge_sha,
    category, problem, solution, why, relation, conclusion, action,
    priority, status, owner, files: keyFiles(pr, 6),
  };
}

function buildMainSheet(workbook, rows) {
  const sheet = workbook.worksheets.getItem("优化点总表");
  deleteTables(sheet); sheet.getRange("A4:P60").clear({ applyTo: "contents" });
  sheet.getRange("A1").values = [["vLLM 上游优化分析｜main-lys V3｜成功水位至本次触发"]];
  sheet.getRange("A2").values = [[`区间：[${PERIOD.start}, ${PERIOD.end}) ${PERIOD.timezone} ｜ 全部合入PR ${PERIOD.totalPrs}条，优化${rows.length}条 ｜ 流程：AI语义分析→main-lys V3归类→pinned源码精确复核`]];
  const headers = ["V3优先级", "PR", "合入日期", "分类", "优化点", "Ascend价值", "V3迁移策略", "目标Ascend模块", "Patch冲突", "第一责任人", "五类最终结论", "结论差异", "工作量", "风险", "关键文件", "PR证据"];
  sheet.getRange("A4:P4").values = [headers];
  const values = rows.map(({ pr, v3, report }) => [v3.priority, `#${pr.number}`, excelDate(v3.date), v3.category, v3.optimization_point, v3.ascend_value, v3.migration_method, v3.target_ascend_module, v3.patch_conflict ? "是" : "否", report.owner, report.five_category, null, v3.effort_estimate, v3.risk_level, keyFiles(pr), pr.url]);
  if (!values.length) values.push(...nonEmptyValues(values,16)); const last = DATA_START + values.length - 1;
  sheet.getRange(`A${DATA_START}:P${last}`).values = values;
  sheet.getRange(`L${DATA_START}:L${last}`).values = nonEmptyValues(rows.map(({ v3, report }) => [isDifference(v3, report) ? "有差异" : "无差异"]),1);
  sheet.getRange(`C${DATA_START}:C${last}`).format.numberFormat = "yyyy-mm-dd";
  styleDataRange(sheet, `A${DATA_START}:P${last}`, 78); sheet.getRange(`A${DATA_START}:D${last}`).format.horizontalAlignment = "center"; sheet.getRange(`F${DATA_START}:N${last}`).format.horizontalAlignment = "center";
  setHeader(sheet, "A4:P4"); addTable(sheet, `A4:P${last}`, ...TABLE_SPECS["优化点总表"]); applyAlternatingFill(sheet, DATA_START, last, "P");
  const priorityRange = sheet.getRange(`A${DATA_START}:A${last}`); priorityRange.conditionalFormats.deleteAll();
  priorityRange.conditionalFormats.add("containsText", { text: "P0", format: { fill: "#F4CCCC", font: { bold: true, color: "#9C0006" } } });
  priorityRange.conditionalFormats.add("containsText", { text: "P1", format: { fill: "#FFC000", font: { bold: true, color: "#333333" } } });
  priorityRange.conditionalFormats.add("containsText", { text: "P2", format: { fill: "#FFF2CC", font: { bold: true, color: "#7F6000" } } });
  priorityRange.conditionalFormats.add("containsText", { text: "skip", format: { fill: "#D9D9D9", font: { color: "#666666" } } });
  return last;
}
function buildActionSheet(workbook, rows) {
  const sheet = workbook.worksheets.getItem("行动清单(P0-P1)"); const selected = rows.filter(({ v3 }) => ["P0", "P1"].includes(v3.priority));
  deleteTables(sheet); sheet.getRange("A4:K40").clear({ applyTo: "contents" }); sheet.getRange("A1").values = [["行动清单（V3 P0-P1）"]];
  const counts = countBy(selected.map(({ v3 }) => v3), "priority"); sheet.getRange("A2").values = [[`按main-lys V3列出${counts.P0 || 0}条P0、${counts.P1 || 0}条P1；仅列优化行动，Bugfix/功能P1见各自管理页；其余优化POC见精确复核页。`]];
  sheet.getRange("A4:K4").values = [["优先级", "PR", "第一责任人", "优化点", "目标模块", "迁移步骤", "验证场景", "V3结论", "精确复核", "结论差异", "Review摘要"]];
  const values = selected.map(({ pr, v3, report }) => [v3.priority, `#${pr.number}`, report.owner, v3.optimization_point, v3.target_ascend_module, (v3.migration_steps || []).join("\n"), (v3.test_verification || []).join("\n"), `${v3.ascend_value} / ${v3.migration_method}`, report.five_category, isDifference(v3, report) ? "有差异" : "无差异", v3.review_notes]);
  const last = DATA_START + values.length - 1;
  if (values.length) {
    sheet.getRange(`A${DATA_START}:K${last}`).values = values; styleDataRange(sheet, `A${DATA_START}:K${last}`, 118); sheet.getRange(`A${DATA_START}:E${last}`).format.horizontalAlignment = "center"; sheet.getRange(`H${DATA_START}:J${last}`).format.horizontalAlignment = "center";
    setHeader(sheet, "A4:K4"); addTable(sheet, `A4:K${last}`, ...TABLE_SPECS["行动清单(P0-P1)"]);
  } else {
    sheet.getRange("A5:K5").values = [["无记录", "", "", "", "", "", "", "", "", "", ""]];
    styleDataRange(sheet, "A5:K5", 42); setHeader(sheet, "A4:K4"); addTable(sheet, "A4:K5", ...TABLE_SPECS["行动清单(P0-P1)"]);
  }
  return Math.max(last, 5);
}
function buildSkipSheet(workbook, rows) {
  const sheet = workbook.worksheets.getItem("跳过清单"); const selected = rows.filter(({ v3 }) => v3.migration_method === "skip");
  deleteTables(sheet); sheet.getRange("A4:H30").clear({ applyTo: "contents" }); sheet.getRange("A1").values = [["跳过清单"]]; sheet.getRange("A2").values = [[`本页仅列不可用/skip。平台实现不可直接用但有借鉴入口的${rows.filter(r=>r.report.five_category==='平台差异可借鉴').length}条已保留在优化总表，不列为skip。`]];
  sheet.getRange("A4:H4").values = [["PR", "合入日期", "第一责任人", "优化点", "分类", "跳过原因", "可借鉴点", "Review摘要"]];
  const values = selected.map(({ pr, v3, report, opt }) => [`#${pr.number}`, excelDate(v3.date), report.owner, v3.optimization_point, v3.category, v3.ascend_value_reason, opt.borrow || "保留上游方法论，按NPU有效路径独立验证。", v3.review_notes]);
  const last = DATA_START + values.length - 1;
  if (values.length) {
    sheet.getRange(`A${DATA_START}:H${last}`).values = values; sheet.getRange(`B${DATA_START}:B${last}`).format.numberFormat = "yyyy-mm-dd"; styleDataRange(sheet, `A${DATA_START}:H${last}`, 102); sheet.getRange(`A${DATA_START}:C${last}`).format.horizontalAlignment = "center"; sheet.getRange(`E${DATA_START}:E${last}`).format.horizontalAlignment = "center";
    setHeader(sheet, "A4:H4"); addTable(sheet, `A4:H${last}`, ...TABLE_SPECS["跳过清单"]); sheet.getRange(`A${DATA_START}:H${last}`).format.fill = "#E7E6E6";
  } else {
    sheet.getRange("A5:H5").values = [["无记录", "", "", "", "", "", "", ""]];
    styleDataRange(sheet, "A5:H5", 42); setHeader(sheet, "A4:H4"); addTable(sheet, "A4:H5", ...TABLE_SPECS["跳过清单"]);
  }
  return Math.max(last, 5);
}
function buildExactReviewSheet(workbook, rows, main2main) {
  const sheet = workbook.worksheets.getItem("精确代码复核"); deleteTables(sheet); sheet.getRange("A4:I40").clear({ applyTo: "contents" });
  sheet.getRange("A1").values = [["V3 工作流结论 vs 精确代码复核"]]; sheet.getRange("A2").values = [["V3 Prompt结合架构映射；精确复核检查import、继承、override、patch、调用链和cutoff端点有效路径。"]];
  sheet.getRange("A4:I4").values = [["PR", "V3结论", "架构候选/真实Patch键", "目标模块", "精确代码结论", "精确原因", "新增接口断点", "V3判定依据摘要", "最终执行口径"]];
  const introduced = new Set((main2main.report.findings || []).filter((f) => f.classification === "introduced_break").flatMap((f) => [f.upstream?.new?.file, f.upstream?.old?.file]).filter(Boolean));
  const directFindings = new Map((main2main.report.direct_findings || []).map((f) => [Number(f.pr_number), f.detail]));
  const values = rows.map(({ pr, v3, report }) => [`#${pr.number}`, `${v3.priority} / ${v3.migration_method}`, v3.patch_matched_keys?.length ? v3.patch_matched_keys.join("\n") : v3.architecture_candidate_keys.length ? `旧映射候选（不等于冲突）：\n${v3.architecture_candidate_keys.join('\n')}` : "无旧映射命中；仍按源码复核", v3.target_ascend_module, `${report.five_category}\n${report.secondary_category}`, report.ascend_relation, directFindings.has(pr.number) ? `已知适配点：${directFindings.get(pr.number)}` : pr.files.some((f) => introduced.has(f.path)) ? "是：main2main introduced_break命中" : "未发现需完整扫描的未知断点", v3._thought_process, exactExecution(report)]);
  rows.forEach(({v3}, index) => { if(v3.verified_patch_bindings.length) values[index][2]='当前源码真实覆盖：\n'+v3.verified_patch_bindings.map(binding=>typeof binding==='string'?binding:`${binding.upstream}\n→ ${binding.ascend}\n${binding.kind}`).join('\n'); });
  if (!values.length) values.push(...nonEmptyValues(values,9)); const last = DATA_START + values.length - 1; sheet.getRange(`A${DATA_START}:I${last}`).values = values; styleDataRange(sheet, `A${DATA_START}:I${last}`, 114); sheet.getRange(`A${DATA_START}:E${last}`).format.horizontalAlignment = "center"; sheet.getRange(`G${DATA_START}:G${last}`).format.horizontalAlignment = "center";
  setHeader(sheet, "A4:I4"); addTable(sheet, `A4:I${last}`, ...TABLE_SPECS["精确代码复核"]); applyAlternatingFill(sheet, DATA_START, last, "I"); return last;
}
function buildTestBenefitSheet(workbook, rows) {
  const sheet = workbook.worksheets.getItem("测试与收益"); deleteTables(sheet); sheet.getRange("A4:F40").clear({ applyTo: "contents" });
  sheet.getRange("A1").values = [["上游测试条件、可核验收益与 NPU 验证"]]; sheet.getRange("A2").values = [["收益只填PR可核验数据；未提供A/B的记录明确注明，不把GPU结果外推到NPU。"]];
  sheet.getRange("A4:F4").values = [["PR", "第一责任人", "上游测试条件", "PR可核验结果", "NPU验证方案", "关注KPI"]];
  const values = rows.map(({ pr, report }) => [`#${pr.number}`, report.owner, report.upstream_test_conditions, report.upstream_result, report.verification_plan, report.expected_npu_kpi]);
  if (!values.length) values.push(...nonEmptyValues(values,6)); const last = DATA_START + values.length - 1; sheet.getRange(`A${DATA_START}:F${last}`).values = values; styleDataRange(sheet, `A${DATA_START}:F${last}`, 106); sheet.getRange(`A${DATA_START}:B${last}`).format.horizontalAlignment = "center"; setHeader(sheet, "A4:F4"); addTable(sheet, `A4:F${last}`, ...TABLE_SPECS["测试与收益"]); applyAlternatingFill(sheet, DATA_START, last, "F", "#E2F0D9", "#FFFFFF"); return last;
}
function buildReviewEvidenceSheet(workbook, rows) {
  const sheet = workbook.worksheets.getItem("Review证据"); deleteTables(sheet); sheet.getRange("A4:G40").clear({ applyTo: "contents" });
  sheet.getRange("A1").values = [["PR Review 原始证据"]]; sheet.getRange("A2").values = [["保留Review、行级评论、Issue评论计数和PR原始URL；机器人状态评论不单独决定迁移结论。"]];
  sheet.getRange("A4:G4").values = [["PR", "标题", "Review正文数", "行级评论数", "Issue评论数", "影响结论的Review摘要", "原始证据URL"]];
  const values = rows.map(({ pr, v3, review }) => { const c = reviewCounts(review); return [`#${pr.number}`, pr.title, c.reviews, c.inline, c.comments, v3.review_notes, pr.url]; });
  if (!values.length) values.push(...nonEmptyValues(values,7)); const last = DATA_START + values.length - 1; sheet.getRange(`A${DATA_START}:G${last}`).values = values; styleDataRange(sheet, `A${DATA_START}:G${last}`, 108); sheet.getRange(`A${DATA_START}:E${last}`).format.horizontalAlignment = "center"; setHeader(sheet, "A4:G4"); addTable(sheet, `A4:G${last}`, ...TABLE_SPECS["Review证据"]); applyAlternatingFill(sheet, DATA_START, last, "G"); return last;
}
function buildPipelineSheet(workbook, rows, main2main) {
  const sheet = workbook.worksheets.getItem("流水线与版本"); deleteTables(sheet); sheet.getRange("A4:C40").clear({ applyTo: "contents" });
  sheet.getRange("A1").values = [["流水线、时间窗与版本锚点"]]; sheet.getRange("A2").values = [["GraphQL mergedAt保证窗口完整性；main-lys V3负责价值/迁移路由；pinned old/new/Ascend源码负责最终关系复核，只有常规追踪无法闭环时才触发main2main完整接口扫描。"]];
  const v3Counts = countBy(rows.map(({ v3 }) => v3), "priority"); const finalCounts = countBy(rows.map(({ report }) => report), "five_category"); const s = main2main.report.summary || {};
  const data = [
    ["流水线仓库", "SOMEONEUNSEEN/vllm-report", "https://github.com/SOMEONEUNSEEN/vllm-report/tree/main-lys/templates"],
    ["main-lys HEAD", PERIOD.mainLys, `https://github.com/SOMEONEUNSEEN/vllm-report/commit/${PERIOD.mainLys}`],
    ["流程输入", "templates/SKILL.md + optimization_prompt.txt", "最新四阶段/12步V3流程；PR description、Review和architecture作为输入"],
    ["原仓库程序执行", `scripts/deep_analyze.py；输入${PERIOD.totalPrs}条`, PROGRAM_ANALYSIS.empty_input_adapter ? "已实际调用原程序，空输入返回码1；空结果由适配器显式生成，未声称原程序成功处理记录。日志和执行凭证已保留。" : `实际运行原程序；关键词输出 performance=${PROGRAM_ANALYSIS.commits.filter(x=>x.tags.includes('performance')).length}、bugfix=${PROGRAM_ANALYSIS.commits.filter(x=>x.tags.includes('bugfix')).length}、feature=${PROGRAM_ANALYSIS.commits.filter(x=>x.tags.includes('feature')).length}。其输出只作工作流归类参照；AI按最终diff/Review/源码识别${rows.length}条优化。原始结果保留在pipeline-data。`],
    ["程序边界", "精确窗口采集替代daily_refresh自然日采集", "daily_refresh按自然日重抓且会破坏滚动水位，因此未运行；未调用外部LLM_API_KEY服务。AI完成语义阶段并按仓库SKILL/Prompt/Schema路由，不声称未执行的外部模型程序已运行。"],
    ["Skill/Prompt/Schema blob", `${PERIOD.skillBlob} / ${PERIOD.promptBlob} / ${PERIOD.schemaBlob}`, "GitHub pinned Contents与本地原文件逐字节一致"],
    ["Schema", "UpstreamOptimizationRecordV3", "官方optimization_schema.json；校验结果见本次audit"],
    ["窗口开始", `${PERIOD.start}（${PERIOD.timezone}）`, "左闭"], ["窗口cutoff", `${PERIOD.end}（${PERIOD.timezone}）`, "右开"],
    ["vLLM old SHA", PERIOD.vllmOld, "起点前最后main提交"], ["vLLM cutoff SHA", PERIOD.vllmNew, `https://github.com/vllm-project/vllm/commit/${PERIOD.vllmNew}`],
    ["vllm-ascend SHA", PERIOD.ascend, `https://github.com/vllm-project/vllm-ascend/commit/${PERIOD.ascend}`], ["verified vLLM SHA", PERIOD.verified, ".github/vllm-main-verified.commit"],
    ["区间合入PR", PERIOD.totalPrs, `GraphQL mergedAt左闭右开精确筛选；原始分页和${PERIOD.totalPrs}个diff已留存`], ["优化分析记录", rows.length, "性能、功能性收益、构建效率；允许与Bugfix交叉"],
    ["Bugfix记录", BUGFIX_IDS.length, "运行时、安全、模型/协议及CI/测试缺陷；无版本分支匹配和证据等级列"], ["功能/接口记录", FEATURE_IDS.length, "新增能力、接口或校验职责变化"], ["全部PR分析", PERIOD.totalPrs, "全部PR页逐条说明问题、方案、原因和是否Bugfix"],
    ["V3结论", `P0=${v3Counts.P0 || 0}；P1=${v3Counts.P1 || 0}；P2=${v3Counts.P2 || 0}；P3=${v3Counts.P3 || 0}；skip=${v3Counts.skip || 0}`, "按main-lys矩阵"],
    ["五类最终结论", `直接继承=${finalCounts["直接继承"] || 0}；收益明确待适配=${finalCounts["收益明确待适配"] || 0}；收益不明确待验证=${finalCounts["收益不明确待验证"] || 0}；平台差异可借鉴=${finalCounts["平台差异可借鉴"] || 0}；不可用=${finalCounts["不可用"] || 0}`, "AI语义分析+main-lys路由+import/继承/override/patch/调用链复核"],
    ["main2main 调用门控", main2main.report.metadata?.full_analyzer_executed ? "已运行接口扫描，详见报告" : "本次采用直接源码复核", main2main.report.metadata?.gate || "未提供门控说明，禁止验收"],
    ["精确源码复核", "pinned old/new/Ascend：import、继承、override、patch、调用链", (main2main.report.direct_findings || []).map(f=>`#${f.pr_number}：${f.detail}`).join("\n") || "详见本次逐项源码复核；未声明NPU实测收益。"],
    ["旧架构映射的边界", "固定版本architecture.json，生成时间以原文件为准", "patch_impact_map只作为候选提示；最终冲突与可达性由cutoff源码复核，不沿用旧never_affected_paths把NPU native offload判为不可达。"],
    ["用户口径覆盖原提示词", "平台路径不自动skip；真实CI缺陷仍入Bugfix台账", "按本任务已确认的五类口径，平台实现不可直用但有具体NPU借鉴入口的记录保留idea-copy；不沿用原Prompt的纯平台/CI统一跳过规则。待POC不等于已取得收益。"],
    ["结论差异口径", "V3路由与五类结论的一致性检查", "这两列是同一条AI分析的不同管理编码，不是两个独立模型投票。关键词原程序输出与最终语义分类单独保留。"],
    ["版本关系解释", "cutoff主干为分析目标；verified为Ascend已验证依赖锚点", "理论继承指升到本次目标vLLM后的源码关系，不表示verified已包含所有修复，也不表示NPU收益已实测。"],
    ["Excel格式基准", "交接包固定 V3 11 页模板", "版式与模板SHA256见交接包manifest；成功样例为20260922-20260923。未修改历史报告。"],
  ];
  sheet.getRange("A4:C4").values = [["项目", "值", "来源/说明"]]; sheet.getRange(`A5:C${4 + data.length}`).values = data; styleDataRange(sheet, `A5:C${4 + data.length}`, 46); setHeader(sheet, "A4:C4"); addTable(sheet, `A4:C${4 + data.length}`, ...TABLE_SPECS["流水线与版本"]); return 4 + data.length;
}
function buildDashboard(workbook, rows, mainLast) {
  const sheet = workbook.worksheets.getItem("统计看板"); sheet.getRange("A4:F22").clear({ applyTo: "contents" }); sheet.getRange("A1").values = [["统计看板"]]; sheet.getRange("A2").values = [[`数量由“优化点总表”公式计算；快速核对${rows.length}条优化、V3迁移和五类最终结论。`]];
  sheet.getRange('A17:F19').unmerge();
  sheet.mergeCells('A19:F19');
  sheet.getRange("A4").values = [["统计项"]]; sheet.getRange("B4").values = [["数量"]]; sheet.getRange("D4").values = [["结论说明"]];
  sheet.getRange("A5:A18").values = ["总记录", "V3 P0", "V3 P1", "V3 P2", "V3 P3", "V3 skip", "V3 patch-sync", "最终：直接继承", "最终：收益明确待适配", "最终：收益不明确待验证", "最终：平台差异可借鉴", "最终：不可用", "结论有差异", "结论无差异"].map((x) => [x]);
  const formulas = [
    `=COUNTA('优化点总表'!$B$${DATA_START}:$B$${mainLast})`, `=COUNTIF('优化点总表'!$A$${DATA_START}:$A$${mainLast},"P0")`, `=COUNTIF('优化点总表'!$A$${DATA_START}:$A$${mainLast},"P1")`, `=COUNTIF('优化点总表'!$A$${DATA_START}:$A$${mainLast},"P2")`, `=COUNTIF('优化点总表'!$A$${DATA_START}:$A$${mainLast},"P3")`, `=COUNTIF('优化点总表'!$A$${DATA_START}:$A$${mainLast},"skip")`, `=COUNTIF('优化点总表'!$G$${DATA_START}:$G$${mainLast},"patch-sync")`, `=COUNTIF('优化点总表'!$K$${DATA_START}:$K$${mainLast},"直接继承")`, `=COUNTIF('优化点总表'!$K$${DATA_START}:$K$${mainLast},"收益明确待适配")`, `=COUNTIF('优化点总表'!$K$${DATA_START}:$K$${mainLast},"收益不明确待验证")`, `=COUNTIF('优化点总表'!$K$${DATA_START}:$K$${mainLast},"平台差异可借鉴")`, `=COUNTIF('优化点总表'!$K$${DATA_START}:$K$${mainLast},"不可用")`, `=COUNTIF('优化点总表'!$L$${DATA_START}:$L$${mainLast},"有差异")`, `=COUNTIF('优化点总表'!$L$${DATA_START}:$L$${mainLast},"无差异")`,
  ];
  sheet.getRange("B5:B18").formulas = formulas.map((x) => [x]);
  const adapt = rows.filter(({ report }) => report.five_category === "收益明确待适配").map(({ pr }) => `#${pr.number}`).join("/"); const poc = rows.filter(({ report }) => report.five_category === "收益不明确待验证").map(({ pr }) => `#${pr.number}`).join("/"); const borrow = rows.filter(({ report }) => report.five_category === "平台差异可借鉴").map(({ pr }) => `#${pr.number}`).join("/");
  sheet.getRange("D5").values = [[`五类复核：${adapt || "无"}收益明确待适配；${poc || "无"}收益不明确待验证；${borrow || "无"}平台差异可借鉴。\n\n重点项以行动清单和精确代码复核页为准；所有结论均按本窗口cutoff源码、PR diff与Review证据生成。`]];
  sheet.getRange("A19").formulas = [["=\"QC：总记录=\"&B5&\"；V3 P0+P1+P2+P3+skip=\"&(B6+B7+B8+B9+B10)&\"；五类结论合计=\"&(B12+B13+B14+B15+B16)&\"；结论差异+无差异=\"&(B17+B18)&\"。\""]];
  sheet.getRange("A5:B18").format.borders = { preset: "all", style: "thin", color: "#BFBFBF" }; sheet.getRange("A5:A18").format.fill = "#DCE6F1"; sheet.getRange("B5:B18").format.font = { bold: true, color: "#4472C4", size: 11 }; sheet.getRange("B5:B18").format.horizontalAlignment = "center"; sheet.getRange("A5:B18").format.rowHeight = 28; sheet.getRange("D5:F14").format.wrapText = true; sheet.getRange("D5:F14").format.verticalAlignment = "top"; sheet.getRange("A19:F19").format.wrapText = true;
  sheet.getRange('A5:A18').format.font={name:'Carlito',size:10,color:'#333333',bold:false};
  sheet.getRange('A19:F19').format.rowHeight=36;
}
function buildBugfixSheet(workbook, bugRows) {
  const sheet = workbook.worksheets.getItem("上游Bugfix"); deleteTables(sheet); sheet.getRange("A1:U60").clear({ applyTo: "contents" });
  sheet.getRange("A1").values = [["vLLM 上游 Bugfix 管理表"]];
  sheet.getRange("A2:U2").values = [["统计周期起", PERIOD.start, "统计周期止（不含）", PERIOD.end, "时区", PERIOD.timezone, "vLLM old SHA", PERIOD.vllmOld, "vLLM new SHA", PERIOD.vllmNew, "Ascend基线", PERIOD.ascend, "版本lane", `main；anchor ${PERIOD.verified}`, null, null, null, null, null, null, null]];
  const counts = countBy(bugRows, "conclusion"); sheet.getRange("A3").values = [[`统计口径：${PERIOD.totalPrs}个合入PR；本表纳入${bugRows.length}条正确性、安全、兼容性及实际CI/测试缺陷。结论按来源可达、修复覆盖、Ascend等价处理矩阵生成。`]];
  sheet.getRange("A5:L5").values = [["记录总数", null, "需要适配", null, "待确认", null, "直接继承修复", null, "不受影响", null, "判定闭环率", null]];
  sheet.getRange("A7").values = [["判定矩阵：来源不可达或已有等价处理→不受影响；来源可达且共享修复进入→直接继承修复；来源可达、修复未进入且无等价处理→需要适配；任一关键项未知→待确认。继承指目标vLLM版本，不等于当前verified已升级。"]];
  const headers = ["记录ID", "上游PR链接", "PR标题", "合入时间", "Merge SHA", "Bugfix简介", "Bug分类", "上游修复点（文件/问题/修复语义）", "影响场景", "Ascend有效实现/关系", "Bug来源在Ascend可达", "上游修复进入Ascend", "Ascend等价处理", "Ascend结论", "因果判断依据", "建议动作", "优先级", "验证建议", "状态", "第一责任人", "备注"];
  sheet.getRange("A8:U8").values = [headers]; const values = bugRows.map((r) => [r.record_id, r.url, r.title, excelLocalTime(r.merged_local), r.merge_sha, r.summary, r.category, r.fix, r.scenario, r.relation, r.reachable, r.enters, r.equivalent, r.conclusion, r.causal, r.action, r.priority, r.verification, r.status, r.owner, r.notes]);
  if (!values.length) values.push(...nonEmptyValues(values,21)); const last = 8 + values.length; sheet.getRange(`A9:U${last}`).values = values;
  sheet.getRange(`D9:D${last}`).format.numberFormat="yyyy-mm-dd hh:mm:ss";
  sheet.getRange('B5').formulas = [[`=COUNTA(A9:A${last})`]];
  for (const [cell, label] of [['D5','需要适配'],['F5','待确认'],['H5','直接继承修复'],['J5','不受影响']]) sheet.getRange(cell).formulas = [[`=COUNTIF(N9:N${last},"${label}")`]];
  sheet.getRange('L5').formulas = [['=IF(B5=0,1,1-F5/B5)']];
  // These title/filter ranges are already merged in the imported V3 template.
  sheet.showGridLines = false;
  sheet.getRange("A1:U1").format = { fill: "#17365D", font: { name: "Carlito", size: 16, bold: true, color: "#FFFFFF" }, horizontalAlignment: "left", verticalAlignment: "center" };
  sheet.getRange("A2:U2").format = { fill: "#D9EAF7", font: { name: "Carlito", size: 9, color: "#17365D" }, verticalAlignment: "center", wrapText: true, borders: { preset: "all", style: "thin", color: "#B4C6E7" } };
  sheet.getRange("A3:U3").format = { fill: "#F2F2F2", font: { name: "Carlito", size: 9, color: "#44546A" }, verticalAlignment: "center", wrapText: true };
  for (const c of ["A5", "C5", "E5", "G5", "I5", "K5"]) sheet.getRange(c).format = { fill: "#D9EAF7", font: { name: "Carlito", size: 10, bold: true, color: "#17365D" }, horizontalAlignment: "center", borders: { preset: "all", style: "thin", color: "#B4C6E7" } };
  for (const c of ["B5", "D5", "F5", "H5", "J5", "L5"]) sheet.getRange(c).format = { fill: "#FFFFFF", font: { name: "Carlito", size: 10, bold: true, color: "#17365D" }, horizontalAlignment: "center", borders: { preset: "all", style: "thin", color: "#B4C6E7" } };
  sheet.getRange("A7:U7").format = { fill: "#FFF2CC", font: { name: "Carlito", size: 9, bold: true, color: "#7F6000" }, wrapText: true, borders: { preset: "all", style: "thin", color: "#D6B656" } };
  sheet.getRange("A8:U8").format = { fill: "#1F4E78", font: { name: "Carlito", size: 9, bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true, borders: { preset: "all", style: "thin", color: "#9EADBA" } };
  applyAlternatingFill(sheet, 9, last, "U", "#DDEBF7", "#FFFFFF"); styleDataRange(sheet, `A9:U${last}`, 98); sheet.getRange(`K9:N${last}`).format.horizontalAlignment = "center"; sheet.getRange(`P9:Q${last}`).format.horizontalAlignment = "center"; sheet.getRange(`S9:T${last}`).format.horizontalAlignment = "center";
  const widths = { A: 14, B: 25, C: 34, D: 19, E: 18, F: 30, G: 18, H: 38, I: 26, J: 38, K: 15, L: 15, M: 15, N: 16, O: 40, P: 12, Q: 10, R: 38, S: 15, T: 12, U: 25 };
  for (const [col, width] of Object.entries(widths)) sheet.getRange(`${col}1:${col}${last}`).format.columnWidth = width;
  sheet.getRange("A1:U1").format.rowHeight = 34; sheet.getRange("A2:U2").format.rowHeight = 50; sheet.getRange("A3:U3").format.rowHeight = 42; sheet.getRange("A5:L5").format.rowHeight = 30; sheet.getRange("A7:U7").format.rowHeight = 48; sheet.getRange("A8:U8").format.rowHeight = 50; sheet.getRange("L5").format.numberFormat = "0%";
  const cr = sheet.getRange(`N9:N${last}`); cr.conditionalFormats.deleteAll(); cr.conditionalFormats.add("containsText", { text: "需要适配", format: { font: { bold: true, color: "#C65911" } } }); cr.conditionalFormats.add("containsText", { text: "直接继承修复", format: { font: { bold: true, color: "#548235" } } }); cr.conditionalFormats.add("containsText", { text: "不受影响", format: { font: { bold: true, color: "#666666" } } });
  return last;
}
function buildFeatureSheet(workbook, featureRows) {
  let sheet; try { sheet = workbook.worksheets.getItem("上游功能接口"); } catch { sheet = workbook.worksheets.add("上游功能接口"); }
  deleteTables(sheet); sheet.getRange("A1:P50").clear({ applyTo: "contents" }); sheet.showGridLines = false;
  sheet.getRange("A1").values = [["vLLM 上游功能 / 接口适配管理表"]];
  sheet.getRange("A2").values = [[`时间窗：[${PERIOD.start}, ${PERIOD.end}) ${PERIOD.timezone}；纳入${featureRows.length}条新增能力、接口或职责边界变化。性能收益另见优化页。`]];
  sheet.mergeCells("A1:P1"); sheet.mergeCells("A2:P2");
  sheet.getRange("A1:P1").format = { fill: "#17365D", font: { name: "Carlito", size: 16, bold: true, color: "#FFFFFF" }, verticalAlignment: "center" };
  sheet.getRange("A2:P2").format = { fill: "#D9EAF7", font: { name: "Carlito", size: 9, color: "#17365D" }, wrapText: true, verticalAlignment: "center" };
  const headers = ["记录ID", "PR", "合入时间", "标题", "分类", "解决的问题", "上游方案", "修改原因", "关键文件", "Ascend有效关系", "Ascend结论", "建议动作", "优先级", "状态", "第一责任人", "PR证据"];
  sheet.getRange("A4:P4").values = [headers]; setHeader(sheet, "A4:P4");
  const values = featureRows.map((r) => [r.record_id, `#${r.url.split("/").pop()}`, excelLocalTime(r.merged_local), r.title, r.category, r.problem, r.solution, r.why, r.files, r.relation, r.conclusion, r.action, r.priority, r.status, r.owner, r.url]);
  if (!values.length) values.push(...nonEmptyValues(values,16)); const last = 4 + values.length;
  sheet.getRange(`A5:P${last}`).values = values; sheet.getRange(`C5:C${last}`).format.numberFormat="yyyy-mm-dd hh:mm:ss"; styleDataRange(sheet, `A5:P${last}`, 102); applyAlternatingFill(sheet, 5, last, "P", "#DDEBF7", "#FFFFFF");
  sheet.getRange(`A5:E${last}`).format.horizontalAlignment = "center"; sheet.getRange(`K5:O${last}`).format.horizontalAlignment = "center";
  addTable(sheet, `A4:P${last}`, ...TABLE_SPECS["上游功能接口"]);
  const widths = { A: 14, B: 10, C: 20, D: 38, E: 22, F: 38, G: 40, H: 40, I: 42, J: 46, K: 18, L: 38, M: 10, N: 14, O: 12, P: 28 };
  for (const [col, width] of Object.entries(widths)) sheet.getRange(`${col}1:${col}${last}`).format.columnWidth = width;
  sheet.getRange("A1:P1").format.rowHeight = 34; sheet.getRange("A2:P2").format.rowHeight = 42; sheet.getRange("A4:P4").format.rowHeight = 48;
  const cr = sheet.getRange(`K5:K${last}`); cr.conditionalFormats.add("containsText", { text: "需要适配", format: { fill: "#FCE4D6", font: { bold: true, color: "#C65911" } } }); cr.conditionalFormats.add("containsText", { text: "直接继承", format: { fill: "#E2F0D9", font: { bold: true, color: "#548235" } } });
  return last;
}
function buildAllPRSheet(workbook, allRows) {
  let sheet; try { sheet = workbook.worksheets.getItem("全部PR分析"); } catch { sheet = workbook.worksheets.add("全部PR分析"); }
  deleteTables(sheet); sheet.getRange("A1:J80").clear({ applyTo: "contents" }); sheet.showGridLines = false;
  sheet.getRange("A1").values = [["vLLM 区间全部已合入 PR 分析"]]; sheet.getRange("A2").values = [[`时间窗：[${PERIOD.start}, ${PERIOD.end}) ${PERIOD.timezone}；共${allRows.length}条。问题、方案和修改原因基于PR正文、diff与Review证据，按GraphQL mergedAt排序。`]];
  sheet.mergeCells("A1:J1"); sheet.mergeCells("A2:J2"); sheet.getRange("A1:J1").format = { fill: "#17365D", font: { name: "Carlito", size: 16, bold: true, color: "#FFFFFF" }, verticalAlignment: "center" }; sheet.getRange("A2:J2").format = { fill: "#D9EAF7", font: { name: "Carlito", size: 9, color: "#17365D" }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("A4:J4").values = [["PR", "合入时间", "PR标题", "标签/类型", "解决什么问题", "解决方案", "为什么这么修改", "是否为Bugfix", "关键文件", "PR证据"]]; setHeader(sheet, "A4:J4");
  const values = allRows.map((r) => [`#${r.number}`, excelLocalTime(r.merged_local), r.title, r.labels.join("; "), r.problem, r.solution, r.why, r.is_bugfix, r.key_files, r.url]); if (!values.length) values.push(...nonEmptyValues(values,10)); const last = 4 + values.length;
  sheet.getRange(`A5:J${last}`).values = values; sheet.getRange(`B5:B${last}`).format.numberFormat="yyyy-mm-dd hh:mm:ss"; styleDataRange(sheet, `A5:J${last}`, 94); sheet.getRange(`A5:D${last}`).format.horizontalAlignment = "center"; sheet.getRange(`H5:H${last}`).format.horizontalAlignment = "center"; applyAlternatingFill(sheet, 5, last, "J", "#DDEBF7", "#FFFFFF"); addTable(sheet, `A4:J${last}`, ...TABLE_SPECS["全部PR分析"]);
  const widths = { A: 10, B: 20, C: 38, D: 24, E: 42, F: 44, G: 44, H: 18, I: 42, J: 28 }; for (const [col, width] of Object.entries(widths)) sheet.getRange(`${col}1:${col}${last}`).format.columnWidth = width;
  sheet.getRange("A1:J1").format.rowHeight = 34; sheet.getRange("A2:J2").format.rowHeight = 42; sheet.getRange("A4:J4").format.rowHeight = 46;
  const bugRange = sheet.getRange(`H5:H${last}`); bugRange.conditionalFormats.add("containsText", { text: "是", format: { fill: "#FCE4D6", font: { bold: true, color: "#C00000" } } });
  return last;
}

async function main() {
  if(process.argv.includes('--recalc-probe')) {
    const test=await SpreadsheetFile.importXlsx(await FileBlob.load(OUTPUT));
    const main=test.worksheets.getItem('优化点总表'),dashboard=test.worksheets.getItem('统计看板');
    const get=cell=>Number(dashboard.getRange(cell).values[0][0]);
    const before={p1:get('B7'),p2:get('B8'),direct:get('B12'),borrow:get('B15')};
    const originalPriority=main.getRange('A5').values[0][0],originalCategory=main.getRange('K5').values[0][0];
    main.getRange('A5').values=[['P1']];main.getRange('K5').values=[['直接继承']];test.recalculate();
    const changed={p1:get('B7'),p2:get('B8'),direct:get('B12'),borrow:get('B15')};
    if(changed.p1!==before.p1+Number(originalPriority!=='P1')||changed.p2!==before.p2-Number(originalPriority==='P2')||changed.direct!==before.direct+Number(originalCategory!=='直接继承')||changed.borrow!==before.borrow-Number(originalCategory==='平台差异可借鉴'))throw Error('Dashboard dependency recalculation probe failed');
    main.getRange('A5').values=[[originalPriority]];main.getRange('K5').values=[[originalCategory]];test.recalculate();
    const restored={p1:get('B7'),p2:get('B8'),direct:get('B12'),borrow:get('B15')};
    if(JSON.stringify(restored)!==JSON.stringify(before))throw Error('Probe restore failed');
    const result={valid:true,engine:'artifact-tool',native_excel_not_run:true,before,changed,restored,exported:false};
    await fs.writeFile(path.join(RUN_DIR,'recalculation-probe.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result));return;
  }
  const recordsOnly = process.argv.includes('--records-only');
  const layoutPreview = process.argv.includes('--layout-preview');
  const [prs, reviewContext, main2main] = await Promise.all([readJson(path.join(EVIDENCE, "merged_prs.json")), readJson(path.join(EVIDENCE, "pr_review_context.json")), recordsOnly || layoutPreview ? {file:null,report:{summary:{},metadata:{status:'not_finished'}}} : findMain2MainReport()]);
  if (prs.length !== PERIOD.totalPrs) throw new Error(`Expected ${PERIOD.totalPrs} PRs, got ${prs.length}`);
  const prById = new Map(prs.map((p) => [p.number, p]));
  const architecture = await readJson(path.join(EVIDENCE, 'main_lys_architecture.json'));
  const candidateKeys = Object.keys(architecture.cross_project_relationship.patch_impact_map);
  for (const opt of OPTIMIZATIONS) opt.architectureHintKeys = candidateKeys.filter(key => prById.get(opt.pr).files.some(file => { const module = file.path.replace(/\.py$/, '').replaceAll('/', '.'); return module === key || module.startsWith(key+'.'); }));
  for (const p of prs) if (!ALL_PR_ANALYSIS[p.number]) throw new Error(`Missing all-PR analysis for #${p.number}`);
  for (const id of BUGFIX_IDS) if (!prById.has(id)) throw new Error(`Missing bugfix PR #${id}`);
  for (const id of FEATURE_IDS) if (!prById.has(id)) throw new Error(`Missing feature PR #${id}`);
  const rows = OPTIMIZATIONS.map((opt) => { const pr = prById.get(opt.pr); if (!pr) throw new Error(`Missing optimization #${opt.pr}`); const v3 = makeV3(pr, opt); const report = makeReport(pr, opt); const review = reviewContext.records[String(pr.number)] || {}; return { pr, opt, v3, report, review }; });
  const v3Records = rows.map((r) => r.v3); const schemaValidation = validateV3(v3Records); if (!schemaValidation.valid) throw new Error(schemaValidation.errors.join("\n"));
  const bugRows = BUGFIX_IDS.map((id) => bugfixRow(prById.get(id))).sort((a, b) => a.priority.localeCompare(b.priority)||a.merged_at.localeCompare(b.merged_at));
  const featureRows = FEATURE_IDS.map((id) => featureRow(prById.get(id))).sort((a, b) => a.merged_local.localeCompare(b.merged_local));
  const allRows = prs.map((pr) => { const [problem, solution, why, isBugfix] = ALL_PR_ANALYSIS[pr.number]; return { number: pr.number, merged_at: pr.merged_at, merged_local: localTimestamp(pr.merged_at), title: pr.title, labels: pr.labels, problem, solution, why, is_bugfix: isBugfix, key_files: keyFiles(pr, 5), url: pr.url, merge_sha: pr.merge_sha }; });
  const workflowManifest = { generated_at: new Date().toISOString(), period: PERIOD, inputs: { merged_prs: path.join(EVIDENCE, "merged_prs.json"), reviews: path.join(EVIDENCE, "pr_review_context.json"), relationship_review: main2main.file, template: TEMPLATE }, counts: { all_prs: PERIOD.totalPrs, optimization: rows.length, bugfix: bugRows.length, feature_interface: featureRows.length }, main_lys_workflow: { repository: "SOMEONEUNSEEN/vllm-report", branch: "main-lys", head: PERIOD.mainLys, skill_blob: PERIOD.skillBlob, prompt_blob: PERIOD.promptBlob, schema_blob: PERIOD.schemaBlob, stages: ["AI独立语义分析", "main-lys原仓库deep_analyze归类输入", "意图与收益解析", "迁移策略路由", "精准模块映射", "pinned源码关系复核与main2main门控", "零损耗结构化输出"] } };
  const qualityRecords = {
    meta: {
      period_start: PERIOD.startIso, period_end: PERIOD.endIso, timezone: PERIOD.timezone,
      vllm_old_sha: PERIOD.vllmOld, vllm_new_sha: PERIOD.vllmNew, ascend_baseline: PERIOD.ascend,
      version_lane: "main", version_anchor: PERIOD.verified,
    },
    bugfix_rows: bugRows,
    feature_interface_rows: featureRows,
    optimization_rows: rows.map((r) => r.report),
  };
  const aiRaw = rows.map(({ pr, opt }) => ({
    pr_number: pr.number, title: pr.title, problem: ALL_PR_ANALYSIS[pr.number][0], solution: ALL_PR_ANALYSIS[pr.number][1],
    why: ALL_PR_ANALYSIS[pr.number][2], optimization_mechanism: opt.mechanism, upstream_test_conditions: opt.conditions,
    verifiable_result: opt.result, initial_ascend_hypothesis: opt.valueReason,
  }));
  const main2mainReview = rows.map(({ pr, v3, report }) => ({
    pr_number: pr.number, migration_method: v3.migration_method, target_ascend_module: v3.target_ascend_module,
    patch_matched_keys: v3.patch_matched_keys, ascend_relation: report.ascend_relation,
    five_category: report.five_category, secondary_category: report.secondary_category,
    four_gate_action: report.action_gate || null, main2main_report: main2main.file,
  }));
  const threeStageValidation = {
    valid: schemaValidation.valid, stage_order: ["AI独立语义分析", "main-lys V3工作流归类", "pinned源码关系复核与main2main门控"],
    counts: { ai_raw: aiRaw.length, main_lys_v3: v3Records.length, main2main_review: main2mainReview.length },
    five_category: countBy(rows.map((r) => r.report), "five_category"),
    main2main_scenario: main2main.report.metadata?.scenario, main2main_profile: main2main.report.metadata?.profile,
  };
  await Promise.all([
    fs.writeFile(path.join(RUN_DIR, "ai_raw_analysis.json"), JSON.stringify(aiRaw, null, 2), "utf8"),
    fs.writeFile(path.join(RUN_DIR, "main_lys_classification.json"), JSON.stringify(v3Records, null, 2), "utf8"),
    fs.writeFile(path.join(RUN_DIR, "main2main_code_review.json"), JSON.stringify(main2mainReview, null, 2), "utf8"),
    fs.writeFile(path.join(RUN_DIR, "three-stage-validation.json"), JSON.stringify(threeStageValidation, null, 2), "utf8"),
    fs.writeFile(path.join(RUN_DIR, "optimization_schema_v3.json"), JSON.stringify(v3Records, null, 2), "utf8"),
    fs.writeFile(path.join(RUN_DIR, "optimization_rows.json"), JSON.stringify(rows.map((r) => r.report), null, 2), "utf8"),
    fs.writeFile(path.join(RUN_DIR, "bugfix_rows.json"), JSON.stringify(bugRows, null, 2), "utf8"),
    fs.writeFile(path.join(RUN_DIR, "feature_interface_rows.json"), JSON.stringify(featureRows, null, 2), "utf8"),
    fs.writeFile(path.join(RUN_DIR, "all_pr_rows.json"), JSON.stringify(allRows, null, 2), "utf8"),
    fs.writeFile(path.join(RUN_DIR, "schema-validation.json"), JSON.stringify(schemaValidation, null, 2), "utf8"),
    fs.writeFile(path.join(RUN_DIR, "workflow_manifest.json"), JSON.stringify(workflowManifest, null, 2), "utf8"),
    fs.writeFile(path.join(RUN_DIR, "upstream-quality-records.json"), JSON.stringify(qualityRecords, null, 2), "utf8"),
  ]);

  if (recordsOnly) { console.log(JSON.stringify({mode:'records-only',schemaValidation,counts:workflowManifest.counts})); return; }

  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(TEMPLATE));
  if (process.argv.includes('--template-preview')) {
    console.log((await workbook.inspect({kind:'workbook,sheet,table',maxChars:5000,tableMaxRows:2,tableMaxCols:5})).ndjson);
    const preview=await workbook.render({sheetName:'优化点总表',range:'A1:P7',scale:1,format:'png'});
    await fs.writeFile(path.join(RUN_DIR,'template-preview.png'),new Uint8Array(await preview.arrayBuffer()));
    return;
  }
  // Templates retain example content. Clear ALL prior contents (not just 40/60 rows),
  // preserving formatting, merges and sheet order before writing this run.
  for (const name of SHEETS) {
    const sheet=workbook.worksheets.getItem(name);
    deleteTables(sheet);
    sheet.getUsedRange().clear({applyTo:'contents'});
  }
  const mainLast = buildMainSheet(workbook, rows); const sheetLastRows = { "优化点总表": mainLast, "行动清单(P0-P1)": buildActionSheet(workbook, rows), "跳过清单": buildSkipSheet(workbook, rows), "精确代码复核": buildExactReviewSheet(workbook, rows, main2main), "测试与收益": buildTestBenefitSheet(workbook, rows), "Review证据": buildReviewEvidenceSheet(workbook, rows), "流水线与版本": buildPipelineSheet(workbook, rows, main2main), "统计看板": 19, "上游Bugfix": buildBugfixSheet(workbook, bugRows), "上游功能接口": buildFeatureSheet(workbook, featureRows), "全部PR分析": buildAllPRSheet(workbook, allRows) };
  buildDashboard(workbook, rows, mainLast);
  fitWrittenRows(workbook, sheetLastRows);
  workbook.worksheets.getItem('行动清单(P0-P1)').getRange(`F5:G${sheetLastRows['行动清单(P0-P1)']}`).format.horizontalAlignment='left';
  workbook.recalculate();
  if(layoutPreview) workbook.worksheets.getItem('流水线与版本').getRange('A2').values=[['排版检查中：predict精确断点分析尚未完成；此文件未验收，禁止推进成功水位。']];
  const errorPattern="#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!";
  const preErrors = await workbook.inspect({ kind: "match", searchTerm: errorPattern, options: { useRegex: true, maxResults: 400 }, summary: "pre-export formula error scan" });
  const preInspections = {}; for (const name of SHEETS) { const sheet = workbook.worksheets.getItem(name); preInspections[name] = (await workbook.inspect({ kind: "region,table,formula", sheetId: name, range: sheet.getUsedRange().address, maxChars: 6000, tableMaxRows: 10, tableMaxCols: 22, tableMaxCellChars: 120, options: { maxResults: 300 } })).ndjson; }
  const out = await SpreadsheetFile.exportXlsx(workbook); await out.save(OUTPUT);
  const saved = await SpreadsheetFile.importXlsx(await FileBlob.load(OUTPUT)); const savedErrors = await saved.inspect({ kind: "match", searchTerm: errorPattern, options: { useRegex: true, maxResults: 400 }, summary: "saved formula error scan" });
  const renderDir = path.join(RUN_DIR, "renders"); await fs.mkdir(renderDir, { recursive: true }); const renders = {}; const savedInspections = {};
  const renderSelection=process.argv.find(a=>a.startsWith('--render-sheets='))?.slice('--render-sheets='.length).split(',')||SHEETS;
  const lastCols={'优化点总表':'P','行动清单(P0-P1)':'K','跳过清单':'H','精确代码复核':'I','测试与收益':'F','Review证据':'G','流水线与版本':'C','统计看板':'F','上游Bugfix':'U','上游功能接口':'P','全部PR分析':'J'};
  for (const name of SHEETS) { const sheet = saved.worksheets.getItem(name); const file = path.join(renderDir, `${name.replace(/[\\/:*?"<>|]/g, "_")}.png`); if(renderSelection.includes(name)){const preview = await saved.render({ sheetName: name, range:`A1:${lastCols[name]}${sheetLastRows[name]}`, scale: 1, format: "png" }); await fs.writeFile(file, new Uint8Array(await preview.arrayBuffer()));} renders[name] = file; savedInspections[name] = (await saved.inspect({ kind: "region,table,formula", sheetId: name, range: sheet.getUsedRange().address, maxChars: 8000, tableMaxRows: 12, tableMaxCols: 22, tableMaxCellChars: 140, options: { maxResults: 400 } })).ndjson; }
  const result = { valid: true, output: OUTPUT, template: TEMPLATE, sheets: SHEETS, sheet_last_rows: sheetLastRows, counts: { all_prs: PERIOD.totalPrs, optimization: rows.length, bugfix: bugRows.length, feature_interface: featureRows.length, v3: countBy(v3Records, "priority"), optimization_five_category: countBy(rows.map((r) => r.report), "five_category"), bugfix_final: countBy(bugRows, "conclusion"), feature_final: countBy(featureRows, "conclusion") }, formula_error_scan_pre: preErrors.ndjson, formula_error_scan_saved: savedErrors.ndjson, schema_validation: schemaValidation, main2main_report: main2main.file, renders };
  await Promise.all([fs.writeFile(path.join(RUN_DIR, "pre-export-inspection.json"), JSON.stringify(preInspections, null, 2), "utf8"), fs.writeFile(path.join(RUN_DIR, "saved-inspection.json"), JSON.stringify(savedInspections, null, 2), "utf8"), fs.writeFile(path.join(RUN_DIR, "build-verification.json"), JSON.stringify(result, null, 2), "utf8")]);
  console.log(JSON.stringify(result, null, 2));
}

try { await main(); } catch(error) { console.error(error.message); console.error(error.stack?.split('\n').filter(line=>!line.includes('artifact_tool.mjs')).slice(0,8).join('\n')); process.exitCode=1; }
