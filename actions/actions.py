#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import time

# ================= 配置 =================
GITHUB_TOKEN = "token"     # GitHub Token
GITHUB_USER = "user"       # GitHub 用户名
PER_PAGE = 100
MAX_WORKERS = 8                # 控制并发避免触发 API 限速
OUTPUT_FILE = Path("./github_artifacts_usage.txt")
PROXY = "socks5h://127.0.0.1:10808"  # None 不走代理
# =======================================

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}
PROXIES = {"http": PROXY, "https": PROXY}

# ================= 通用请求 =================
def fetch_json(url, retry=0):
    try:
        resp = requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=15)
        if resp.status_code in (403, 429):
            reset = resp.headers.get("X-RateLimit-Reset")
            wait = int(reset) - int(time.time()) if reset else 60
            wait = max(wait, 1)
            print(f"⚠️ 限速等待 {wait}s: {url}")
            time.sleep(wait)
            return fetch_json(url, retry)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        if retry < 3:
            time.sleep(2 ** retry)
            print(f"🔁 重试 {retry+1}/3: {url}")
            return fetch_json(url, retry + 1)
        print(f"❌ 请求失败: {url}\n  错误: {e}")
        return {}

def delete_request(url, retry=0):
    try:
        resp = requests.delete(url, headers=HEADERS, proxies=PROXIES, timeout=15)
        if resp.status_code in (403, 429):
            reset = resp.headers.get("X-RateLimit-Reset")
            wait = int(reset) - int(time.time()) if reset else 60
            wait = max(wait, 1)
            print(f"⚠️ 删除限速等待 {wait}s: {url}")
            time.sleep(wait)
            return delete_request(url, retry)
        resp.raise_for_status()
        return True
    except Exception as e:
        if retry < 3:
            time.sleep(2 ** retry)
            return delete_request(url, retry + 1)
        print(f"❌ 删除失败: {url}\n  错误: {e}")
        return False

# ================= 获取仓库 =================
def get_all_repos():
    repos = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/user/repos"
            f"?per_page={PER_PAGE}&page={page}"
            f"&visibility=all&affiliation=owner,collaborator,organization_member"
        )
        data = fetch_json(url)
        if not data:
            break
        repos.extend([r["full_name"] for r in data])
        page += 1
    return repos

# ================= 获取 workflow runs =================
def get_workflow_runs(repo):
    runs = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{repo}/actions/runs?per_page={PER_PAGE}&page={page}"
        data = fetch_json(url)
        workflow_runs = data.get("workflow_runs", [])
        if not workflow_runs:
            break
        runs.extend([run["id"] for run in workflow_runs])
        page += 1
    return runs

# ================= 获取 artifacts =================
def get_artifacts(repo, run_id):
    data = fetch_json(f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts")
    artifacts = data.get("artifacts", [])
    return repo, [
        {
            "repo": repo,
            "run_id": run_id,
            "id": a["id"],
            "name": a["name"],
            "size_mb": a["size_in_bytes"] / 1024 / 1024,
            "created_at": a["created_at"]
        }
        for a in artifacts
    ]

# ================= 删除 artifact =================
def delete_artifact(repo, artifact_id):
    url = f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}"
    if delete_request(url):
        print(f"✅ 删除 artifact {artifact_id} ({repo})")
        return True
    return False

# ================= 主程序 =================
def main():
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    repo_artifacts = defaultdict(list)
    total_size = 0

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        repos = get_all_repos()
        print(f"👤 用户: {GITHUB_USER}")
        print(f"📦 仓库数: {len(repos)}\n")
        f.write(f"用户: {GITHUB_USER}\n仓库数: {len(repos)}\n\n")

        futures = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            for repo in repos:
                print(f"🔍 扫描仓库: {repo}")
                runs = get_workflow_runs(repo)
                for run_id in runs:
                    futures.append(executor.submit(get_artifacts, repo, run_id))

            for future in as_completed(futures):
                try:
                    repo, artifacts = future.result()
                    repo_artifacts[repo].extend(artifacts)
                except Exception as e:
                    print(f"❌ task error: {e}")

        for repo in sorted(repo_artifacts.keys()):
            f.write(f"\n=== 仓库: {repo} ===\n")
            print(f"\n=== 仓库: {repo} ===")
            for a in repo_artifacts[repo]:
                line = (f"RunID: {a['run_id']} | {a['name']} | "
                        f"{a['size_mb']:.2f} MB | {a['created_at']}")
                print(line)
                f.write(line + "\n")
                total_size += a["size_mb"]

        summary = f"\n总 artifact 占用空间: {total_size:.2f} MB"
        print(summary)
        f.write(summary + "\n")

        one_month_ago = datetime.now(timezone.utc) - timedelta(days=30)

        # 收集过期 artifacts
        old_list = [
            (a["repo"], a["id"], a["name"], a["created_at"])
            for repo in repo_artifacts.values()
            for a in repo
            if datetime.fromisoformat(a["created_at"].replace("Z", "+00:00")) < one_month_ago
        ]

        if old_list:
            ans = input(f"\n检测到 {len(old_list)} 个一个月前的 artifacts，是否删除？(y/N): ").strip().lower()
            if ans == "y":
                print("⚡ 分批删除中...")
                for i in range(0, len(old_list), MAX_WORKERS):
                    batch = old_list[i:i + MAX_WORKERS]
                    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                        futures = [ex.submit(delete_artifact, repo, aid) for repo, aid, *_ in batch]
                        for _ in as_completed(futures):
                            pass
                    time.sleep(1)
            else:
                print("⚡ 已跳过删除。")

    print(f"\n📄 输出已写入: {OUTPUT_FILE.resolve()}")

if __name__ == "__main__":
    main()
