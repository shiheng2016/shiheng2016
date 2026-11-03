# 主页维护与统计口径

本项目按 https://github.com/realchendahuang/realchendahuang/blob/master/PROMPT.md 的视觉要求实现。
参考布局：https://github.com/DietrichGebert/DietrichGebert 。个人资料仅来自用户公开 GitHub 资料，不推测生日、操作系统、IDE、母语或工作状态。

## 更新

- GitHub Actions 每天 04:17 UTC（台北/上海 12:17）刷新；GitHub 定时任务可能延迟。
- Actions → Update profile stats → Run workflow 可手动刷新。
- 纯 Python 标准库，Python 3.12+；不需要 pip install。
- 本地运行：`GITHUB_TOKEN="$(gh auth token)" python3 update_profile.py`。
- 若本地 Python 缺少系统 CA，可使用 `SSL_CERT_FILE=/etc/ssl/cert.pem`，不要关闭 TLS 验证。
- README 严格保留五行 picture，维护说明放本文件，不加进主页。
- 图片采用 GitHub 的 `raw.github.com` 官方旧域名（重定向到 raw.githubusercontent.com），让 GitHub Markdown 使用 camo 图片代理。这样本地网络不能直连 raw.githubusercontent.com 时仍可显示。GitHub 图片缓存可能导致短时显示旧统计。

## 数据含义

- **Repos**：REST `/users/shiheng2016` 的 `public_repos`，包括 fork，和公开主页一致。
- **Contributed**：GraphQL `repositoriesContributedTo`，类型 COMMIT / PULL_REQUEST / REPOSITORY，按 GitHub 返回范围，非所有时间所有仓库的完整名单。
- **Stars**：账号拥有的公开仓库（包括 fork）当前收到的星标之和，不是本人给别人的 stars。
- **Commits**：从账号创建年份至今，逐年统计 contribution calendar 的 commit contributions。公开模式过滤掉私有仓库，不包含 PR、issue、review。GitHub 对 fork/default branch/邮箱归属的贡献规则同样适用。
- **Lines of Code**：遍历账号拥有仓库（包括 fork）的**当前默认分支可达历史**，仅统计 GraphQL author ID 归属于本人提交的 additions/deletions；展示 `净增量 (+增加 -删除)`。不是现存代码行数、不是全平台/所有分支贡献、不排除生成文件；跨 fork 重复可达提交按各仓库分别累计。
- **Languages**：非 fork 仓库中各仓库前 3 种语言的出现频次排名，展示前 3；不是个人熟练度推断。
- **Account since / Location / Contact**：公开 GitHub profile 元数据。
- **Updated**：API 采集开始时间（UTC）；GitHub API/camo 缓存可能造成暂时延迟。

本地即使使用能看私有代码的 PAT，默认仍强制 PUBLIC，和 Actions 的 GITHUB_TOKEN 一致。只有主动设置 **ACCESS_TOKEN** 才纳入可见私有仓库及私有贡献聚合；公开 SVG 会泄露这些汇总数字，请确认后再设置。ACCESS_TOKEN 不改变 Repos 的公开仓库计数。私有模式 commits 使用 `totalCommitContributions + restrictedContributionsCount`，后者可能涵盖不可见的非 commit 贡献，因此不是严格纯 commit 计数。

API 请求重试且遍历全部分页，最多 8 个并发；任何仓库失败或 GraphQL 部分错误都会让更新失败并保留上次完整 SVG，不把错误伪装成 0。极高活跃年份达到 100 个贡献仓库时拒绝静默截断。

## 换头像

`img/avatar.png` 是 GitHub 头像的 PNG 快照，便于跨平台复现，自动统计不重新下载头像。

```bash
python3 tools/ascii_avatar.py img/avatar.png --background portrait-wall > tools/avatar.txt
```

将生成字符画同步进 `update_profile.py` 的 `ART` raw string 后，重新运行统计脚本，再提交脚本、ASCII 文本、头像与两个 SVG。头像转换具体选项见 `python3 tools/ascii_avatar.py --help`。当前头像背景不是蓝天，不应盲目使用蓝色背景抠图。

## 部署

仓库必须公开且命名为 `shiheng2016/shiheng2016`。Workflow 只 stage 两个 SVG，不会误提交凭据或本地缓存。若 GitHub 对主页显示提供 Share to Profile 按钮，可在仓库页面启用。初次部署后打开 https://github.com/shiheng2016 验证。
