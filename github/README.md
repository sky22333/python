- 查看历史提交信息，提交地址结尾添加`.patch`
- 批量替换github仓库提交信息的用户名和邮箱，防止隐私泄露。支持 HTTPS + Token 自动认证推送。

---

1：需要py环境，然后安装以下依赖：
```
apt install git-filter-repo git -y
```
2：`repos.txt`文件中放入github仓库地址，一行一个。

3：在`git.py`代码中添加`GitHub用户名和 Token变量`

4：然后执行`python git.py`
