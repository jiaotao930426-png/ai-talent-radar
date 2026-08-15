# AI Talent Radar Open-Source Validation

验证日期：2026-08-16

## 交付范围

- Windows 10/11、macOS 和主流 Linux 的本地浏览器应用；
- Docker Compose 首选启动方式，Python 3.11 原生启动作为备用；
- 每台设备使用独立的本地 SQLite 数据库；
- Apache-2.0 许可证、贡献指南、隐私规则和安全报告流程；
- GitHub Actions 跨平台测试、Docker 构建和仓库隐私检查。
- macOS 登录后自动启动模板和既有 GitHub/Gitee 主页邮箱重新核验工具。

当前稳定代码位于公开 GitHub 仓库的 `main` 分支：[jiaotao930426-png/ai-talent-radar](https://github.com/jiaotao930426-png/ai-talent-radar)。本次验收从公开仓库重新取得了 `main` 归档（验收时提交为 `90ba3d0`），未发布容器镜像，也未把本地数据上传到仓库。

## 数据边界

- 开源目录不包含候选人数据库、真实邮箱、日志、备份、导出表格、`.env` 或凭证；
- 测试只使用合成身份及 `.example` / `.test` 保留域名；
- 没有复制现有本地运行目录或修改其数据库；
- 采集器只使用公开接口或公开页面，不绕过登录、验证码、访问控制或平台限制。

## 已完成验证

- 71 项 Python 单元与接口测试已在 GitHub Actions 的 Python 3.11、Ubuntu、macOS 和 Windows 全部通过；本次从公开 `main` 归档在本机 Python 3.9 环境重新执行，71/71 通过；公开归档的 HTTP 流程另以临时回环服务逐项验证；
- 空数据库首次启动、默认关闭每周任务、配置写入和服务重启持久化通过；
- 空人才池 Excel 导出可生成有效 `.xlsx`；
- Excel 的日期、公式注入防护、XML 控制字符清理和原生超链接通过回读测试；
- JavaScript、POSIX Shell、Python 导入和 YAML 语法检查通过；
- 桌面视口完成应用内浏览器页面加载、总览、每周任务保存、人才池和数据管理页面交互检查；
- 当前运行版本与开源副本逐项对照：核心 Python 模块、前端页面、API 路由、SQLite 字段、定时任务、核验/联系进度、报告和导出字段一致；Excel 生成器改为可移植实现，保留双工作表、字段、原生外链和公式安全处理；
- 已补齐当前版本的 macOS 自动启动能力（使用无个人路径的模板）及既有 GitHub/Gitee 主页邮箱重新核验工具；
- SQLite 连接在上下文退出时会提交或回滚并释放文件句柄，Windows 临时数据库清理不再被文件锁阻塞；自动补跑时间格式不依赖系统 locale；
- 390 x 844 移动视口未完成自动化验收：Playwright 所需浏览器分发包未安装，后续应用内浏览器访问也被本机安全策略拒绝；不能将移动视口标记为已验证；
- 新目录未发现数据库、日志、导出文件、疑似凭证或个人绝对路径。

## 2026-08-16 公开归档复测

- 从公开 `main` 重新取得的源码与验收时记录的仓库提交 `90ba3d0c37a7d3eef4eef9337f0ab32144b8ce4b` 一致，工作区无未提交改动。
- URL 采集（`https://github.com/octocat`）和关键词搜索（`octocat`）均完成；候选人详情、核验字段、联系进度、归档、恢复、永久删除、备份和数据库整理接口均返回成功。
- HTML 周报返回 200；Excel 下载返回有效 XLSX，包含“候选人总表”和“项目证据”两个工作表、可点击公开链接和邮箱链接。
- 本机没有 Docker Desktop/ Docker Engine，因此没有声称本机完成 Docker 构建；Windows 启动脚本由 GitHub Actions 的 Windows 检查覆盖，本机未安装 PowerShell，未重复执行脚本解析。

## 2026-08-15 本机运行验收

- 当前机器已按此前约定移除 Docker Desktop，未检测到 `docker` 命令；因此本轮没有在本机重跑 Docker Compose 构建或容器启动。
- 使用项目对应的 Python 3.12 运行开源副本的非端口测试：66/66 通过（包含 `openpyxl` Excel 回读测试）；5 个 HTTP 测试因当前执行环境禁止测试套接字绑定，改用临时端口服务和接口流程逐项验证。
- 临时数据库服务的健康接口、总览、来源状态、人才池、候选人详情、核验字段保存、联系进度、邮箱复制、HTML 报告和 Excel 导出均返回成功。
- 导出的 `.xlsx` 已用 `openpyxl` 回读，包含“候选人总表”和“项目证据”两个工作表、原生邮箱/主页链接和日期字段；归档、恢复、备份、永久删除及每周一 10:00 计划也已在临时数据库验证。
- Docker Compose 配置、非 root 镜像构建和三平台测试仍由 GitHub Actions 工作流覆盖；需要重新验证 Docker 时，应在安装 Docker Desktop/Engine 的机器上执行 README 中的命令。

## CI 验证结果

当前验收设备没有 PowerShell，因此未在本机执行 Windows 启动脚本。GitHub Actions 运行 [31869245064](https://github.com/jiaotao930426-png/ai-talent-radar/actions/runs/31869245064) 已通过以下门禁：

- Python 3.11 在 Windows、macOS、Linux 的完整测试；
- PowerShell 与 POSIX 启动脚本解析；
- Docker Compose 配置校验与非 root 镜像构建；
- 敏感文件、疑似凭证、候选人导出物和个人绝对路径检查。

- CI 结论：全部通过。公开 `main` 已完成本机公开归档复测；Docker 构建、Windows 脚本和移动视口仍以 CI/后续设备验收结果为准。

## 发布结论

- 公开仓库 / 本地部署：PASS WITH WARNINGS；
- 当前 `main` 可作为稳定公开版本使用；旧 `v1.0.0` 标签仍保留为历史版本，下载最新版应使用默认 `main` 分支或最新稳定标签；
- 运行服务不应直接暴露到公网：当前产品没有多人登录鉴权，默认只监听本机回环地址。

## 运行提醒

- 默认只监听 `127.0.0.1`，应用没有多人登录鉴权，不应直接暴露到局域网或公网；
- Docker 数据保存在命名卷，执行删除卷操作前必须先备份；
- GitHub Token 仅通过本机环境或部署 Secrets 注入，不得提交到仓库；
- 年龄、学历、工作地点意愿和 Agent 项目归属必须人工或向候选人核验。
