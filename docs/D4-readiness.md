# D4 冷态验收就绪报告 · smd-web-hmi

> 版本：v1　日期：2026-07-06　分支：`claude/awesome-mayer-3sifla`
> 适用阶段：D3 接口联调 → **D4 冷态验收**
> 依据：`CLAUDE.md` 第 2 节（安全红线）、第 9 节（D3 阶段门）；
> `docs/上位机软件开发规格说明书.md` 第 9 章；HostComm 协议 §15 验收测试。

---

## 1. 结论

**D3 阶段门全部满足，软件侧具备进入 D4 冷态验收的条件。**
全栈已在工控机（Windows / zh-CN）离线跑通（Mock 模式），自动化测试全绿，安全红线核验通过。
接入真实 STM32 控制板前尚需完成第 4 节的现场/硬件待办项。

---

## 2. 测试结果（本分支实测）

| 套件 | 命令 | 结果 |
|---|---|---|
| 后端 pytest（全量） | `pytest -q` | ✅ **61 passed** |
| └ 安全红线 `test_safety.py` | `pytest tests/test_safety.py -v` | ✅ **16 passed** |
| 前端 vitest | `npm run test` | ✅ **8 文件 / 29 passed** |
| 数据库迁移 | `alembic upgrade head` | ✅ 建 10 表（9 业务表 + `alembic_version`）|

### 安全红线（T11 等，16 项全过）
以下违禁接口经代码检索确认**不存在**：
`force_co`、`force_open_co`、`force_co_valve`、`bypass_safety`、`bypass_relay`、
`bypass_interlock`、`force_heating`、`force_heat_permit`、`force_resume_heat`、
`write_do_bitmap`、`set_do_bit`、`override_safety`、`force_scr`。
另核验：CO 命令（start_test/stop_test）强制二次确认；Observer 角色命令被拒；
HostComm 调试工具仅只读。

### 前端单测覆盖
crc32、工艺阶段常量、device / auth / alarms store、AlarmBadge / ConfirmDialog / RoleGate 组件。

---

## 3. D3 阶段门核对（CLAUDE.md §9）

| 阶段门项 | 状态 | 证据 |
|---|---|---|
| HostComm 客户端 ↔ Mock 全流程（hello/心跳/快照/命令/事件）| ✅ | 运行实测 + `test_hostcomm.py` |
| FastAPI P0 路由 TestClient 通过 | ✅ | `test_api.py`（含于 61）|
| SQLite 建表 + sample_point 落库 | ✅ | `alembic upgrade head` / 采样服务 |
| 前端实时总览经 WebSocket 显示 | ✅ | 浏览器冒烟：KPI 随 WS 刷新 |
| start_test / stop_test 二次确认 + 操作日志写库 | ✅ | `test_commands.py`；ack 流程浏览器实测 |
| `test_safety.py` 全通过 | ✅ | 16 passed |
| git 历史合理并已 push 到远程 | ✅ | 远程分支 20 提交 |

---

## 4. D4 冷态验收准备清单

### 4.1 软件侧（已就绪 ✅）
- [x] 全栈离线可运行（后端 + 前端 + Mock），Windows 工控机实测
- [x] 自动化测试全绿：后端 61 / 前端 29 / 安全红线 16
- [x] DB 迁移、建表、默认 admin 初始化正常
- [x] WebSocket 实时推送、报警链路（事件→落库→推送→确认）实测
- [x] 命令权限矩阵、状态限制、CRC 校验、operator_action 审计

### 4.2 接入真实控制板前（现场 / 硬件待办 🟡）
- [ ] **切换真实 STM32**：`backend/.env` 设 `HOSTCOMM_MOCK=false`，
      `HOSTCOMM_HOST` 改为控制板实际 IP（默认模板 `192.168.1.100:34211`）。
- [ ] **数据字典对齐**：以供应商交付的实时状态字段/单位/有效性标志表，
      逐项核对 `status_snapshot` 解析与前端 KPI（协议 §7、§14 交付物）。
- [ ] **按协议 §15 验收矩阵**逐条执行：
      - 连通性/会话：TCP 连接、心跳 10min 稳定、断线重连、版本不匹配
      - 数据/命令：状态快照、start_test 受理、拒绝命令带原因码、参数下发回读一致、去皮
      - 日志/异常：日志导出分块、错误 JSON、超长帧、重复 msg_id
      - 安全边界：禁止强制 CO、禁止绕过联锁、禁止直接 SCR、上位机断线本地流程不受影响
- [ ] **JWT 密钥**：生产 `.env` 填入 ≥32 字节强随机 `SMD_JWT_SECRET`
      （当前未配置时为进程内临时生成，重启即失效）。
- [ ] **默认口令**：用 `admin/admin` 首次登录时确认系统只允许进入改密页；改密后须重新登录。
- [ ] **数据留存**：确认 `SMD_DB_PATH` 指向持久化盘位；
      `sample_point` / `event_log` / `alarm_log` 只追加，验收期数据不得清库。

---

## 5. 复现实测的运行方式（Windows / zh-CN）

```bash
# 后端（backend/）
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements-dev.txt
copy ..\.env.example .env      # 开发置 HOSTCOMM_MOCK=true
alembic upgrade head           # 或首次启动由 create_all 自动建表
python -m app.hostcomm.mock_server          # 另开终端：Mock（--demo-alarms 可注入演示报警）
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 前端（frontend/）
npm install
npm run dev                    # http://localhost:5173，默认 admin/admin（首次登录强制改密）

# 测试
pytest -q                      # 后端
npm run test                   # 前端
```

### 环境注意（已知坑）
- 本机全局 `~/.npmrc` 含 `os=linux`：Windows 本地构建须
  `npm install --os=win32 --cpu=x64`，否则缺 esbuild/rollup 原生包。
- `alembic.ini` 必须纯 ASCII：configparser 以系统 locale（GBK）读取，
  中文注释会触发 `UnicodeDecodeError`（已于 commit `da16b30` 修复）。

---

## 6. 遗留与后续

- 分支现状与 CLAUDE.md 约定不一致：权威实现在 `claude/awesome-mayer-3sifla`，
  `origin/dev` 为陈旧单提交、`main` 不存在。建议在 D4 前规整为 `main`/`dev` 流程。
- 建议将测试纳入 CI（GitHub Actions），每次提交自动跑安全红线 + P0。
