#!/usr/bin/env python3
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

TZ_CN = timezone(timedelta(hours=8))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from source_repo import repo_dir_name

MRV2_PATTERNS = [
    r"model.*runner.*v2",
    r"MRV2",
    r"model_runner_v2",
    r"ModelRunnerV2",
    r"use_v2_model_runner",
    r"V2ModelRunner",
    r"v2.*model.*runner",
]


def is_mrv2_commit(commit):
    import re
    text = commit.get("message", "")
    for f in commit.get("files", []):
        text += f.get("filename", "") + "\n" + f.get("patch", "")
    for pattern in MRV2_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def save_json_atomic(filepath, data):
    dirpath = os.path.dirname(filepath)
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise e


VLLM_AUTO_FALSE_DIRS = (
    "tests/", "docs/", ".github/", "benchmarks/", "csrc/",
    ".buildkite/", ".buildifier/", "rust/",
)
VLLM_AUTO_FALSE_EXTS = (".md", ".rst", ".txt", ".cfg", ".ini", ".rs")
VLLM_AUTO_FALSE_FILES = {
    "format.sh", "Dockerfile", "Makefile", "CMakeLists.txt",
    "pyproject.toml", "setup.py", "setup.cfg",
    "Cargo.toml", "Cargo.lock",
    "mkdocs.yaml", "mkdocs.yml",
    "README.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "LICENSE",
    ".gitignore", ".gitattributes",
    ".pre-commit-config.yaml", ".codespellrc", ".flake8",
}
VLLM_AUTO_FALSE_PLATFORM_SPECIFIC = (
    "vllm/platforms/cuda.py",
    "vllm/platforms/rocm.py",
    "vllm/platforms/xpu.py",
    "vllm/platforms/tpu.py",
    "vllm/platforms/cpu.py",
    "vllm/platforms/zen_cpu.py",
    "vllm/v1/worker/gpu_worker.py",
    "vllm/v1/worker/cpu_worker.py",
    "vllm/v1/worker/xpu_worker.py",
    "vllm/v1/attention/backends/flash_attn.py",
    "vllm/v1/attention/backends/flashinfer.py",
    "vllm/v1/attention/backends/rocm_attn.py",
    "vllm/v1/attention/backends/rocm_aiter.py",
    "vllm/v1/attention/backends/cpu_attn.py",
    "vllm/v1/attention/backends/triton_attn.py",
    "vllm/v1/attention/backends/flex_attention.py",
    "vllm/v1/attention/backends/turboquant_attn.py",
    "vllm/kernels/aiter_ops/",
    "vllm/kernels/vllm_c/",
    "vllm/kernels/xpu_ops/",
    "vllm/distributed/device_communicators/cuda_communicator.py",
    "vllm/distributed/device_communicators/cpu_communicator.py",
    "vllm/distributed/device_communicators/xpu_communicator.py",
    "vllm/distributed/device_communicators/ray_communicator.py",
)


def _is_vllm_auto_false_path(filename):
    if filename.startswith(VLLM_AUTO_FALSE_DIRS):
        return True
    if filename.endswith(VLLM_AUTO_FALSE_EXTS):
        return True
    if filename in VLLM_AUTO_FALSE_FILES:
        return True
    for prefix in VLLM_AUTO_FALSE_PLATFORM_SPECIFIC:
        if filename == prefix or filename.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


def vllm_ascend_affected(commit):
    files = commit.get("files", [])
    if not files:
        return True
    return any(not _is_vllm_auto_false_path(f.get("filename", "")) for f in files)


