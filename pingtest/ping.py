import asyncio
import aiohttp
import time
import urllib3
import os
from typing import List, Tuple, Optional
import ssl
from dataclasses import dataclass
from pathlib import Path

# 忽略 HTTPS 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

@dataclass
class TestResult:
    url: str
    success: bool
    latency: Optional[int]
    status_code: Optional[int] = None
    error: Optional[str] = None

class AsyncSiteTester:
    def __init__(self, max_concurrent: int = 80, timeout: int = 10):
        self.max_concurrent = max_concurrent
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.semaphore = asyncio.Semaphore(max_concurrent)
        
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        
        # 请求头配置
        self.docker_headers = {
            "User-Agent": "docker/20.10.23 go/go1.20.5 git-commit/1234567 kernel/5.15.0 os/linux arch/amd64 UpstreamClient(Docker-Client/20.10.23)",
            "Accept": "application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json, application/json",
            "Connection": "close",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache"
        }
        
        self.github_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/plain, text/html, application/json, */*",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache"
        }

    def normalize_url(self, line: str) -> Optional[str]:
        """规范化URL"""
        line = line.strip()
        if not line or line.startswith("#"):
            return None
        if not line.startswith("http://") and not line.startswith("https://"):
            line = "https://" + line
        return line

    def load_sites(self, filename: str = "docker.txt") -> List[str]:
        """从文件加载Docker Registry域名"""
        sites = []
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                for line in f:
                    url = self.normalize_url(line)
                    if url:
                        sites.append(url)
        return sites

    def load_github_proxies(self, filename: str = "github.txt") -> List[str]:
        """从文件加载GitHub代理域名并生成测试URL"""
        urls = []
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                for line in f:
                    domain = line.strip()
                    if not domain or domain.startswith("#"):
                        continue
                    # 自动加 https:// 如果没有
                    if not domain.startswith("http://") and not domain.startswith("https://"):
                        domain = "https://" + domain
                    test_url = f"{domain}/https://raw.githubusercontent.com/microsoft/vscode/main/LICENSE.txt"
                    urls.append(test_url)
        return urls

    async def test_docker_registry(self, session: aiohttp.ClientSession, site: str) -> TestResult:
        """异步测试Docker Registry"""
        async with self.semaphore:
            start_time = time.time()
            try:
                async with session.get(f"{site}/v2/", headers=self.docker_headers, ssl=self.ssl_context) as response:
                    latency = round((time.time() - start_time) * 1000)
                    success = response.status in (200, 401)
                    return TestResult(site, success, latency, response.status)
                        
            except asyncio.TimeoutError:
                latency = round((time.time() - start_time) * 1000)
                return TestResult(site, False, latency, error="Timeout")
            except Exception as e:
                latency = round((time.time() - start_time) * 1000)
                return TestResult(site, False, latency, error=str(e))

    async def test_github_proxy(self, session: aiohttp.ClientSession, url: str) -> TestResult:
        """异步测试GitHub代理"""
        async with self.semaphore:
            start_time = time.time()
            try:
                async with session.get(url, headers=self.github_headers, ssl=self.ssl_context) as response:
                    latency = round((time.time() - start_time) * 1000)
                    success = 200 <= response.status < 400
                    return TestResult(url, success, latency, response.status)
                    
            except asyncio.TimeoutError:
                latency = round((time.time() - start_time) * 1000)
                return TestResult(url, False, latency, error="Timeout")
            except Exception as e:
                latency = round((time.time() - start_time) * 1000)
                return TestResult(url, False, latency, error=str(e))

    async def run_batch_tests(self, urls: List[str], test_func, batch_size: int = 50) -> List[TestResult]:
        """批量运行测试，避免同时发起过多请求"""
        all_results = []

        connector = aiohttp.TCPConnector(
            limit=self.max_concurrent,
            limit_per_host=20,
            ssl=self.ssl_context,
            enable_cleanup_closed=True
        )
        
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=self.timeout,
            trust_env=True
        ) as session:
            for i in range(0, len(urls), batch_size):
                batch_urls = urls[i:i + batch_size]
                
                tasks = [test_func(session, url) for url in batch_urls]
                
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in batch_results:
                    if isinstance(result, Exception):
                        all_results.append(TestResult("unknown", False, None, error=str(result)))
                    else:
                        all_results.append(result)
                
                if i + batch_size < len(urls):
                    await asyncio.sleep(0.1)
        
        return all_results

    def print_results(self, results: List[TestResult], label: str = ""):
        """打印测试结果"""
        success_results = [r for r in results if r.success]
        failed_results = [r for r in results if not r.success]
        
        success_count = len(success_results)
        fail_count = len(failed_results)
        
        valid_latencies = [r.latency for r in success_results if r.latency is not None]
        
        print(f"\n[{label}] 检测结果：")
        print(f"正常数量: {success_count}, 失败数量: {fail_count}")
        if valid_latencies:
            print(f"延迟(ms) → 最快: {min(valid_latencies)}, 最慢: {max(valid_latencies)}, 平均: {round(sum(valid_latencies)/len(valid_latencies), 2)}")

        for result in results:
            status = "✅" if result.success else "❌"
            latency_str = f"{result.latency}ms" if result.latency else "-"
            display_url = result.url
            if "/https://raw.githubusercontent.com/" in result.url:
                display_url = result.url.split("/https://raw.githubusercontent.com/")[0]
            print(f"{status} {display_url} 延迟: {latency_str}")

    def cleanup_failed_sites(self, results: List[TestResult], filename: str, is_github: bool = False):
        """清理测速失败的站点"""
        if not os.path.exists(filename):
            print(f"文件 {filename} 不存在，无法清理")
            return

        # 获取成功的站点URL集合
        success_urls = set()
        for result in results:
            if result.success:
                if is_github:
                    if "/https://raw.githubusercontent.com/" in result.url:
                        domain = result.url.split("/https://raw.githubusercontent.com/")[0]
                        success_urls.add(domain)
                else:
                    success_urls.add(result.url)

        with open(filename, "r", encoding="utf-8") as f:
            lines = f.readlines()

        kept_lines = []
        removed_count = 0
        
        for line in lines:
            original_line = line.strip()
            if not original_line or original_line.startswith("#"):
                kept_lines.append(line)
                continue
                
            if is_github:
                domain = original_line
                if not domain.startswith("http://") and not domain.startswith("https://"):
                    domain = "https://" + domain
                
                if domain in success_urls:
                    kept_lines.append(line)
                else:
                    removed_count += 1
                    print(f"移除失败站点: {original_line}")
            else:
                url = self.normalize_url(original_line)
                if url and url in success_urls:
                    kept_lines.append(line)
                else:
                    removed_count += 1
                    print(f"移除失败站点: {original_line}")

        if removed_count > 0:
            with open(filename, "w", encoding="utf-8") as f:
                f.writelines(kept_lines)
            print(f"已从 {filename} 中移除 {removed_count} 个失败站点")
        else:
            print(f"{filename} 中没有需要移除的失败站点")

    def ask_cleanup(self, docker_results: Optional[List[TestResult]] = None, 
                   github_results: Optional[List[TestResult]] = None):
        """询问用户是否清理失败的站点"""
        if not docker_results and not github_results:
            return
            
        has_docker_failures = docker_results and any(not r.success for r in docker_results)
        has_github_failures = github_results and any(not r.success for r in github_results)
        
        if not has_docker_failures and not has_github_failures:
            print("\n所有站点测试都成功，无需清理")
            return

        print("\n" + "="*50)
        response = input("是否移除测速失败的站点？(输入 y 确认，其他输入或回车取消): ").strip().lower()
        
        if response == 'y':
            print("\n开始清理失败站点...")
            
            if has_docker_failures and docker_results:
                self.cleanup_failed_sites(docker_results, "docker.txt", is_github=False)
                
            if has_github_failures and github_results:
                self.cleanup_failed_sites(github_results, "github.txt", is_github=True)
                
            print("清理完成！")
        else:
            print("已取消清理操作")

    async def run_all_tests(self):
        """运行所有测试"""
        docker_sites = self.load_sites("docker.txt")
        github_urls = self.load_github_proxies("github.txt")
        
        docker_results = None
        github_results = None
        
        if docker_sites:
            docker_results = await self.run_batch_tests(docker_sites, self.test_docker_registry)
            self.print_results(docker_results, "Docker Registry")
        else:
            print("未找到 docker.txt 或文件为空")
        
        if github_urls:
            github_results = await self.run_batch_tests(github_urls, self.test_github_proxy)
            self.print_results(github_results, "GitHub Proxy")
        else:
            print("未找到 github.txt 或文件为空")
        
        # 询问是否清理失败的站点
        self.ask_cleanup(docker_results, github_results)

async def main():
    """主函数"""
    tester = AsyncSiteTester(max_concurrent=80, timeout=8)
    await tester.run_all_tests()

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    asyncio.run(main())
