#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 批量替换github仓库提交信息的用户名和邮箱，支持 HTTPS + Token 自动认证推送，需要安装apt install git-filter-repo

import os
import shutil
import subprocess
import sys
import tempfile

# 配置：统一替换的用户名和邮箱
NEW_NAME = "NewName"
NEW_EMAIL = "newemail@example.com"

# GitHub 认证用户名和 Token变量
GITHUB_USERNAME = "sky22333"
GITHUB_TOKEN = "xxxxxxxxxxxxxxxxxxxxxxxxxxx"

# 跳过处理的用户名列表（这些用户名的提交将保持不变）
SKIP_AUTHORS = [
    "github-actions[bot]",
    # 在此添加更多需要跳过的用户名
]

# 仓库列表文件，每行一个仓库地址（HTTPS或SSH）
REPOS_FILE = "repos.txt"


def validate_config():
    """验证配置参数"""
    if not NEW_NAME or not NEW_NAME.strip():
        print("错误：NEW_NAME 不能为空")
        return False

    if not NEW_EMAIL or not NEW_EMAIL.strip():
        print("错误：NEW_EMAIL 不能为空")
        return False

    if not GITHUB_USERNAME or not GITHUB_USERNAME.strip():
        print("错误：GITHUB_USERNAME 不能为空")
        return False

    if not GITHUB_TOKEN or not GITHUB_TOKEN.strip():
        print("错误：GITHUB_TOKEN 不能为空")
        return False

    return True


def check_git_available():
    """检查git命令是否可用"""
    try:
        result = subprocess.run(["git", "--version"], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"Git版本：{result.stdout.strip()}")
            return True
    except FileNotFoundError:
        pass

    print("错误：未找到git命令，请确保Git已安装并在PATH中")
    return False


def run_command(cmd, cwd=None, timeout=300):
    """执行命令并处理错误"""
    print(f"执行命令: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode != 0:
            print(f"命令执行失败 (退出码: {result.returncode}): {' '.join(cmd)}")
            if result.stdout:
                print("标准输出:", result.stdout)
            if result.stderr:
                print("错误输出:", result.stderr)
            return False, result.stderr

        if result.stdout:
            print("输出:", result.stdout.strip())

        return True, result.stdout

    except subprocess.TimeoutExpired:
        print(f"命令执行超时 ({timeout}秒): {' '.join(cmd)}")
        return False, "超时"
    except Exception as e:
        print(f"命令执行异常: {e}")
        return False, str(e)


def get_authenticated_url(repo_url):
    """
    如果配置了 GITHUB_USERNAME 和 GITHUB_TOKEN，则返回带 token 的 HTTPS URL，
    否则返回原 URL。
    """
    if not GITHUB_USERNAME or not GITHUB_TOKEN:
        return repo_url

    if repo_url.startswith("git@"):
        # ssh形式转换成 https
        https_url = repo_url.replace("git@", "https://").replace(":", "/")
    elif repo_url.startswith("https://"):
        https_url = repo_url
    else:
        # 其他情况直接返回
        https_url = repo_url

    prefix = "https://"
    if https_url.startswith(prefix):
        return prefix + f"{GITHUB_USERNAME}:{GITHUB_TOKEN}@" + https_url[len(prefix):]
    else:
        return https_url


def process_repo(repo_url):
    """处理单个仓库"""
    print(f"\n{'=' * 60}")
    print(f"开始处理仓库：{repo_url}")
    print(f"{'=' * 60}")

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix="gitrepo_")
        print(f"创建临时目录: {temp_dir}")

        # 克隆仓库时用原始地址，避免暴露 token
        print("正在克隆仓库...")
        success, error = run_command(
            ["git", "clone", "--mirror", repo_url, temp_dir],
            timeout=600
        )
        if not success:
            print(f"克隆失败：{error}")
            return False

        # 移除旧的 origin
        run_command(["git", "remote", "remove", "origin"], cwd=temp_dir)

        # 添加远程 origin，使用带 token 的 HTTPS URL
        auth_url = get_authenticated_url(repo_url)
        print(f"使用认证地址重新添加远程 origin: {auth_url}")
        success, error = run_command(["git", "remote", "add", "origin", auth_url], cwd=temp_dir)
        if not success:
            print(f"添加远程地址失败: {error}")
            return False

        print(f"修改提交的作者信息为：{NEW_NAME} <{NEW_EMAIL}>")
        print(f"跳过的用户名: {SKIP_AUTHORS}")

        print("正在修改提交作者信息...")
        skip_authors_str = ", ".join([f'"{author}"' for author in SKIP_AUTHORS])
        callback_script = f'''
skip_authors = [{skip_authors_str}]
current_author = commit.author_name.decode('utf-8')

if current_author not in skip_authors:
    commit.author_name = b"{NEW_NAME}"
    commit.author_email = b"{NEW_EMAIL}"
    commit.committer_name = b"{NEW_NAME}"
    commit.committer_email = b"{NEW_EMAIL}"
'''

        success, error = run_command([
            "git", "filter-repo",
            "--force",
            "--commit-callback", callback_script
        ], cwd=temp_dir, timeout=1800)
        if not success:
            print(f"filter-repo 执行失败：{error}")
            return False

        # filter-repo 会删除远程，重新添加带 token 的 remote
        run_command(["git", "remote", "remove", "origin"], cwd=temp_dir)
        success, error = run_command(["git", "remote", "add", "origin", auth_url], cwd=temp_dir)
        if not success:
            print(f"重新添加远程失败: {error}")
            return False

        print("正在推送修改到远程仓库...")

        # ==== 修改点：不使用 --mirror 推送，避免推送 refs/pull/* 导致拒绝推送 ====
        # 推送所有分支
        success, error = run_command(["git", "push", "--force", "origin", "refs/heads/*:refs/heads/*"], cwd=temp_dir, timeout=600)
        if not success:
            print(f"推送分支失败：{error}")
            return False

        # 推送所有标签
        success, error = run_command(["git", "push", "--force", "origin", "refs/tags/*:refs/tags/*"], cwd=temp_dir, timeout=600)
        if not success:
            print(f"推送标签失败：{error}")
            return False

        print("✅ 仓库处理完成！")
        return True

    except Exception as e:
        print(f"处理仓库时发生未预期的错误：{e}")
        return False
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                print(f"清理临时目录: {temp_dir}")
                shutil.rmtree(temp_dir)
            except Exception as e:
                print(f"清理临时目录失败：{e}")


