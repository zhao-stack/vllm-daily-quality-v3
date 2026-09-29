# 通过 GitHub 交接

仓库：`https://github.com/zhao-stack/vllm-daily-quality-v3`（私有）。

## 给同事的操作

1. 仓库所有者在 Settings → Collaborators 中添加同事的 GitHub 账号。收到邀请后先接受。
2. 用自己的 GitHub 账号克隆：

```powershell
gh repo clone zhao-stack/vllm-daily-quality-v3
cd vllm-daily-quality-v3
```

3. 在该目录打开 agent，将 `docs/接收方启动提示词.md` 全文发给它。也可以直接说：

> 请按本仓库 AGENTS.md 接管报告生成工作，先做离线样例复现与环境验收，不启动正式定时任务，不推进成功水位。

4. 按 README.md 准备 Spreadsheets 插件及运行环境、自己的 GitHub 授权。静态分析和 Excel 生成不要求本机有 NPU；POC/NPU 收益验证另需相应硬件。
5. 先运行 `python scripts/handover.py verify`（使用 agent 依赖加载工具提供的 Python），再按 `docs/执行与验收.md` 复现样例。目标机不能访问依赖时先解决环境，不忽略校验。

## 正式切换

离线验收后，双方确认旧任务停用、无运行中的水位写入，再同步**届时最新成功水位**。本仓库种子停在 2026-09-23 01:01:54.707 +08:00，不表示后续永远没有新报告。原任务不会因创建仓库而自动停用。

每天 01:00 Asia/Hong_Kong 的任务需在接收方 agent 中单独创建，使用 `docs/每日执行提示词.md`。不在 GitHub Actions 中配置每日推理或嵌入凭证。

## 维护

- `vendor/main-lys` 保留经过验收的原程序快照；实际运行前检查远端更新并锁定 SHA。
- 模板、生成器、规则修改需通过样例对照与零记录测试，更新完整性清单后再提交。
- 请勿直接提交所有新报告、模型源码 checkout 或环境目录。需要归档新样例时，明确选择文件、清理凭证并记录来源。
- 仓库权限和正式调度是两个不同的交接步骤。创建私有仓库不等于同事已经获得访问权限。
