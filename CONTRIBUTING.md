# Contributing to AI Talent Radar

感谢你改进 AI Talent Radar。贡献必须同时满足工程质量、平台规则和候选人隐私要求。

## Before You Start

- 功能和缺陷可以通过公开 Issue 讨论，但 Issue 中不得包含真实候选人数据、日志正文、令牌或私人截图。
- 安全问题请按照 [SECURITY.md](SECURITY.md) 私下报告。
- 涉及新数据来源、联系方式或外部传输的改动，应先说明数据字段、公开依据、请求频率、失败处理和删除影响。
- 不接受绕过登录、验证码、签名、访问控制、速率限制或平台反自动化措施的实现。

## Development Setup

按照 [README.md](README.md) 创建 Python 3.11 虚拟环境并启动应用。核心测试不需要真实 API 凭证，也不应访问真实候选人账号。

运行完整 Python 测试：

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

检查浏览器 JavaScript：

```bash
node --check static/app.js
```

如改动容器配置，还应执行：

```bash
docker build -t ai-talent-radar:dev .
```

## Branches and Changes

1. 从最新默认分支创建短生命周期分支。
2. 每个 Pull Request 聚焦一个明确问题，避免无关重构和生成文件变更。
3. 为行为变化增加或更新测试。
4. 更新受影响的 README、隐私、安全和配置说明。
5. 提交前检查完整 diff，确认没有个人数据、凭证、本机路径或构建产物。

提交消息应简短描述行为，例如 `fix: preserve verified email on profile refresh`。Pull Request 说明应包含问题、实现、测试结果、隐私影响和回滚方式。

## Coding Guidelines

- 支持 Python 3.11，并优先使用标准库和现有项目模式。
- 保持 API 输入校验、SQL 参数化、URL 协议校验和输出转义。
- 采集器必须设置合理超时、低请求频率和明确的网络/限流错误。
- 不把平台错误静默解释为“候选人不存在”。
- 不在日志中输出候选人资料正文、邮箱、令牌或响应原文。
- 前端必须兼顾桌面和移动视口，不使用不安全的内联 HTML 拼接展示外部数据。
- 新依赖需要说明必要性、许可证、维护状态和替代方案，并固定可复现版本。

## Test Data Rules

所有测试数据必须是合成的：

- 使用 `candidate`, `example`, `test-user` 等虚构标识；
- 邮箱使用 `.example` 或 `.test` 域名；
- 链接使用标准保留域名或明确的模拟端点；
- 数据库写入临时目录，并在测试结束后清理；
- 网络调用使用 Mock，不访问真实个人主页。

禁止提交：

- `data/`、SQLite、备份、报告或表格导出；
- `.env`、令牌、Cookie、证书或私钥；
- 包含候选人信息的日志、终端输出、录屏或截图；
- 个人主目录、桌面、下载目录或本地运行时的绝对路径。

## Pull Request Checklist

- [ ] 改动范围清晰，没有无关文件。
- [ ] Python 3.11 测试通过。
- [ ] JavaScript 语法检查通过（如适用）。
- [ ] Docker 构建通过（如适用）。
- [ ] 新行为有测试，并覆盖失败路径。
- [ ] 没有真实个人信息、凭证、数据库、日志或导出文件。
- [ ] 没有个人绝对路径或本机专用配置。
- [ ] 数据来源遵守公开访问和平台规则。
- [ ] 已更新相关文档和隐私说明。

## License

向本项目提交贡献即表示你有权提交这些内容，并同意按照 [Apache License 2.0](LICENSE) 授权你的贡献。
