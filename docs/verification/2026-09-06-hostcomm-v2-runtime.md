# HostComm 2.0 上位机运行验证

适用候选版本：`0.3.0-rc.3`。设计依据：`2.0-design.1`；基线主线 `06d7573c6ebbb22c9a4ffd1201903ac90e9ab785`。本文维护可复现的软件验证入口；发布标签对应的 GitHub Actions 才是最终候选包的提交与产物证据。

## 已覆盖的行为

| 范围 | 可复现测试入口 | 证据内容 |
|---|---|---|
| 认证与连接 | `backend/tests/test_hostcomm_v2_security.py`、`test_hostcomm_v2_transport.py` | 真 TLS PSK、错误身份/密钥/证书替代拒绝、套件校验、心跳、半帧超时、慢回调、过载、请求关联 |
| 持久操作 | `backend/tests/test_v2_operations.py` | 写前意图、固定身份、完整 uint64 水位、重复/冲突、未知结果、重启查询与安全核查 |
| 配方 | `backend/tests/test_v2_recipe_compiler.py`、`test_v2_client.py` | 精确整数转换、旧摘要保留、含糊加热拒绝、工程范围、原子激活及逐字节回读 |
| 数据与报警 | `backend/tests/test_v2_source_logs.py`、`test_v2_archive.py` | 分块落盘后 ACK、摘要/缺口、补传去重、未知 run、原始边界、固定报警快照与历史事件不回退当前状态 |
| 取消与数据库清理 | `backend/tests/test_v2_cancellation.py` | 在真实 SQLite 游标执行后注入重复取消；事务和游标释放后传播取消，后续独立写入成功，网络命令不受取消保护 |
| 刷新与日志确认隔离 | `backend/tests/test_v2_refresh.py`、`test_v2_application.py` | 可选状态/界面刷新由单个合并任务执行；刷新停顿不阻塞日志块落盘及 ACK，关闭/重连排空任务，刷新失败撤销控制就绪 |
| 完整业务 | `backend/tests/test_v2_application.py` | REST → 生产回调 → 真实 TCP 模拟器 → SQLite；启动、停止后冷却、结束确认、丢回执查询不重发、满读槽和普通命令超时不阻塞停止、全局报警 |
| 国标指标 | `backend/tests/test_v2_standard_metrics.py` | 源序排序、H600、首滴原事件、空设备 UTC、无滴落证据、源位置未知到补传完整、坏质量与缺口 |
| 无执行器板端模型 | `backend/tests/test_hostcomm_v2_simulator.py` | 全部协议类型、租约、配方、状态、日志、故障注入、持久重启；不构成固件验收 |
| 桌面配对与迁移 | `desktop/tests/test_pairing.py`、`backend/tests/test_v2_operations.py` | 受控配对、身份/密钥不公开、恢复日志、旧库迁移；真实 Windows 行为以 CI 和现场记录分别确认 |
| 实验后的离线维护 | `backend/tests/test_v2_application.py`、`desktop/tests/test_pairing.py` | 真实 REST/TCP 及归档数据在安全完成后可通过离线配对检查；确证未开始的拒绝启动不补造安全时间；错配终态、未知结果和已观测运行证据继续阻止配对 |
| 拒绝与中断恢复 | `backend/tests/test_v2_operation_api.py`、`test_v2_upgrade_ready.py` | 启动拒绝证据关闭预约；interrupted 不提前结束采集；未配对安装可维护，已配对未确认终态/未知命令阻止升级 |

## 浏览器集成验证

2026-09-06 在 macOS Chromium，以独立临时 SQLite、生产 FastAPI 回调和真实回环 TCP 模拟器验证；前端为 Node 24 生成的 rc.3 产物。模拟器没有执行器，温度和阶段通过测试控制接口推进。

- 首次登录强制改密；工程配置只读；标准配方保存、校验、原子启用；编辑产生独立非标版本并重新启用。
- 设备恢复期间启动不可用；启动后进入测定；自然结束和主动停止后继续置换、冷却；安全完成后独立确认结束回到待机。
- L3 报警显示、清除实时更新、填写依据后显式故障复位；操作记录显示命令/时间并可查询板端结果。
- 复现并修复已消除但未确认的历史报警卡住实验结束的问题：结束前提示确认报警，历史页按权限显式确认，再允许 ack_run。没有隐式确认或解除活跃联锁。
- 手动源日志补传作为后台任务完成；起始序号校验有效。最后一次历史报警完整路径的浏览器 console 与 API 错误均为空。

浏览器证据与最终候选提交、CI、资产清单一同归档；这组验证不包含 WebView2 桌面窗口、干净断网 Windows 或真实控制板。

## 本地重现

在 Python 3.13 环境安装哈希锁依赖，从 `backend/` 执行：

```sh
python -m pytest -q --cov=app --cov-report=term --cov-fail-under=80
```

从仓库根执行桌面检查、文档和跨语言契约检查：

```sh
python -m pytest -c backend/pytest.ini desktop/tests -q
python scripts/check_docs.py
python scripts/check_hostcomm_contract.py
node contracts/hostcomm/v2/verify-js.mjs
```

前端使用 Node 24 执行 `npm run api:schema`、`types:check`、`typecheck`、`lint`、`test` 和 `build`。TLS 测试只在独立子进程中设置 OpenSSL 测试配置，不修改应用或整套 CI 的 TLS 配置。

停止独立性测试分别检查本地事务完成与网络回执：停止报文在普通命令或四个读取仍等待时发出并收到回执，网络阶段限制为 1 秒；完整 HTTP 测试预算包含确认和数据库耐久提交。该测试预算不是设备的端到端安全停机时限，板端处置和硬接线切断仍由设备验收证明。

Windows 密钥验证同时检查实际文件所有者和 DACL。Python 3.13 私有目录中的 OWNER RIGHTS 仅解析为已经验证的受信所有者，不能作为宽授权豁免；真实 Windows ACL 测试继续要求拒绝 Everyone 读取。配对后的服务身份读取与候选包安装检查一同保存证据，不等同于真实控制板 TLS 握手通过。

## 仍未验收

固件尚未开发，没有真实控制板联调结果。未取得干净断网 Win10/11、真实设备批准采样率 24h、实物联锁/失电、国标争议判定规则批准及完整实验签字。候选包只能标记 RC；正式发布门禁继续有效。[任务清单](../../tasks/todo.md)保留这些退出条件。
