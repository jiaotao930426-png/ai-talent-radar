# AI Talent Radar

AI Talent Radar 是一个本地优先的 AI 人才发现与审核工具。它从允许访问的公开技术资料中整理候选人线索，支持人工核验、联系进度、定时任务、本地报告和数据导出。

本项目用于辅助招聘研究，不替代人工判断。年龄、学历、工作地点意愿和项目归属等结论必须由招聘人员核验，不应仅凭公开资料推断。

## 合规边界

- 仅处理候选人主动公开的职业资料与联系入口。
- 不绕过登录、验证码、访问控制、速率限制或平台反自动化机制。
- 不推断或补全未公开的邮箱、电话、年龄、学历等个人信息。
- 使用前应确认数据来源条款、适用法律和招聘场景下的处理依据。
- 不得把真实候选人数据库、导出文件、日志、截图或凭证提交到代码仓库。

详细规则见 [PRIVACY.md](PRIVACY.md) 和 [SECURITY.md](SECURITY.md)。
本次开源副本的检查结果见 [OPEN_SOURCE_VALIDATION.md](OPEN_SOURCE_VALIDATION.md)。

## 当前功能

- 手动搜索与公开链接分析。
- 每周定时采集、失败重试、任务取消和任务日志。
- 候选人去重、匹配评分、首次与最近采集时间。
- 公开联系方式分级、人工核验和联系进度管理。
- 本地 SQLite 存储、归档、备份和永久删除。
- 本地 HTML 周报与可选 Excel 导出。
- 来源连通性诊断，不使用登录态、Cookie 或浏览器凭证。
- 可选的 GitHub/Gitee 既有主页邮箱重新核验工具。
- macOS 登录后自动启动（可选，使用通用 `launchd` 模板）。

当前自动采集器支持 GitHub、Gitee、GitLab、Hugging Face 和 Stack Overflow。界面中标记为“规划中”或“人工链接”的来源尚未实现自动采集器。

## 系统要求

- Python 3.11
- 现代浏览器
- 可选：Node.js 20，用于 JavaScript 本地检查
- 可选：Docker Desktop 或 Docker Engine，用于容器构建

核心 Web 服务使用 Python 标准库，Excel 导出使用固定版本的 `openpyxl`。依赖声明位于 `requirements.txt`。

## 推荐启动：Docker Compose

Docker Compose 是首选安装方式，适用于 Windows 10/11、macOS 和主流 Linux。首次启动会构建本地镜像，并创建只在本机使用的持久化数据卷：

```bash
docker compose up --build -d
```

服务启动后访问 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)。查看运行状态或停止服务：

```bash
docker compose ps
docker compose down
```

`docker compose down` 不会删除命名卷中的数据库。除非已经备份并明确要清空数据，否则不要执行带 `--volumes` 的删除命令。

容器部署时必须使用持久化卷保存 `/data`，并只把端口发布到可信主机接口。`compose.yaml` 默认发布到 `127.0.0.1:8765`。不要把候选人数据打进镜像或提交到镜像仓库。

## 原生 Python 启动

### macOS / Linux

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --requirement requirements.txt
python -m unittest discover -s tests -p "test_*.py" -v
./scripts/start.sh
```

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --requirement requirements.txt
python -m unittest discover -s tests -p "test_*.py" -v
.\scripts\start.ps1
```

请通过 HTTP 地址使用产品，不要直接双击 `static/index.html`。

首次启动会在本地创建 SQLite 数据库。每周任务初始为关闭状态，需要使用者在页面中核对来源和时间后主动启用。默认情况下服务只监听回环地址，不能从其他设备访问；不要把这个没有登录鉴权的本地服务直接暴露到公网。

## macOS 登录后自动启动（可选）

当前版本在 macOS 上支持用户登录后自动启动。开源版使用不含个人绝对路径的模板，安装脚本会根据当前目录和 Python 路径生成本机配置：

```bash
chmod +x scripts/install-macos-launchd.sh scripts/uninstall-macos-launchd.sh
./scripts/install-macos-launchd.sh
```

