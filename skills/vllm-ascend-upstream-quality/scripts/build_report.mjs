#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const BUG_FIELDS = [
  "record_id", "upstream_pr", "title", "merged_at", "merge_sha", "summary",
  "category", "upstream_fix_point", "impact_scenario", "ascend_relation",
  "source_reachable", "fix_enters_ascend", "equivalent_handling", "lane_matches",
  "conclusion", "basis", "action", "priority", "validation_advice",
  "evidence_level", "status", "owner", "notes",
];

const OPT_FIELDS = [
  "record_id", "upstream_pr", "title", "merged_at", "merge_sha", "description",
  "mechanism", "change_point", "upstream_metric", "upstream_result",
  "upstream_test_conditions", "evidence_url", "affected_code", "ascend_relation",
  "npu_applicability", "benefit_acquisition", "reason", "evidence_level",
  "expected_npu_kpi", "extra_work", "estimated_effort", "verification_priority",
  "adaptation_priority", "verification_plan", "npu_result", "action", "status",
  "owner", "notes",
];

const BUG_HEADERS = [
  "记录ID", "上游PR链接", "PR标题", "合入时间", "Merge SHA", "Bugfix简介", "Bug分类",
  "上游修复点（文件/问题/修复语义）", "影响场景", "Ascend有效实现/关系",
  "Bug来源在Ascend可达", "上游修复进入Ascend", "Ascend等价处理", "版本分支匹配",
  "Ascend结论", "因果判断依据", "建议动作", "优先级", "验证建议", "证据等级",
  "状态", "负责人", "备注",
];

const OPT_HEADERS = [
  "收益点ID", "上游PR链接", "PR标题", "合入时间", "Merge SHA", "优化描述",
  "优化机制/类型", "优化机制/变更点", "上游评价指标", "上游实测结果（PR原文）",
  "上游测试条件", "收益证据URL", "影响代码路径", "Ascend代码关系", "NPU适用性",
  "NPU获取方式", "判定理由", "证据等级", "预期NPU指标", "额外工作类型",
  "预估工作量", "验证优先级", "适配优先级", "验证方案/验收标准", "NPU实测结果",
  "建议动作", "状态", "负责人", "备注",
];

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) throw new Error(`Unexpected argument: ${token}`);
    const key = token.slice(2);
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`Missing value for --${key}`);
    result[key] = value;
    index += 1;
  }
  for (const key of ["input", "template", "output"]) {
    if (!result[key]) throw new Error(`Required argument --${key} is missing`);
  }
  return result;
}

function asDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date;
}

function objectsToRows(records, fields) {
  return records.map(record => fields.map((field, index) => {
    const value = record[field] ?? "";
    return index === 3 ? asDate(value) : value;
  }));
}

function formulaRange(column, lastRow) {
  return `${column}9:${column}${Math.max(lastRow, 9)}`;
}

function totalLabel(meta) {
  return Number.isFinite(Number(meta.total_merged_prs))
    ? `${Number(meta.total_merged_prs)}个合入PR`
    : "合入PR总数未提供";
}

const args = parseArgs(process.argv.slice(2));
const inputPath = path.resolve(args.input);
const templatePath = path.resolve(args.template);
const outputPath = path.resolve(args.output);
const outputDir = path.dirname(outputPath);
await fs.mkdir(outputDir, { recursive: true });

const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
if (!data || typeof data !== "object") throw new Error("Input JSON must be an object");
if (!Array.isArray(data.bugfix_rows) || !Array.isArray(data.optimization_rows)) {
  throw new Error("Input JSON must contain bugfix_rows and optimization_rows arrays");
}
const meta = data.meta ?? {};
const bugRows = objectsToRows(data.bugfix_rows, BUG_FIELDS);
const optRows = objectsToRows(data.optimization_rows, OPT_FIELDS);
const bugLast = 8 + bugRows.length;
const optLast = 8 + optRows.length;

const template = await FileBlob.load(templatePath);
const workbook = await SpreadsheetFile.importXlsx(template);
const bug = workbook.worksheets.getItem("上游Bugfix");
const opt = workbook.worksheets.getItem("上游优化");

const preBug = await workbook.render({
  sheetName: "上游Bugfix", range: "A1:W12", scale: 1, format: "png",
});
const preOpt = await workbook.render({
  sheetName: "上游优化", range: "A1:AC12", scale: 1, format: "png",
});
await fs.writeFile(
  path.join(outputDir, "pre_edit_bugfix.png"),
  new Uint8Array(await preBug.arrayBuffer()),
);
await fs.writeFile(
  path.join(outputDir, "pre_edit_optimization.png"),
  new Uint8Array(await preOpt.arrayBuffer()),
);

