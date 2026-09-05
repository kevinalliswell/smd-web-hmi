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
| 认证 | JWT（HS256）+ 数据库会话版本撤销 + PBKDF2-SHA256（600k） |
| 报告 | HTML · PDF · XLSX |
| 测试 | pytest + pytest-asyncio（后端 189 例，覆盖率 80%+）· Vitest（前端 65 例） |

## 已实现功能（D3）

| 模块 | 能力 |
|---|---|
| HostComm | TCP 长连接客户端（握手/心跳/指数退避重连/超时/容错帧解析）+ Mock（报警与定时断线注入） |
| 实时通道 | WebSocket 首帧认证、Origin/大小/频率/空闲限制；状态、事件与通信质量推送 |
| 命令 | 权限矩阵 + 状态校验 + CO 命令二次确认令牌 + 操作审计；start/stop/pause/resume/tare/ack |
| 试验生命周期 | start_test 建会话并归档 H/样品/备注、实时写 `sample_point`、stop_test 收尾 |
| 参数 | `set_parameters` 全链路：非运行态校验→CRC→下发→回读确认→快照入库 |
| 报警 | 分级（L1/L2/L3）落库、活跃/历史、确认（发 `ack_alarm`） |
| 报告/导出 | SQL 计算 ΔPmax/Td/T10/T40 等；生成 PDF/XLSX/HTML；日志导出 CSV zip |
| 前端页面 | 总览/趋势/当前试验/报警/历史/参数/诊断/设置/分析/报告（11 页全功能） |
| 角色 | Observer / Operator / Admin / Maintainer，前后端一致门控；改密/停用/改角色即时撤销会话 |
| 运维 | Alembic head 启动门禁、SQLite 在线备份/校验/清理、就绪检查、Windows 自动重启 |

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
pip install --require-hashes -r requirements-dev.lock
cp ../.env.example .env          # 按需修改
sed -i.bak 's/HOSTCOMM_MOCK=false/HOSTCOMM_MOCK=true/' .env  # 本地无控制板开发
alembic upgrade head             # 建表
uvicorn app.main:app --reload --port 8000
```

> 生产模式（`HOSTCOMM_MOCK=false`）必须配置至少 32 字节的 `SMD_JWT_SECRET`、至少
> 12 字符的首次管理员一次性口令，并先执行 `alembic upgrade head`，否则拒绝启动。
> Windows 安装器会自动生成密钥和受 ACL 保护的一次性口令文件。仅 Mock 开发模式允许空 JWT 密钥并回退到
> `admin/admin`；该开发口令首次登录仍强制修改。

### HostComm Mock Server（无真实控制板时，另开终端）

```bash
cd backend
python -m app.hostcomm.mock_server                  # 正常模式
python -m app.hostcomm.mock_server --demo-alarms    # 周期注入演示报警
python -m app.hostcomm.mock_server --mode reject_all
python -m app.hostcomm.mock_server --fault disconnect_after=10s
```

### 前端

```bash
cd frontend
npm install
npm run dev          # 开发服务器（/api、/ws 代理到 localhost:8000）
```

Mock 开发默认账户为 `admin / admin`。生产安装的一次性口令见安装目录
`initial-admin-password.txt`；首次登录改密后应安全删除该文件。

### 运行测试

```bash
cd backend && pytest -q --cov=app --cov-fail-under=80
cd frontend && npm run test             # Vitest（store + 组件）
```

## 部署与发布

生产为**同源部署**：后端直接托管 `vite build` 产物（自动探测 `frontend/dist`，或经
`SMD_FRONTEND_DIST` 指定），单端口 8000、无跨域。打 tag `vX.Y.Z` 自动触发发布流水线，
构建 Windows 离线安装包并创建 GitHub Release。现场安装 / 升级 / 回滚见
[`deploy/windows/README.md`](./deploy/windows/README.md)；分支、版本与发布全流程见
[`docs/发布与维护指南.md`](./docs/发布与维护指南.md)，变更记录见 [`CHANGELOG.md`](./CHANGELOG.md)。

如调试环境必须跨域访问，使用 `SMD_CORS_ORIGINS` 配置逗号分隔的明确来源；生产模式
禁止 `*`，且后端不启用跨域凭证。仅 `HOSTCOMM_MOCK=true` 的开发环境默认允许通配来源。

## 当前阶段

`v0.3.0-rc.1` 已达到软件商用候选标准：自动化、迁移、安全、备份、报告和 Windows
离线包流程均已收口。正式标签只等待供应商接口表核对与真实 Windows/STM32 D4 验收。

D3 阶段交付标准见 `CLAUDE.md` 第 9 节，P0 测试矩阵（T01-T15）见
`docs/上位机软件开发规格说明书.md` 第 9.2 节。

> ⚠ 部分实现基于对供应商接口的**推导性约定**（状态机枚举、参数结构、CRC 算法、
> 事件码、结果指标定义等），待接口冻结表/数据字典到位后逐项核对，详见
> `docs/待确认事项与接口对齐清单.md`。