def determine_type_tags(message_lower):
    tags = []
    if any(w in message_lower for w in ["[bugfix]", "fix ", "fixes ", "fix:", "hotfix", "bug"]):
        tags.append("bugfix")
    elif any(w in message_lower for w in ["[feature]", "[feat]", "add ", "support ", "implement", "new"]):
        tags.append("feature")
    elif any(w in message_lower for w in ["[refactor]", "[cleanup]", "refactor", "restructure", "rename"]):
        tags.append("refactor")
    elif any(w in message_lower for w in ["[perf]", "[optimization]", "optimize", "performance", "speedup"]):
        tags.append("performance")
    elif any(w in message_lower for w in ["[doc]", "docs", "document"]):
        tags.append("docs")
    elif any(w in message_lower for w in ["[test]", "test_", "tests/"]):
        tags.append("test")
    elif any(w in message_lower for w in ["[ci]", "[build]", "chore", "bump", "upgrade"]):
        tags.append("ci")
    else:
        tags.append("chore")
    return tags


def determine_risk_tags(message_lower, files, stats):
    total_changes = stats.get("total_additions", 0) + stats.get("total_deletions", 0)
    files_changed = stats.get("files_changed", 0)
    
    has_core_code = any(
        f.get("filename", "").startswith(("vllm/v1/", "vllm/engine/", "vllm/config/"))
        for f in files
    )
    
    has_cuda_kernel = any(
        f.get("filename", "").endswith((".cu", ".cuh")) or "csrc/" in f.get("filename", "")
        for f in files
    )
    
    if "[bugfix]" in message_lower and has_core_code and total_changes > 100:
        return ["high-risk"]
    elif has_core_code and total_changes > 200:
        return ["medium-risk"]
    elif has_cuda_kernel:
        return ["medium-risk"]
    elif any(t in message_lower for t in ["[doc]", "[test]", "[ci]", "docs/", "tests/"]):
        return ["low-risk"]
    else:
        return ["medium-risk"]


def determine_module_tags(files, message_lower):
    tags = []
    filenames = [f.get("filename", "") for f in files]
    
    module_keywords = {
        "attention": ["attention", "attn"],
        "scheduler": ["scheduler", "schedule"],
        "sampler": ["sampler", "sampling", "logits"],
        "tokenizer": ["tokenizer"],
        "model-runner": ["model_runner", "model_executor"],
        "spec-decode": ["spec_decode", "speculative", "eagle"],
        "mrv2": ["model_runner_v2", "mrv2", "v2_model_runner"],
        "kernels": ["kernel", "csrc/", "kernels/"],
        "quantization": ["quant", "quantize", "w4a8", "w8a8", "fp8"],
        "distributed": ["distributed", "communicator", "allgather", "allreduce"],
        "compilation": ["compile", "torch.compile", "cudagraph"],
        "moe": ["moe", "mixture_of_experts", "expert"],
        "multimodal": ["multimodal", "vision", "image", "audio", "video"],
        "reasoning": ["reasoning", "thinking", "think_budget"],
        "prefix-caching": ["prefix", "pcp", "kv_cache"],
        "ascend": ["ascend", "npu", "huawei", "310p", "910"],
        "config": ["config/"],
        "worker": ["worker/"],
        "engine": ["engine/"],
    }
    
    for tag, keywords in module_keywords.items():
        for fn in filenames:
            fn_lower = fn.lower()
            if any(kw in fn_lower for kw in keywords):
                tags.append(tag)
                break
    
    if is_mrv2_commit_from_files(filenames, message_lower):
        if "mrv2" not in tags:
            tags.append("mrv2")
        if "model-runner" not in tags:
            tags.append("model-runner")
    
    return list(set(tags))