const bugClearLast = Math.max(83, bugLast);
const optClearLast = Math.max(83, optLast);
bug.getRange(`A9:W${bugClearLast}`).clear({ applyTo: "contents" });
opt.getRange(`A9:AC${optClearLast}`).clear({ applyTo: "contents" });

bug.getRange("A8:W8").values = [BUG_HEADERS];
opt.getRange("A8:AC8").values = [OPT_HEADERS];
if (bugRows.length) bug.getRange(`A9:W${bugLast}`).values = bugRows;
if (optRows.length) opt.getRange(`A9:AC${optLast}`).values = optRows;

for (const sheet of [bug, opt]) {
  sheet.getRange("A2:N2").values = [[
    "统计周期起", asDate(meta.period_start),
    "统计周期止（不含）", asDate(meta.period_end),
    "时区", meta.timezone ?? "",
    "vLLM old SHA", meta.vllm_old_sha ?? "",
    "vLLM new SHA", meta.vllm_new_sha ?? "",
    "Ascend基线", meta.ascend_baseline ?? "",
    "版本lane", `${meta.version_lane ?? ""}；anchor ${meta.version_anchor ?? ""}`,
  ]];
  sheet.getRange("A2:N2").format.wrapText = true;
  sheet.getRange("2:2").format.rowHeight = 44;
  sheet.showGridLines = false;
}

bug.getRange("A3").values = [[
  `统计口径：${totalLabel(meta)}；本表纳入${bugRows.length}条运行时、安全、兼容性或构建相关Bugfix。`
  + "结论按“Bug来源可达→上游修复覆盖→Ascend等价处理”矩阵生成；未命中映射不能作为不受影响证据。",
]];
opt.getRange("A3").values = [[
  `统计口径：${totalLabel(meta)}；本表保留${optRows.length}个优化收益点。`
  + "G列描述机制，I列描述KPI，J列仅记录上游量化结果，K列用中文提炼模型、硬件/后端、并行与负载、对照方式；未披露参数明确标注“未说明”。",
]];
bug.getRange("3:3").format.rowHeight = 42;
opt.getRange("3:3").format.rowHeight = 42;

bug.getRange("A5:L5").values = [[
  "记录总数", null, "需要适配", null, "待确认", null,
  "直接继承修复", null, "不受影响", null, "判定闭环率", null,
]];
bug.getRange("B5").formulas = [[`=COUNTA(${formulaRange("A", bugLast)})`]];
bug.getRange("D5").formulas = [[`=COUNTIF(${formulaRange("O", bugLast)},"需要适配")`]];
bug.getRange("F5").formulas = [[`=COUNTIF(${formulaRange("O", bugLast)},"待确认")`]];
bug.getRange("H5").formulas = [[`=COUNTIF(${formulaRange("O", bugLast)},"直接继承修复")`]];
bug.getRange("J5").formulas = [[`=COUNTIF(${formulaRange("O", bugLast)},"不受影响")`]];
bug.getRange("L5").formulas = [["=IF(B5=0,0,(B5-F5)/B5)"]];
bug.getRange("L5").format.numberFormat = "0%";
bug.getRange("A7").values = [[
  "判定矩阵：来源不可达→不受影响；来源可达且修复进入→直接继承修复；"
  + "来源可达、修复未进入且有等价处理→不受影响；来源可达、修复未进入且无等价处理→需要适配；任一项未知→待确认。",
]];

opt.getRange("A5:L5").values = [[
  "收益点总数", null, "理论直接继承", null, "需要适配", null,
  "待POC", null, "已获得NPU收益", null, "E3/E4", null,
]];
opt.getRange("B5").formulas = [[`=COUNTA(${formulaRange("A", optLast)})`]];
opt.getRange("D5").formulas = [[`=COUNTIF(${formulaRange("P", optLast)},"理论直接继承")`]];
opt.getRange("F5").formulas = [[`=COUNTIF(${formulaRange("P", optLast)},"需要适配")`]];
opt.getRange("H5").formulas = [[`=COUNTIF(${formulaRange("P", optLast)},"待POC")`]];
opt.getRange("J5").formulas = [[
  `=COUNTIF(${formulaRange("AA", optLast)},"已获得收益")+COUNTIF(${formulaRange("AA", optLast)},"已获得NPU收益")`,
]];
opt.getRange("L5").formulas = [[
  `=COUNTIF(${formulaRange("R", optLast)},"E3")+COUNTIF(${formulaRange("R", optLast)},"E4")`,
]];
opt.getRange("A7").values = [[
  "口径提醒：“理论直接继承”仅表示静态代码路径进入；只有完成NPU微基准或端到端验证（E3/E4）后，才能写入NPU实测结果或“已获得收益”。",
]];

