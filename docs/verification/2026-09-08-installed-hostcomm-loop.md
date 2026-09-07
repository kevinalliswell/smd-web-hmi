# Windows安装版与HostComm实验闭环验证

实施基线：`89e5ec1ed18c7232ad07a222b2f1c49059bcdcec`，上一已发布候选rc.4。
本轮开发候选：`0.3.0-rc.5`；文档修订：`2.0-doc.3`；线协议：`2.0 / 2.0-design.1`。

## 状态

首个集成提交为 `c6c2a0697c07b378011e3929e4d5aaf3a4af5c56`，见[PR #75](https://github.com/kevinalliswell/smd-web-hmi/pull/75)。[首轮CI](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34144808314)执行中；最终提交、CI及发布资产仍以本轮实际结果登记，不能据此认定Windows或真机验收通过。

| 层次 | 验证内容 | 当前证据 |
|---|---|---|
| 本机软件 | 心跳/租约/重连，未知运行审查与回放，报告空值/完整性门禁，工具归属与发布门禁 | Python3.13.12/macOS后端743通过、2项Windows限定跳过，覆盖率86.80%；Node24前端123通过，类型/lint/build通过；发行门禁补字节绑定反例后8通过 |
| 模拟器 | 真实TLS、合成阶段、故障注入、日志/报警关联和实际导出 | 场景集成中 |
| Windows CI | 实际NSIS/LocalService；旧安装冒烟；独立工具重新安装并执行TLS/页面/报告 | 待执行；由同一checks工作流严格阻止失败发包 |
| Win10/11桌面 | WebView2、关闭窗口持续采集、中文路径、显示缩放、下载及重开 | 未验收，单列人工复验 |
| 固件/真机 | STM32H750、TLS栈、外设/联锁、工艺时长、完整实验与24小时 | 未验收；固件尚待开发 |

## 测试方法与证据限制

- 模拟器拥有独立SQLite和受限配对资料，通过真实TLS与安装后的后台连接；页面操作不修改后台内部状态或数据库。
- 驱动以私有JSONL动作注入阶段和故障，不加速网络时钟。报告中的模拟数据保留`not_certified`。
- 数据预期使用固定人工核算样本，不用被测指标计算器生成预期。
- `acceptance.json`绑定版本、SHA、场景、断言和清理结果。发布门禁要求全部固定场景通过，不接受跳过。
- 公开证据仅包含白名单摘要、必要截图和合成实验导出，不包含真实配置、数据库、PSK、口令或会话凭据。
- CI先完成原安装冒烟及其清理，再运行工具自己的完整安装流程。两次安装增加执行时间，避免工具接管另一流程的对象。

实施依据：[ADR-010](../decisions/ADR-010-installed-hostcomm-loop.md)、[任务清单](../../tasks/todo.md)。

## 故障覆盖层次

| 验证层次 | 用例与证据入口 |
|---|---|
| 传输/业务软件回归 | [租约代次](../../backend/tests/test_v2_lease_context.py)、[时限与单帧非法ACK](../../backend/tests/test_v2_control_timing.py)、[写锁与释放竞态](../../backend/tests/test_v2_control_runtime.py)、[真实TLS上传中断](../../backend/tests/test_v2_tls_application.py) |
| 恢复与数据软件回归 | [审查冲突/去重/工作进程恢复](../../backend/tests/test_v2_run_recovery.py)、[旧库迁移](../../backend/tests/test_run_recovery_migration.py)、[报告生成期版本漂移](../../backend/tests/test_recovery_reports.py)、[当前运行与停止归属](../../backend/tests/test_v2_command_lifecycle.py) |
| 安装版实际场景 | [SmdBench正常流程](../../tools/bench/smd_bench/scenarios.py)和[故障流程](../../tools/bench/smd_bench/faults.py)；结果须读取对应构建acceptance.json，软件单元测试不冒充安装后的场景执行 |

公开安装版证据还记录协议/design/场景版本、实际安装器SHA256及工具清单SHA256。相同源码提交的不同冻结字节不能共用验收结论。