def is_mrv2_commit_from_files(filenames, message_lower):
    import re
    text = message_lower + " " + " ".join(filenames)
    for pattern in MRV2_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def analyze_vllm_commit(commit):
    message = commit.get("message", "")
    message_lower = message.lower()
    files = commit.get("files", [])
    stats = commit.get("stats", {})
    sha = commit["sha"]
    
    title = message.split("\n")[0]
    
    tags = []
    tags.extend(determine_type_tags(message_lower))
    tags.extend(determine_risk_tags(message_lower, files, stats))
    tags.extend(determine_module_tags(files, message_lower))
    tags = list(set(tags))
    
    comment_parts = []
    
    if "thinking_budget" in title.lower() or "thinking" in message_lower and "budget" in message_lower:
        comment_parts.append(
            "修复 thinking_token_budget 在自然结束 </think> 后重新进入 <think> 时预算未正确执行的问题。"
            "核心改动在 thinking_budget_state.py 的状态机逻辑：自然退出后重置 start_thinking/end_thinking 为 -1，"
            "并更新 scan_offset 以便后续重新检测新的思考块。"
        )
    elif "flashmla" in message_lower:
        comment_parts.append(
            "更新 FlashMLA 外部依赖的 git tag，修复 dense fp8 metadata crash 问题（num_sm_parts clamp）。"
            "属于外部依赖版本升级，不影响 vLLM 核心代码。"
        )
    elif "topk_ids" in message_lower or "moe_align" in message_lower:
        comment_parts.append(
            "重构 moe_align_sum kernel 中的 expert ID 查找逻辑，提取 get_local_expert_id 辅助函数，"
            "统一处理 padding 和 expert_map 场景。修复 topk_ids padding 导致的越界问题。"
        )
    elif "register" in message_lower and "qwen" in message_lower and "example" in message_lower:
        comment_parts.append(
            "在测试模型注册表中新增 Qwen3.5-4B 示例模型配置，用于测试覆盖。"
        )
    else:
        short_title = title[:80]
        comment_parts.append(f"变更：{short_title}")
    
    asc_affected = vllm_ascend_affected(commit)
    
    if asc_affected:
        core_files = [
            f["filename"] for f in files
            if not _is_vllm_auto_false_path(f["filename"])
        ]
        functionality = (
            f"涉及 vLLM 核心代码变更（{', '.join(core_files[:3])}），"
            "可能影响 vllm-ascend 的相应模块实现。建议 Ascend 侧关注接口兼容性。"
        )
        testing = "建议在 Ascend 平台上进行相关功能的回归测试。"
        needs_update = True
        suggested_areas = core_files[:5]
    else:
        functionality = "无影响"
        testing = "无影响"
        needs_update = False
        suggested_areas = []
    
    comment = " ".join(comment_parts)
    
    return {
        "sha": sha,
        "comment": comment,
        "tags": tags,
        "ascend_impact": {
            "ascend_affected": asc_affected,
            "functionality": functionality,
            "testing": testing,
            "needs_test_update": needs_update,
            "suggested_test_areas": suggested_areas,
        }
    }