const bugValidationLast = Math.max(bugLast, 9);
const optValidationLast = Math.max(optLast, 9);
bug.getRange(`K9:L${bugValidationLast}`).dataValidation = {
  rule: { type: "list", values: ["是", "否", "未知"] },
};
bug.getRange(`M9:M${bugValidationLast}`).dataValidation = {
  rule: { type: "list", values: ["是", "否", "未知", "不适用"] },
};
bug.getRange(`O9:O${bugValidationLast}`).dataValidation = {
  rule: { type: "list", values: ["待确认", "不受影响", "直接继承修复", "需要适配"] },
};
opt.getRange(`P9:P${optValidationLast}`).dataValidation = {
  rule: { type: "list", values: ["理论直接继承", "需要适配", "待POC", "当前不适用"] },
};

if (bugRows.length) {
  bug.getRange(`D9:D${bugLast}`).format.numberFormat = "yyyy-mm-dd hh:mm";
  bug.getRange(`A9:W${bugLast}`).format.wrapText = true;
  bug.getRange(`A9:W${bugLast}`).format.autofitRows();
}
if (optRows.length) {
  opt.getRange(`D9:D${optLast}`).format.numberFormat = "yyyy-mm-dd hh:mm";
  opt.getRange(`A9:AC${optLast}`).format.wrapText = true;
  opt.getRange(`A9:AC${optLast}`).format.autofitRows();
}

const bugInspect = await workbook.inspect({
  kind: "table",
  range: `上游Bugfix!A1:W${Math.min(Math.max(bugLast, 9), 14)}`,
  include: "values,formulas",
  tableMaxRows: 14,
  tableMaxCols: 23,
  tableMaxCellChars: 180,
  maxChars: 9000,
});
const optInspect = await workbook.inspect({
  kind: "table",
  range: `上游优化!A1:AC${Math.min(Math.max(optLast, 9), 14)}`,
  include: "values,formulas",
  tableMaxRows: 14,
  tableMaxCols: 29,
  tableMaxCellChars: 180,
  maxChars: 9000,
});
const conditionsInspect = await workbook.inspect({
  kind: "table",
  range: `上游优化!K9:K${Math.max(optLast, 9)}`,
  include: "values",
  tableMaxRows: Math.max(optRows.length, 1),
  tableMaxCols: 1,
  tableMaxCellChars: 220,
  maxChars: 12000,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});

const previewBase = path.basename(outputPath, path.extname(outputPath));
const bugPreview = await workbook.render({
  sheetName: "上游Bugfix",
  range: `A1:W${Math.min(Math.max(bugLast, 9), 15)}`,
  scale: 1,
  format: "png",
});
const optPreview = await workbook.render({
  sheetName: "上游优化",
  range: `A1:AC${Math.min(Math.max(optLast, 9), 15)}`,
  scale: 1,
  format: "png",
});
await fs.writeFile(
  path.join(outputDir, `${previewBase}.bugfix-preview.png`),
  new Uint8Array(await bugPreview.arrayBuffer()),
);
await fs.writeFile(
  path.join(outputDir, `${previewBase}.optimization-preview.png`),
  new Uint8Array(await optPreview.arrayBuffer()),
);

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

const saved = await FileBlob.load(outputPath);
const verifiedWorkbook = await SpreadsheetFile.importXlsx(saved);
const savedErrors = await verifiedWorkbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "saved workbook formula error scan",
});

console.log("BUG_INSPECT", bugInspect.ndjson);
console.log("OPT_INSPECT", optInspect.ndjson);
console.log("CONDITIONS_INSPECT", conditionsInspect.ndjson);
console.log("ERROR_SCAN", errors.ndjson);
console.log("SAVED_ERROR_SCAN", savedErrors.ndjson);
console.log(JSON.stringify({
  output: outputPath,
  bugfix_rows: bugRows.length,
  optimization_rows: optRows.length,
}));
