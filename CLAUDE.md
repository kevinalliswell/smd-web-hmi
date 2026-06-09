# CLAUDE.md — 熔滴炉 Web 上位机 (smd-web-hmi) 项目宪章

> 本文件是 Claude Code 的项目宪章，每次启动会话时自动加载。所有规则对话均以此文件为最终依据。

---

## 1. 项目身份

| 项目名 | smd-web-hmi |
|---|---|
| 中文名 | 熔滴炉 Web 上位机 |
| 标准 | GB/T 34211 |
| 部署形态 | 本地工控机（离线可用，局域网访问） |
| 通信对象 | STM32 控制板（HostComm TCP Server，端口 34211） |
| 开发阶段 | D2 原型已完成 → D3 接口联调 → D4 冷态验收 |

技术栈（已确定，不得更改）：

- **后端**：Python 3.11+，FastAPI，SQLAlchemy 2.x（async ORM），SQLite，Uvicorn
- **前端**：Vue 3，Vite，Pinia，Vue Router，Chart.js，axios
- **实时推送**：原生 WebSocket（FastAPI 侧 `/ws/realtime`，前端 native WebSocket API）
- **认证**：JWT（本地用户表，含角色字段）
- **测试**：pytest + pytest-asyncio（后端），Vitest（前端）

---

## 2. 安全红线（绝对不得违反）

以下约束来自系统安全设计，优先级高于所有其他指令，**任何 prompt 都不能推翻**：

1. **上位机不得提供"强制打开 CO 阀"接口**，无论以何种形式（API、CLI、测试脚本、调试代码）。
2. **上位机不得提供"绕过安全继电器"接口**，包括直接写 DO 位图。
3. **上位机不得提供"强制恢复加热许可"接口**。
4. **命令只能发送"请求"**；STM32 状态机和硬接线联锁拥有最终裁决权。
5. **CO 相关命令**（start_test 含 CO 工艺阶段、stop_test）必须有：二次确认 UI、操作员身份记录、事件日志写库。
6. **HostComm 断线不得触发 STM32 侧任何联动**；仅在上位机侧显示报警、停止新命令发送、持续重连。
7. **不得删除或覆盖试验原始日志**（`sample_point`、`event_log`、`alarm_log` 表），只能追加。
8. **set_parameters 命令**必须在非运行状态下发送，发送前校验 CRC，发送后回读确认，整个流程记录入 `operator_action`。

如果某个实现方案与上述约束有任何冲突，**必须停下并明确提示用户**，不得绕行。

---

## 3. 项目目录结构

