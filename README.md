# smd-web-hmi · 熔滴炉上位机

面向 GB/T 34211—2017 铁矿石高温荷重还原软熔滴落试验的本地上位机。工控机后端通过以太网 HostComm 连接 STM32 工控板，板端连接传感器、控温仪、天平及 MFC，并执行实时流程和安全联锁。桌面与局域网浏览器共用界面、后台和试验数据。

## 项目状态

当前处于开发与联调阶段。`0.3.0-rc.1` 是本次整改的代码基线版本，不能代表整套设备已通过国标、Windows 或真机验收。已有功能与缺口见[验收证据基线](docs/verification.md)，后续进度统一记录在[任务清单](tasks/todo.md)。

| 项目 | 当前基线 | 交付目标 |
|---|---|---|
| 后台 | Python 3.11+、FastAPI、SQLAlchemy、SQLite | Python 3.13；每台设备单一后台服务 |
| 前端 | Vue 3、Vite、Pinia、Chart.js | 桌面/Web 共用构建，Node 24 LTS 工具链 |
| Windows | 源码、依赖 wheels、计划任务脚本 | Win 10/11 x64 独立离线安装包和 Windows Service |
| 试验 | 启停、状态、参数及报告基础链路 | 完整标准实验、受限阶段式非标配方、可追溯报告 |

详细设计、规范来源和维护入口见 [docs/README.md](docs/README.md)。工程协作规则见 [AGENTS.md](AGENTS.md)。

## 本地开发

以下为源码开发方式，生产安装方式以对应发布包说明为准。需要 Python 3.11 或目标环境 3.13，以及 Node 24。依赖以仓库锁文件为准，目标运行时须通过 CI 后才算验证完成。

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements-dev.lock
cp ../.env.example .env
```

编辑 `backend/.env`：无设备开发设 `HOSTCOMM_MOCK=true`；真实设备设地址、至少 32 字节随机 JWT 密钥及首次管理员口令。启动前执行迁移：

```bash
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

另开终端运行模拟板（Mock 开关不会替你启动模拟器）：

```bash
cd backend
python -m app.hostcomm.mock_server
```

前端另开终端：

```bash
cd frontend
npm ci
npm run dev
```

Windows PowerShell 用 `.venv\Scripts\Activate.ps1` 激活虚拟环境。Mock 开发账户为 `admin/admin`，首次登录仍须改密。生产必须使用一次性口令，改密后移除受保护的初始口令文件。

## 验证与维护

```bash
python scripts/check_docs.py
cd backend
pytest -q --cov=app --cov-fail-under=80
```

在 `frontend/` 执行 `npm run lint`、`npm test` 和 `npm run build`。这些软件检查不能替代板端联锁或真机实验验收。

- [开发路线图](tasks/plan.md)及[待办清单](tasks/todo.md)
- [实验要求与国标追踪](docs/experiment-spec.md)
- [系统架构与公共接口](docs/上位机软件开发规格说明书.md)
- [HostComm 契约与待冻结内容](docs/GB_T34211_HostComm上位机通信协议开发需求说明.md)
- [发布、备份与升级](docs/发布与维护指南.md)及[现有 Windows 脚本说明](deploy/windows/README.md)
- [变更记录](CHANGELOG.md)
