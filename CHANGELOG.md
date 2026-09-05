# 更新日志

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)；`backend/app/__init__.py` 的
`__version__` 为唯一权威版本源。条目按 Conventional Commits 类型归类。

## [Unreleased]

## [0.3.0-rc.1] - 2026-09-01

商用收口候选版。基于最新 `dev` 完成软件侧闭环；正式 `v0.3.0` 标签需等待供应商接口确认与真实 Windows 控制器 D4 验收。

### 安全

- PBKDF2-HMAC-SHA256 新密码提升到 600,000 轮，旧哈希在成功登录时透明升级。
- 增加账户 `token_version`；登出、改密/重置、角色变化和停用会立即撤销旧 REST/WS token。
- WebSocket 改为首帧认证，增加 Origin 校验、消息大小/频率限制、空闲超时和会话复核；浏览器 token 改存 `sessionStorage`。
- 生产关闭 OpenAPI 文档，增加请求 ID、安全响应头、敏感校验输入脱敏和 JSON 日志。
- 生产首次管理员使用安装器生成的一次性随机口令，不再使用通用 `admin/admin`。

### 业务与性能

- 试验启动持久化原始料层高度、样品标识和备注；设备侧命令仍保持既有 `test_id` 协议。
- 报告和多试验分析使用持久化高度，并以 SQL 聚合替代全量采样点入内存。
- 报告完整支持 HTML、PDF、XLSX，下载 MIME 与扩展名一致。
- 新增试验、报警、参数快照、操作审计和报告查询索引；增长型报警列表增加边界。

### 生产运维

- 生产启动要求数据库处于 Alembic head，禁止 `create_all()` 静默掩盖漏迁移。
- 增加 SQLite 在线备份、完整性校验、备份/导出保留期清理和详细就绪状态。
- Mock `disconnect_after` 故障注入从占位变为真实断线，可用于重连演练。
- Windows 包增加 ACL 收紧、安全升级自动回滚、计划任务自动启动/失败重启、日志轮转、健康检查和包结构校验。
- CI/Release 后端覆盖率门槛提升到 80%，并校验 Windows 部署脚本。

## [0.2.0] - 2026-08-29

D3 工程基线：自本版起启用 trunk-based 工作流（受保护 main + PR + squash 合并）、
CI 门禁与 tag 触发的离线发布流水线。全面代码审查发现的 15 项缺陷见 issues #13–#18，
修复计划见 `docs/发布与维护指南.md` §5。

### 新增
- 后端托管前端构建产物（同源部署、SPA 回退、路径穿越防护），生产免 CORS
- `release.yml`：打 tag 自动构建 Windows 离线安装包（前端 dist + win_amd64 wheels + 部署脚本）并创建 GitHub Release；支持 workflow_dispatch 试运行
- `deploy/windows/`：install / upgrade / run 脚本与现场运维说明（含 DB 备份与回滚 SOP，未实机验证）
- `docs/发布与维护指南.md`：分支/版本/CI/发布/修复波次全流程
- pre-commit 配置（black + isort）

### 变更
- CI 重构：静态检查 job、覆盖率门槛 70%、后端矩阵 ubuntu(py3.11/3.12)+windows(py3.11)、依赖审计非阻塞 job、并发取消、前端 dist 产物上传
- CLAUDE.md §6：git 工作流由 main/dev/feature 改为 trunk-based
- 后端全量 black/isort 格式化，配置固化于 `backend/pyproject.toml`

## [0.1.0]

D2 原型 → D3 联调期的早期开发（未打 tag）：HostComm 客户端与 Mock Server、
FastAPI 全部 P0 路由、SQLite 全表、前端 11 页、二次确认与审计、CI 初版。
