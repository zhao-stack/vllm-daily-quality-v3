---
name: github-live-check
description: Verify the latest live state of a GitHub repository without relying on a local checkout. Use when the user asks for the current or latest remote branch HEAD, commit ID or date, upstream adaptation point, pinned dependency commit, release anchor, or explicitly says to inspect the latest GitHub code rather than local code. Also use when GitHub webpage, connector, API, Raw, or git results may be stale or inconsistent.
---

# GitHub Live Check

Determine the live remote state quickly, distinguish authoritative data from caches, and report a timestamped, source-linked conclusion.

## Workflow

1. Resolve the exact repository, default branch, and requested artifact.
   - Use a repository named by the user.
   - For “current” or “latest,” inspect the remote default branch unless the user names another ref.
   - Do not use a local checkout as final evidence. Local metadata may only help identify the repository.

2. Query the smallest authoritative remote surface first.
   - For a branch tip, query the GitHub commit API or remote ref.
   - For a pinned commit stored in a file, read the file from GitHub Contents or Raw at the target ref.
   - For an upstream commit date, query that exact commit in the upstream repository.
   - Prefer a connected GitHub tool when it returns live structured data. For public time-sensitive files, confirm with Contents or Raw when cache freshness is uncertain.

3. Keep the fast path short.
   - Use a roughly 10-second timeout where controllable.
   - Retry a failed network request at most once.
   - Do not call several equivalent sources in sequence when the first authoritative result is coherent.
   - Treat authentication errors, network timeouts, connector transport failures, approval delays, and rate limits as different failure classes.

4. Validate only when needed.
   - If sources disagree, compare the target branch HEAD, raw file content at that ref, and the relevant commit timestamps.
   - Prefer a live API, Raw response, or exact Git ref over a crawled/search-indexed webpage.
   - Use a cache-busting query or `Cache-Control: no-cache` when practical.
   - Check whether an apparently different SHA is older, belongs to another branch, or comes from stale page indexing.
   - State the conflict and the basis for choosing the final value.

5. Report a concise result.
   - Include the “as of” date and timezone.
   - Include the full commit SHA, not only the short form.
   - Include the upstream commit date and exact timestamp when useful.
   - Link the remote anchor file, branch/commit, and upstream commit.
   - Explicitly say that the conclusion came from remote GitHub state rather than local code when that distinction matters.

## Evidence Priority

Use this order for time-sensitive GitHub facts:

1. Exact remote ref or GitHub commit/contents API response
2. GitHub Raw content at the exact ref
3. Connected GitHub repository/file result whose ref and freshness are clear
4. GitHub HTML commit or file page
5. Search-engine or crawled page content
6. Local checkout

Do not infer a commit date from a pull request title or upgrade label when the exact upstream commit is available.

## Failure Handling

- If the connected GitHub tool is slow or fails, switch to a direct public GitHub API or Raw request instead of repeatedly retrying the same route.
- If a CLI returns `401`, identify it as a credentials problem and use an authorized connector or public API for public data.
- If direct GitHub access times out, report the network limitation and the freshest independently verified evidence.
- Never present a cached or locally pinned SHA as current without qualifying its provenance.

## Example Requests

- “vllm-ascend 当前最新适配到了 vLLM 上游哪一天的 commit？”
- “查远端 main 最新 HEAD，不要看本地代码。”
- “这个仓库现在 pin 的上游 commit 是什么？”
- “GitHub 网页和 API 给出的 SHA 不一致，哪个才是最新的？”