def analyze_vllm_ascend_commit(commit):
    message = commit.get("message", "")
    message_lower = message.lower()
    files = commit.get("files", [])
    stats = commit.get("stats", {})
    sha = commit["sha"]
    
    title = message.split("\n")[0]
    
    tags = []
    tags.extend(determine_type_tags(message_lower))
    tags.extend(determine_risk_tags(message_lower, files, stats))
    tags.extend(determine_module_tags(files, message_lower))
    tags = list(set(tags))
    
    comment_parts = []
    
    lower_title = title.lower()
    
    if "cpu binding" in lower_title or "cpu_binding" in lower_title:
        comment_parts.append(
            "支持 Ascend 950 的 CPU binding topo affinity 模式，基于 NPU-CPU 拓扑亲和性分配 CPU 集群。"
            "新增 UVB polling 线程绑定、cluster 大小解析等功能。重构 CPU binding 辅助函数。"
        )
    elif "fused_infer_attention" in lower_title or "contiguous" in lower_title:
        comment_parts.append(
            "修复 fused_infer_attention ops 的 contiguous 错误，"
            "在调用 npu_fused_infer_attention_score 前对 key/value 张量进行 contiguous 处理，"
            "确保 Ascend950 硬件兼容性。"
        )
    elif "mrv2" in lower_title or "eagle decode" in lower_title or "model runner v2" in lower_title:
        comment_parts.append(
            "【MRV2】MRV2 Eagle 解码 draft 阶段合并为单个 ACL graph 调用，"
            "减少 graph 切换开销，提升推测解码性能。"
        )
    elif "shortrequestfirst" in lower_title or "short request first" in lower_title:
        comment_parts.append(
            "新增 ShortRequestFirst 调度策略，优先调度短请求，"
            "改善延迟敏感场景下的用户体验。涉及调度器核心逻辑变更。"
        )
    elif "gdn conv1d" in lower_title or "metadata" in lower_title and "device" in lower_title:
        comment_parts.append(
            "将 GDN conv1d 的元数据从 CPU 移动到 device tensor，减少 host-device 数据传输，"
            "优化推理性能。涉及大量文件的元数据访问路径调整。"
        )
    elif "sfa" in lower_title and "dcp" in lower_title:
        comment_parts.append(
            "SFA（Sparse Flash Attention）支持 DCP with replicate-indexer，"
            "扩展稀疏注意力的分布式部署能力。"
        )
    elif "triton" in lower_title and "swiglu" in lower_title:
        comment_parts.append(
            "新增 Triton swiglustep kernel 用于 SwigluStep 激活函数，"
            "提供更灵活的激活函数计算后端。"
        )
    elif "gemma4" in lower_title and "modelslim" in lower_title:
        comment_parts.append(
            "支持 Gemma4 ModelSlim 量化方案，新增相应的量化配置和模型适配。"
        )
    elif "prefix caching" in lower_title or "slidingwindow" in lower_title:
        comment_parts.append(
            "修复 DeepSeek-V4 前缀缓存中 SlidingWindowManager 调度块对齐问题，"
            "确保滑动窗口与前缀缓存正确协作。"
        )
    elif "allgatherep" in lower_title or "mxfpw4a8" in lower_title:
        comment_parts.append(
            "修复 Ascend950 allgatherEP MXFPW4A8 量化问题，修正量化 matmul 的形状处理。"
        )
    elif "fusedmoe" in lower_title and "activation" in lower_title:
        comment_parts.append(
            "修复 FusedMoE 量化路径中使用模型激活函数而非 SwiGLU 的问题，"
            "确保与模型配置的激活函数一致。"
        )
    elif "structured output" in lower_title:
        comment_parts.append(
            "修复混合结构化输出后端的 guard 逻辑，确保不同结构化输出后端正确协作。"
        )
    elif "pp pcp" in lower_title or "hidden state" in lower_title:
        comment_parts.append(
            "修复 PP（Pipeline Parallel）PCP 隐藏状态恢复问题，"
            "确保流水线并行场景下的正确性。"
        )
    elif "kv cache operators" in lower_title or "replace pa" in lower_title:
        comment_parts.append(
            "替换 PA（Prefix Attention）KV cache operators，"
            "更新前缀缓存的算子实现，提升性能或功能。"
        )
    elif "flashcomm" in lower_title and "vit" in lower_title:
        comment_parts.append(
            "修复 ViT 场景下的 FlashComm1/2 跳过逻辑，"
            "避免视觉模型不必要的通信操作。"
        )
    elif "310p" in lower_title and "sdma" in lower_title:
        comment_parts.append(
            "修复 310P 平台上异步拷贝导致的 SDMA 错误，"
            "确保数据拷贝的时序正确性。"
        )
    elif "quanttype" in lower_title or "naming convention" in lower_title:
        comment_parts.append(
            "统一 QuantType 枚举的命名规范，提升代码一致性和可维护性。"
        )
    elif "step3p5" in lower_title and "_pad_query" in lower_title:
        comment_parts.append(
            "修复 Step3.5 中 _pad_query_start_loc_for_fia 的 bug，"
            "确保查询填充位置计算正确。"
        )
    elif "kv quant sparse" in lower_title or "symbolic shapes" in lower_title:
        comment_parts.append(
            "修复 kv quant sparse flash attention 中的符号形状保留问题，"
            "确保动态形状场景下的正确性。"
        )
    elif "w4a4mxfp" in lower_title or "quant_matmul shape" in lower_title:
        comment_parts.append(
            "修复 w4a4mxfp quant_matmul 形状错误，修正矩阵乘法的维度处理。"
        )
    elif "flashcomm1 sp" in lower_title and "step3.5" in lower_title:
        comment_parts.append(
            "为 Step3.5 适配 FlashComm1 SP 通信模式，优化分布式推理通信效率。"
        )
    elif "[doc]" in lower_title:
        comment_parts.append("文档更新，不影响代码逻辑。")
    elif "[test]" in lower_title:
        comment_parts.append("测试用例更新或基线调整。")
    elif "[ci]" in lower_title:
        comment_parts.append("CI 配置变更，不影响运行时功能。")
    else:
        short_title = title[:80]
        comment_parts.append(f"变更：{short_title}")
    
    needs_test = stats.get("files_changed", 0) > 0 and any(
        f.get("status") in ("modified", "added") and "test" not in f.get("filename", "").lower()
        for f in files
    )
    
    if needs_test:
        reason = "核心代码变更，建议补充或更新相应测试用例以保证质量。"
    else:
        reason = "仅涉及测试、文档或 CI 配置变更，无需新增测试。"
    
    suggested_areas = [
        f.get("filename") for f in files
        if f.get("status") in ("modified", "added") and "test" not in f.get("filename", "").lower()
    ][:5]
    
    comment = " ".join(comment_parts)
    
    return {
        "sha": sha,
        "comment": comment,
        "tags": tags,
        "test_impact": {
            "needs_test_update": needs_test,
            "reason": reason,
            "suggested_test_areas": suggested_areas,
        }
    }


