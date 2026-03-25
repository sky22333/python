### 统计每个仓库`artifact`的用量
```
python actions.py
```

### 删除所有的`artifact`缓存
1：下载并安装 [github cli](https://github.com/cli/cli/releases)

2：下载并安装 git

3：登录
```
gh auth login
```
一路回车，然后浏览器授权登录

4：PowerShell 执行清理命令
> `sky22333`是我的github用户名，注意切换为你的
```
gh repo list sky22333 --limit 100 --json nameWithOwner --jq '.[].nameWithOwner' | ForEach-Object { $repo = $_; Write-Host "处理仓库: $repo"; while ($true) { $ids = gh run list --repo $repo --limit 500 --json databaseId --jq '.[].databaseId'; if (-not $ids) { break }; $ids | ForEach-Object { gh run delete $_ --repo $repo; Start-Sleep -Milliseconds 300 } }; gh api repos/$repo/actions/artifacts --paginate --jq '.artifacts[].id' | ForEach-Object { gh api -X DELETE repos/$repo/actions/artifacts/$_; Start-Sleep -Milliseconds 300 } }; Write-Host "全部清理完毕"
```
