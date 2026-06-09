# smd-web-hmi — 熔滴炉 Web 上位机

GB/T 34211 熔滴炉本地工业 Web 上位机。运行在炉旁工控机上，通过局域网以太网与
STM32 控制板通信（HostComm，TCP 端口 `34211`，UTF-8 JSON Lines 协议）。

> 完整项目宪章见 [`CLAUDE.md`](./CLAUDE.md)，详细规格见 [`docs/`](./docs/)。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.11+ · FastAPI · SQLAlchemy 2.x（async）· SQLite · Uvicorn |
| 前端 | Vue 3 · Vite · Pinia · Vue Router · Chart.js · axios |
| 实时推送 | 原生 WebSocket（`/ws/realtime`） |
| 认证 | JWT（HS256，本地用户表，含角色字段） |
| 测试 | pytest + pytest-asyncio（后端）· Vitest（前端） |

## 安全红线（绝对不得违反）

1. 不提供"强制打开 CO 阀""绕过安全继电器""强制恢复加热许可"等任何接口。
2. 命令只发送"请求"，STM32 状态机与硬接线联锁拥有最终裁决权。
3. CO 相关命令（start_test / stop_test）必须二次确认 + 操作员身份记录 + 事件日志写库。
4. `sample_point` / `event_log` / `alarm_log` 表只追加，不得 DELETE / UPDATE。
5. HostComm 帧解析须容错（非法 JSON 只记录不 crash）。

## 目录结构

```
smd-web-hmi/
├── CLAUDE.md            # 项目宪章
├── docs/               # 设计文档（规格说明书、HostComm 协议、UI 原型）
├── backend/            # FastAPI 后端
│   ├── app/
│   │   ├── core/       # 配置、安全、日志
│   │   ├── db/         # ORM 模型、数据库引擎、迁移
│   │   ├── api/        # REST 路由 + WebSocket
│   │   ├── hostcomm/   # HostComm 客户端、协议、Mock Server
│   │   └── services/   # 命令服务、日志服务、状态缓存
│   └── tests/          # pytest（T01-T15）
└── frontend/           # Vue 3 前端
    └── src/{pages,stores,composables,components,api}
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

### HostComm Mock Server（无真实控制板时，另开终端）

```bash
cd backend
python -m app.hostcomm.mock_server          # 正常模式
python -m app.hostcomm.mock_server --mode reject_all
python -m app.hostcomm.mock_server --fault disconnect_after=10s
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

### 运行测试

```bash
cd backend
pytest tests/ -v          # test_safety.py 优先运行
```

## 开发阶段

D2 原型已完成 → **D3 接口联调（当前）** → D4 冷态验收。

D3 阶段交付标准见 `CLAUDE.md` 第 9 节，P0 测试矩阵（T01-T15）见
`docs/上位机软件开发规格说明书.md` 第 9.2 节。