def build_summary(commits_data, analysis_results, is_vllm):
    commits = commits_data.get("commits", [])
    
    mrv2_count = sum(1 for ac in analysis_results if "mrv2" in ac.get("tags", []))
    bugfix_count = sum(1 for ac in analysis_results if "bugfix" in ac.get("tags", []))
    feature_count = sum(1 for ac in analysis_results if "feature" in ac.get("tags", []))
    perf_count = sum(1 for ac in analysis_results if "performance" in ac.get("tags", []))
    high_risk = sum(1 for ac in analysis_results if "high-risk" in ac.get("tags", []))
    medium_risk = sum(1 for ac in analysis_results if "medium-risk" in ac.get("tags", []))
    
    summary_parts = []
    
    if mrv2_count > 0:
        summary_parts.append(f"【MRV2 相关改动】{mrv2_count} 个 commits 涉及 Model Runner V2。")
    
    type_parts = []
    if bugfix_count > 0:
        type_parts.append(f"{bugfix_count} 个 bugfix")
    if feature_count > 0:
        type_parts.append(f"{feature_count} 个 feature")
    if perf_count > 0:
        type_parts.append(f"{perf_count} 个性能优化")
    if type_parts:
        summary_parts.append(f"当日共 {len(commits)} 个 commits，包含 {'，'.join(type_parts)}。")
    else:
        summary_parts.append(f"当日共 {len(commits)} 个 commits，主要为常规维护。")
    
    risk_parts = []
    if high_risk > 0:
        risk_parts.append(f"{high_risk} 个高风险")
    if medium_risk > 0:
        risk_parts.append(f"{medium_risk} 个中风险")
    if risk_parts:
        summary_parts.append(f"风险分布：{'、'.join(risk_parts)}。")
    
    daily_summary = "".join(summary_parts)
    
    if is_vllm:
        affected_count = sum(
            1 for ac in analysis_results
            if ac.get("ascend_impact", {}).get("ascend_affected")
        )
        if affected_count > 0:
            affected_files = set()
            for ac in analysis_results:
                if ac.get("ascend_impact", {}).get("ascend_affected"):
                    for area in ac["ascend_impact"].get("suggested_test_areas", []):
                        affected_files.add(area.split("/")[1] if "/" in area else area)
            ascend_summary = (
                f"当日 {affected_count} 个 commits 可能影响 vllm-ascend，"
                f"主要涉及 {', '.join(list(affected_files)[:5])} 等模块。"
                f"建议 Ascend 侧关注接口兼容性并进行回归测试。"
            )
        else:
            ascend_summary = "当日所有变更均为 tests / docs / CI / 平台特化代码，对 vllm-ascend 无影响。"
        return daily_summary, ascend_summary
    else:
        needs_test_count = sum(
            1 for ac in analysis_results
            if ac.get("test_impact", {}).get("needs_test_update")
        )
        if needs_test_count > 0:
            test_summary = (
                f"当日 {needs_test_count} 个 commits 涉及核心代码变更，建议更新测试用例。"
                f"重点关注新功能（如 ShortRequestFirst 调度、Ascend 950 CPU binding）的测试覆盖。"
            )
        else:
            test_summary = "当日变更主要为测试、文档或 CI 配置，无需新增测试用例。"
        return daily_summary, test_summary


