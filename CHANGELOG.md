# 更新日志

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)；`backend/app/__init__.py` 的
`__version__` 为唯一权威版本源。条目按 Conventional Commits 类型归类。

## [Unreleased]

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
