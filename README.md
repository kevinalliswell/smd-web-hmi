# smd-web-hmi · 熔滴炉上位机

面向 GB/T 34211—2017 铁矿石高温荷重还原软熔滴落试验的本地上位机。工控机后端通过以太网 HostComm 连接 STM32 工控板，板端连接传感器、控温仪、天平及 MFC，并执行实时流程和安全联锁。桌面与局域网浏览器共用界面、后台和试验数据。

## 项目状态

当前处于开发与联调阶段。`dev@855c84d / 0.3.0-rc.1` 是整改前基线，本轮候选版本已推进到 `0.3.0-rc.2`（尚未创建发布标签），不能代表整套设备已通过国标、Windows 或真机验收。已有功能与缺口见[验收证据基线](docs/verification.md)，后续进度统一记录在[任务清单](tasks/todo.md)。

| 项目 | 本轮软件实现 | 待验证边界 |
|---|---|---|
| 后台 | Python 3.13、FastAPI、SQLAlchemy、SQLite，单后台 | Windows 服务实测及现场24h |
| 前端 | Vue 3、Vite、Pinia、Chart.js，Node24与API类型检查 | 双端现场交互、LAN证书信任 |
| Windows | 固定WebView2薄壳、PyInstaller、NSIS、Windows Service、事务升级 | WindowsCI构建待结果；干净断网Win10/11未验收 |
| 试验 | 标准/非标版本配方、测定/冷却归档、质量指标与重复性 | 候选Mock已实现；固件冻结、国标符合性和M5未验收 |

详细设计、规范来源和维护入口见 [docs/README.md](docs/README.md)。工程协作规则见 [AGENTS.md](AGENTS.md)。

## 本地开发

以下为源码开发方式，生产安装方式以对应发布包说明为准。需要 Python 3.13，以及 Node 24；Python3.11仅保留后端兼容回归。依赖以仓库锁文件为准，目标运行时须通过 CI 后才算验证完成。

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
# 候选配方/生命周期联调另用 --extended-contract --time-scale 60
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

在 `frontend/` 执行 `npm run api:schema`、`npm run types:check`、`npm run typecheck`、`npm run lint`、`npm test` 和 `npm run build`。生成类型发生有意变更时先运行 `npm run types:generate` 并审查差异。这些软件检查不能替代板端联锁或真机实验验收。

- [开发路线图](tasks/plan.md)及[待办清单](tasks/todo.md)
- [实验要求与国标追踪](docs/experiment-spec.md)
- [系统架构与公共接口](docs/上位机软件开发规格说明书.md)
- [HostComm 契约与待冻结内容](docs/GB_T34211_HostComm上位机通信协议开发需求说明.md)
- [发布、备份与升级](docs/发布与维护指南.md)及[Windows 交付说明](deploy/windows/README.md)
- [变更记录](CHANGELOG.md)