def deep_analyze(repo, date, data_dir):
    is_vllm = "vllm-ascend" not in repo
    
    repo_dir = os.path.join(data_dir, repo_dir_name(repo))
    commits_path = os.path.join(repo_dir, "commits", f"{date}.json")
    analysis_path = os.path.join(repo_dir, "analysis", f"{date}.json")
    
    commits_data = load_json(commits_path)
    if commits_data is None:
        print(f"No commit data for {repo} on {date}")
        return False
    
    commits = commits_data.get("commits", [])
    if not commits:
        print(f"No commits for {repo} on {date}")
        return False
    
    print(f"Deep analyzing {len(commits)} commits for {repo} on {date}...")
    
    analyzed = []
    for commit in commits:
        if is_vllm:
            ac = analyze_vllm_commit(commit)
        else:
            ac = analyze_vllm_ascend_commit(commit)
        analyzed.append(ac)
    
    daily_summary, second_summary = build_summary(commits_data, analyzed, is_vllm)
    
    analysis = {
        "date": date,
        "repo": repo,
        "generated_at": datetime.now(TZ_CN).isoformat(),
        "daily_summary": daily_summary,
        "commits": analyzed,
    }
    
    if is_vllm:
        analysis["ascend_impact_summary"] = second_summary
    else:
        analysis["test_impact_summary"] = second_summary
    
    save_json_atomic(analysis_path, analysis)
    print(f"Deep analysis written to {analysis_path}")
    
    print("\n" + "=" * 60)
    print(f"📋 当日总结\n{daily_summary}")
    if is_vllm:
        print(f"\n⬆ vllm-ascend 影响\n{second_summary}")
    else:
        print(f"\n🧪 测试看护影响\n{second_summary}")
    print(f"\nCommits analyzed: {len(analyzed)}")
    print("=" * 60 + "\n")
    
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Deep analyze commits without LLM API")
    parser.add_argument("--repo", required=True, help="GitHub repo (owner/repo)")
    parser.add_argument("--date", default=None, help="Date to analyze (YYYY-MM-DD)")
    parser.add_argument("--latest", action="store_true", help="Analyze latest date")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    args = parser.parse_args()
    
    if not args.date and not args.latest:
        from datetime import datetime as dt
        args.date = dt.now(TZ_CN).strftime("%Y-%m-%d")
    
    if args.latest:
        repo_dir = os.path.join(args.data_dir, repo_dir_name(args.repo))
        commits_dir = os.path.join(repo_dir, "commits")
        files = sorted(
            [f for f in os.listdir(commits_dir) if f.endswith(".json") and f != "meta.json"],
            reverse=True,
        )
        if not files:
            print(f"No commit data found for {args.repo}")
            return 1
        args.date = files[0].replace(".json", "")
        print(f"Latest date: {args.date}")
    
    success = deep_analyze(args.repo, args.date, args.data_dir)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