安装后服务会保持运行，访问 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)。停止并取消自动启动：

```bash
./scripts/uninstall-macos-launchd.sh
```

脚本只创建当前用户的 `LaunchAgents` 项，不会上传数据；运行前请确保本机已安装 Python 3.11，或设置 `TALENT_RADAR_PYTHON`。

## 重新核验已有主页邮箱（可选）

采集任务会自动处理符合规则的公开联系方式。若需要对人才池中既有的 GitHub/Gitee 主页重新核验，可在项目根目录运行：

```bash
python tools/import_public_profile_emails.py
```

该工具只访问候选人主动公开的主页，只把唯一且明确的公开邮箱写入本地数据库，重复共享地址会保留为“尚未可靠核验”，终端只输出汇总数量，不输出邮箱或候选人资料正文。

## 配置

原生 Python 程序直接读取操作系统环境变量，不会自动加载 `.env` 文件。Docker Compose 会按其标准行为读取项目根目录的 `.env`；只应在本机创建该文件，并且不得提交真实令牌。

| 变量 | 必填 | 默认值 | 用途 |
| --- | --- | --- | --- |
| `GITHUB_TOKEN` | 否 | 未设置 | 提高 GitHub 公共 API 配额。只授予完成公开资料读取所需的最小权限。 |
| `TALENT_RADAR_HOST` | 否 | `127.0.0.1` | 服务监听地址。仅在容器或受保护代理中谨慎修改。 |
| `TALENT_RADAR_PORT` | 否 | `8765` | 本地服务端口。 |
| `TALENT_RADAR_DB` | 否 | `data/talent_radar.db` | SQLite 数据库路径。 |

不要把真实令牌写入代码、命令示例、`.env`、日志或 Issue。建议通过操作系统凭证管理工具或 CI 的加密 Secrets 注入凭证。应用的健康接口只返回 Token 是否已配置，不返回实际值。

## 数据与备份

- 默认数据库：`data/talent_radar.db`
- 默认备份目录：`data/backups/`
- 数据、备份、日志和导出文件已在 `.gitignore` 中排除。
- 删除候选人前创建的备份不会随候选人记录自动删除，需要由数据管理员按保留策略单独清理。
- 导出的表格和报告可能包含个人信息，应存放在受控目录并限制访问。

仓库不附带真实候选人数据。测试数据必须是合成数据，并使用 `.example` 或 `.test` 保留域名。

## 测试

运行 Python 测试：

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

检查浏览器 JavaScript 语法：

```bash
node --check static/app.js
```

CI 会在 Python 3.11 的 Windows、macOS 和 Linux 环境运行测试，并额外检查 JavaScript、Docker 构建、敏感文件、疑似凭证和个人绝对路径。

## 项目结构

```text
.
|-- app.py                 # 本地 HTTP 服务与 API
|-- collectors.py          # 公开来源采集器
|-- db.py                  # SQLite 数据层
|-- jobs.py                # 手动任务与定时任务
|-- scoring.py             # 匹配评分
|-- source_health.py       # 来源状态诊断
|-- static/                # 前端页面
|-- tests/                 # 自动化测试
|-- scripts/               # macOS/Linux 与 Windows 启动脚本
|-- launchd/               # macOS 自动启动模板（可选）
|-- tools/                 # 本地邮箱重新核验等辅助工具
|-- Dockerfile             # 非 root 容器镜像
|-- compose.yaml           # 回环端口和持久卷配置
|-- OPEN_SOURCE_VALIDATION.md # 开源副本验收报告
|-- SECURITY.md            # 安全报告流程
|-- PRIVACY.md             # 数据与隐私规则
`-- CONTRIBUTING.md        # 贡献指南
```

## 参与贡献

提交代码前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题不要提交公开 Issue，请按照 [SECURITY.md](SECURITY.md) 私下报告。

## 许可证

本项目使用 [Apache License 2.0](LICENSE)。
