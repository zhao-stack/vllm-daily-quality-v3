# 每日 vLLM 质量报告交接入口

接到本仓库的每日分析、报告生成或样例复现任务时，先完整读取：

1. `README.md`
2. `skills/vllm-daily-quality-v3/SKILL.md`
3. 按 skill 路由读取 `docs/分析与字段契约.md`、`docs/执行与验收.md` 和配套 skills。

首次接管使用 `docs/接收方启动提示词.md`，先离线复现、再经负责人确认接续正式水位。不把 example/expected、validation 中历史成功结果当成本次已运行证据。固定 V3 11 页，不回退为旧两表。

仓库包含责任人和历史分析材料，保持私有。不得上传 token、Cookie、代理凭证或整个插件缓存。Spreadsheets/运行时与 GitHub 授权由接收方自己准备。新增运行输出放 `outputs/` 或 `runs/`，正式可变状态放 `local-state/`，默认均不提交。

`state/resume-seed.json` 是 2026-09-29 导出的交接种子，不是自动激活的生产水位。正式切换前从原执行者取得最新成功 cutoff 并避免双机并发。

`manifest.json` 校验交接基线的精确字节，`.gitattributes` 禁止自动换行转换。维护受校验文件时必须重新验收并更新 manifest，不得删除校验来掩盖不一致。此仓库没有自动执行每日任务的 GitHub Actions。