def load_repositories():
    """加载仓库列表"""
    if not os.path.isfile(REPOS_FILE):
        print(f"错误：仓库列表文件 '{REPOS_FILE}' 不存在！")
        print("请创建该文件并在每行添加一个仓库URL")
        return None

    try:
        with open(REPOS_FILE, "r", encoding="utf-8") as f:
            repos = []
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if line and not line.startswith('#'):  # 支持注释行
                    repos.append(line)

        if not repos:
            print(f"错误：仓库列表文件 {REPOS_FILE} 中没有有效的仓库地址")
            return None

        print(f"加载了 {len(repos)} 个仓库地址")
        return repos

    except Exception as e:
        print(f"读取仓库列表文件时出错：{e}")
        return None


def main():
    """主函数"""
    print("Git 批量修改作者信息工具 - 自动模式")
    print("=" * 40)

    # 验证配置
    if not validate_config():
        sys.exit(1)

    # 检查依赖
    if not check_git_available():
        sys.exit(1)

    # 加载仓库列表
    repos = load_repositories()
    if repos is None:
        sys.exit(1)

    # 显示配置信息
    print(f"\n配置信息：")
    print(f"  新用户名: {NEW_NAME}")
    print(f"  新邮箱: {NEW_EMAIL}")
    print(f"  跳过的用户名: {SKIP_AUTHORS}")
    print(f"  GitHub 用户名: {GITHUB_USERNAME}")
    print(f"  仓库数量: {len(repos)}")
    print(f"\n开始自动批量处理...")

    # 处理仓库
    for i, repo_url in enumerate(repos, 1):
        print(f"\n处理进度: {i}/{len(repos)}")

        if not process_repo(repo_url):
            print(f"❌ 仓库处理失败，停止执行: {repo_url}")
            sys.exit(1)

    # 显示总结
    print(f"\n{'=' * 60}")
    print("✅ 所有仓库批量处理完成")
    print(f"{'=' * 60}")
    print(f"成功处理: {len(repos)}/{len(repos)}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n程序被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n程序发生未处理的异常：{e}")
        sys.exit(1)
