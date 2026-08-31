# smd-web-hmi — 熔滴炉 Web 上位机

GB/T 34211 熔滴炉本地工业 Web 上位机。运行在炉旁工控机上，通过局域网以太网与
STM32 控制板通信（HostComm，TCP 端口 `34211`，UTF-8 JSON Lines 协议）。

> 完整项目宪章见 [`CLAUDE.md`](./CLAUDE.md)，详细规格见 [`docs/`](./docs/)。
> 接口对齐待办（依赖供应商交付物）见 [`docs/待确认事项与接口对齐清单.md`](./docs/待确认事项与接口对齐清单.md)。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.11+ · FastAPI · SQLAlchemy 2.x（async）· SQLite · Uvicorn |
| 前端 | Vue 3 · Vite · Pinia · Vue Router · Chart.js · axios |
| 实时推送 | 原生 WebSocket（`/ws/realtime`） |
| 认证 | JWT（HS256，本地用户表，含角色字段） |
| 测试 | pytest + pytest-asyncio（后端 61 例）· Vitest（前端 29 例） |

## 已实现功能（D3）

| 模块 | 能力 |
|---|---|
| HostComm | TCP 长连接客户端（握手/心跳/指数退避重连/超时/容错帧解析）+ Mock Server（含 `--demo-alarms`） |
| 实时通道 | 状态快照缓存 → WebSocket `status_update` 推送；事件 → `alarm_new/clear/ack` 推送 |
| 命令 | 权限矩阵 + 状态校验 + CO 命令二次确认令牌 + 操作审计；start/stop/pause/resume/tare/ack |
| 试验生命周期 | start_test 建会话、实时写 `sample_point`、stop_test 收尾 |
| 参数 | `set_parameters` 全链路：非运行态校验→CRC→下发→回读确认→快照入库 |
| 报警 | 分级（L1/L2/L3）落库、活跃/历史、确认（发 `ack_alarm`） |
| 报告/导出 | 从 `sample_point` 算 ΔPmax/Td/T10/T40 等，生成 HTML 报告；日志导出 CSV zip |
| 前端页面 | 总览/趋势/当前试验/报警/历史/参数/诊断/设置/分析/报告（11 页全功能） |
| 角色 | Observer / Operator / Admin / Maintainer，前后端一致门控 |

## 安全红线（绝对不得违反）

1. 不提供"强制打开 CO 阀""绕过安全继电器""强制恢复加热许可"等任何接口（含诊断调试工具，仅允许 `get_status`/`get_parameters` 只读操作）。
2. 命令只发送"请求"，STM32 状态机与硬接线联锁拥有最终裁决权。
3. CO 相关命令（start_test / stop_test）必须二次确认 + 操作员身份记录 + 事件日志写库。
4. `sample_point` / `event_log` / `alarm_log` 表只追加，不得 DELETE / UPDATE。
5. HostComm 帧解析须容错（非法 JSON 只记录不 crash）。
6. `set_parameters` 必须非运行态、CRC 校验、回读确认，全程记入 `operator_action`。

## 目录结构

```
smd-web-hmi/
├── CLAUDE.md            # 项目宪章
├── docs/               # 设计文档（规格说明书、HostComm 协议、UI 原型、待确认清单）
├── backend/            # FastAPI 后端
│   ├── app/
│   │   ├── core/       # 配置、安全(JWT+PBKDF2)、日志
│   │   ├── db/         # ORM 模型(9 表)、引擎、Alembic 迁移
│   │   ├── api/        # REST 路由 + WebSocket + 依赖
│   │   ├── hostcomm/   # 客户端、协议(FrameParser)、Mock Server
│   │   └── services/   # 命令/参数/报告/导出/报警/日志/缓存
│   └── tests/          # pytest（T01-T15 + 命令/参数/报警/报告/历史/趋势/分析…）
└── frontend/           # Vue 3 前端
    └── src/{pages,stores,composables,components,api,constants,utils}
```

## 快速开始

### 后端

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp ../.env.example .env          # 按需修改
alembic upgrade head             # 建表
uvicorn app.main:app --reload --port 8000
```

> 生产模式（`HOSTCOMM_MOCK=false`）必须配置至少 32 字节的
> `SMD_JWT_SECRET`，否则后端拒绝启动。仅 Mock 开发模式允许留空，此时会告警并
> 生成重启即失效的临时密钥。设 `HOSTCOMM_MOCK=true` 后，后端启动时会自动连接本地 Mock。

### HostComm Mock Server（无真实控制板时，另开终端）

```bash
cd backend
python -m app.hostcomm.mock_server                  # 正常模式
python -m app.hostcomm.mock_server --demo-alarms    # 周期注入演示报警
python -m app.hostcomm.mock_server --mode reject_all
```

### 前端

```bash
cd frontend
npm install
npm run dev          # 开发服务器（/api、/ws 代理到 localhost:8000）
```

默认账户 `admin / admin`（首次登录后请尽快修改密码）。

### 运行测试

```bash
cd backend && pytest tests/ -v          # test_safety.py 验证安全红线
cd frontend && npm run test             # Vitest（store + 组件）
```

## 部署与发布

生产为**同源部署**：后端直接托管 `vite build` 产物（自动探测 `frontend/dist`，或经
`SMD_FRONTEND_DIST` 指定），单端口 8000、无跨域。打 tag `vX.Y.Z` 自动触发发布流水线，
构建 Windows 离线安装包并创建 GitHub Release。现场安装 / 升级 / 回滚见
[`deploy/windows/README.md`](./deploy/windows/README.md)；分支、版本与发布全流程见
[`docs/发布与维护指南.md`](./docs/发布与维护指南.md)，变更记录见 [`CHANGELOG.md`](./CHANGELOG.md)。

## 开发阶段

D2 原型已完成 → **D3 接口联调（当前，工程基线 `v0.2.x`，联调基线 `v0.3.0`）** → D4 冷态验收。

D3 阶段交付标准见 `CLAUDE.md` 第 9 节，P0 测试矩阵（T01-T15）见
`docs/上位机软件开发规格说明书.md` 第 9.2 节。

> ⚠ 部分实现基于对供应商接口的**推导性约定**（状态机枚举、参数结构、CRC 算法、
> 事件码、结果指标定义等），待接口冻结表/数据字典到位后逐项核对，详见
> `docs/待确认事项与接口对齐清单.md`。