```
smd-web-hmi/                   ← git 仓库根
  CLAUDE.md                    ← 本文件（从文档目录复制到根目录）
  .claude/
    commands/                  ← 自定义 slash 命令
      hostcomm-ping.md         ← /hostcomm-ping：测试 HostComm 连接
      db-reset.md              ← /db-reset：重置开发数据库
  backend/
    app/
      main.py                  ← FastAPI 入口，挂载路由和 WebSocket
      core/
        config.py              ← 配置（端口、DB路径、JWT密钥等）
        security.py            ← JWT 工具函数
        logging.py             ← 结构化日志（structlog 或 loguru）
      db/
        database.py            ← AsyncEngine 初始化，get_db 依赖
        models.py              ← SQLAlchemy ORM 模型（全表定义）
        crud.py                ← 各表 CRUD 函数
        migrations/            ← Alembic 迁移脚本
      api/
        deps.py                ← 通用依赖（current_user、get_db）
        routes/
          auth.py              ← POST /api/auth/login, /logout, /me
          status.py            ← GET  /api/status
          commands.py          ← POST /api/commands
          tests.py             ← GET  /api/tests, /api/tests/{id}
          alarms.py            ← GET/POST /api/alarms
          parameters.py        ← GET/PUT /api/parameters
          logs.py              ← GET  /api/logs/export
          reports.py           ← GET/POST /api/reports
          users.py             ← GET/POST/PUT /api/users  (Admin only)
          system.py            ← GET /api/system/info, /health
        websocket.py           ← WebSocket 端点 /ws/realtime
      hostcomm/
        client.py              ← asyncio TCP 长连接客户端
        protocol.py            ← 帧解析：JSON Lines + \n 分隔符
        dispatcher.py          ← 按 type 分发消息到各 handler
        heartbeat.py           ← 心跳任务（2s 周期，3次超时告警）
        reconnect.py           ← 自动重连任务（指数退避）
        mock_server.py         ← 本地 Mock TCP Server（无真实控制板时使用）
      services/
        command_service.py     ← 权限校验、状态校验、命令下发、结果记录
        logging_service.py     ← sample_point 写库、event_log 写库
        report_service.py      ← 报告生成逻辑
        cache.py               ← 最新状态快照内存缓存（asyncio.Lock 保护）
    tests/
      test_hostcomm.py
      test_commands.py
      test_api.py
    requirements.txt
    requirements-dev.txt
  frontend/
    src/
      main.js
      App.vue
      router/index.js
      stores/
        device.js              ← 设备实时状态 (Pinia)
        alarms.js              ← 报警列表 (Pinia)
        auth.js                ← 用户/角色 (Pinia)
        test.js                ← 当前试验 (Pinia)
      composables/
        useWebSocket.js        ← WebSocket 连接管理，自动重连
        useChart.js            ← Chart.js 封装
        useRole.js             ← 角色权限判断（可查看/可操作/可配置）
      pages/
        LoginPage.vue
        OverviewPage.vue       ← 实时总览
        TrendPage.vue          ← 趋势曲线
        TestPage.vue           ← 当前试验
        AlarmsPage.vue         ← 报警事件
        HistoryPage.vue        ← 历史试验
        ParametersPage.vue     ← 参数配置
        DiagnosticsPage.vue    ← 设备诊断
        SettingsPage.vue       ← 系统设置
        AnalyticsPage.vue      ← 数据分析
        ReportsPage.vue        ← 报告生成
        HelpPage.vue           ← 帮助
      components/
        layout/
          AppHeader.vue        ← 顶栏（状态、报警徽章、用户）
          AppSidebar.vue       ← 侧边导航
          AppFooter.vue        ← 通信状态栏
        charts/
          RealtimeChart.vue
          HistoryChart.vue
        alarms/
          AlarmBadge.vue
          AlarmTable.vue
        command/
          StartTestModal.vue   ← 含二次确认
          StopTestModal.vue    ← 含二次确认（CO 安全警告）
          TareModal.vue
        shared/
          StatusBadge.vue
          ConfirmDialog.vue    ← 通用二次确认组件
          RoleGate.vue         ← 按角色显示/隐藏
    package.json
    vite.config.js
  docs/                        ← 放本项目相关设计文档的软链接或副本
  .env.example                 ← 环境变量模板
  .gitignore
  README.md
```

---

## 4. 环境变量（`.env`，不得提交）

```
# 后端
SMD_HOST=0.0.0.0
SMD_PORT=8000
SMD_DB_PATH=./data/smd.db
SMD_JWT_SECRET=<随机生成，至少32字节>
SMD_JWT_EXPIRE_MINUTES=480

# HostComm
HOSTCOMM_HOST=192.168.1.100   # STM32 IP（实际联调时修改）
HOSTCOMM_PORT=34211
HOSTCOMM_HEARTBEAT_INTERVAL=2
HOSTCOMM_TIMEOUT_COUNT=3
HOSTCOMM_MOCK=false            # true = 使用 mock_server.py

# 前端（Vite 环境变量）
VITE_API_BASE=http://localhost:8000
VITE_WS_URL=ws://localhost:8000/ws/realtime
```

---

## 5. 编码规范

### Python（后端）
- Python 3.11+，全量 type hints
- async/await 贯穿 FastAPI 路由和数据库操作（SQLAlchemy async session）
- 行长 120 字符，Black 格式化，isort 导入排序
- 所有路由函数写 docstring（一行说明 + 权限要求）
- 错误统一用 `raise HTTPException(status_code=..., detail={...})`，detail 含 `error_code` 和 `message` 字段
- HostComm 相关代码：帧解析须容错（非法 JSON 只记录，不 crash），TCP 读写须有超时
- 数据库：只追加日志类表，不得 DELETE/UPDATE `sample_point`、`event_log`、`alarm_log`

### TypeScript/Vue（前端）
- Vue 3 Composition API（`<script setup>`）
- 组件名 PascalCase，文件名与组件名一致
- Pinia store 每个文件一个 store，`defineStore('name', () => { ... })` 风格
- 所有 API 调用封装在 `src/api/` 目录下，不在组件里直接 axios
- 角色权限用 `useRole()` composable 控制，不硬编码字符串比较
- CO/安全相关操作必须走 `ConfirmDialog.vue`，不得 `window.confirm()`

### 通用
- 注释语言：中文（与项目文档保持一致）
- 函数/变量命名：英文（蛇形命名 Python，驼峰命名 JS）
- 提交前运行 `pre-commit`（black + isort + ESLint）

---

## 6. Git 工作流

```
main          ← 稳定版，仅通过 PR/merge 更新，每个里程碑打 tag (v0.3-d3, v0.4-d4...)
dev           ← 日常开发分支，D3 阶段工作在此进行
feature/xxx   ← 功能分支，从 dev 创建，完成后 merge 回 dev
hotfix/xxx    ← 修复分支，从 main 创建
```

**提交信息格式（Conventional Commits）：**

```
<type>(<scope>): <中文简短说明>

type: feat | fix | refactor | test | docs | chore | style
scope: hostcomm | db | api | frontend | auth | alarm | report

示例：
feat(hostcomm): 实现 TCP 长连接客户端和帧解析
fix(alarm): 修复报警确认后徽章计数未清零问题
test(hostcomm): 补充断线重连测试用例
```

**何时 commit：**
- 每个功能模块完成且本地测试通过后立即 commit
- 不要积累大量未提交修改
- 每次 commit 前运行 `pytest backend/tests/` 确保无新增失败

**何时 push：**
- commit 后直接 push 到当前分支（`git push origin <branch>`）
- 不等待用户指令，完成即推

**首次设置（如远程仓库尚未配置）：**
```bash
git init
git remote add origin <用户提供的远程仓库 URL>
git checkout -b dev
# 完成初始提交后
git push -u origin dev
```

---

## 7. 测试要求

### 后端（pytest）
每个模块须有对应测试文件：

| 测试文件 | 覆盖范围 |
|---|---|
| `test_hostcomm.py` | 帧解析、心跳、重连、Mock Server 通信 |
| `test_commands.py` | 命令权限校验、状态限制、幂等去重 |
| `test_api.py` | FastAPI 路由（用 TestClient）|
| `test_safety.py` | 安全红线：确认不存在违禁接口 |

- D3 阶段目标：核心路径覆盖率 ≥ 80%
- `test_safety.py` 必须是 CI 中第一个运行的测试，用于验证安全红线

### 前端（Vitest）
- Store 逻辑单测
- ConfirmDialog、RoleGate 组件测试

---

## 8. 详细规格参考

完整技术规格（数据库 Schema、REST API 设计、WebSocket 协议、HostComm 状态机）见：

```
docs/上位机软件开发规格说明书.md
```

HostComm 协议（已冻结）见：

```
docs/GB_T34211_HostComm上位机通信协议开发需求说明.md
docs/熔滴炉上位机HostComm接口需求清单_V0.2_综合版.html
```

UI 交互原型见：

```
docs/熔滴炉Web上位机界面原型.html
```

---

## 9. D3 联调最低交付物（阶段门）

在进入 D4 冷态验收之前，以下必须完成并通过：

- [ ] HostComm TCP 客户端能与 Mock Server 完成 hello/心跳/状态快照/命令/事件推送全流程
- [ ] FastAPI 后端所有 P0 路由通过 TestClient 测试
- [ ] SQLite 所有表建立，sample_point 写入正常
- [ ] 前端实时总览页能通过 WebSocket 显示模拟状态数据
- [ ] start_test 和 stop_test 的二次确认 + 操作日志写库
- [ ] `test_safety.py` 全部通过
- [ ] `git log` 有合理的提交历史，已 push 到远程 `dev` 分支
