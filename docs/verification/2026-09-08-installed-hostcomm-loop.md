# Windows安装版与HostComm实验闭环验证

实施基线：`89e5ec1ed18c7232ad07a222b2f1c49059bcdcec`，上一已发布候选rc.4。
本轮开发候选：`0.3.0-rc.5`；文档修订：`2.0-doc.3`；线协议：`2.0 / 2.0-design.1`。

## 状态

首个集成提交为 `c6c2a0697c07b378011e3929e4d5aaf3a4af5c56`，见[PR #75](https://github.com/kevinalliswell/smd-web-hmi/pull/75)。最终提交、CI及发布资产仍以本轮实际结果登记，不能据此认定Windows或真机验收通过。

| 层次 | 验证内容 | 当前证据 |
|---|---|---|
| 本机软件 | 心跳/租约/重连，未知运行审查与回放，报告空值/完整性门禁，工具归属与发布门禁 | `e27c32ef7e27b20f612e5705bb26b4c5b400512a`在Python3.13.12/macOS后端759通过、2项Windows限定跳过，覆盖率86.88%；Node24前端123通过，类型/lint/build通过；发行门禁补字节绑定反例后8通过 |
| 模拟器 | 真实TLS、合成阶段、故障注入、日志/报警关联和实际导出 | macOS源码后台与Chromium通过15项业务断言，下载并检查10份实际报告；未替代Windows冻结程序验收 |
| Windows CI | 实际NSIS/LocalService；旧安装冒烟；独立工具重新安装并执行TLS/页面/报告 | 第四轮实际安装/升级拒绝/备份重装/配对读取通过，工具冻结通过；工具启动失败，完整实验尚未执行，不允许发包 |
| Win10/11桌面 | WebView2、关闭窗口持续采集、中文路径、显示缩放、下载及重开 | 未验收，单列人工复验 |
| 固件/真机 | STM32H750、TLS栈、外设/联锁、工艺时长、完整实验与24小时 | 未验收；固件尚待开发 |

CI发现并复现了两项问题：[首轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34144808314)的报告测试使用系统默认编码读取UTF-8文件，已显式指定编码；[第二轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34145624716)在Windows及Python3.11发现同版本心跳先于状态查询归档时，已有有效租约被误判为未确认。后者通过模型、真实TCP与HTTP报警流程稳定复现；修复只复用仍有效且身份、状态版本匹配的已确认租约，不修改其截止时间。修后106项相关测试通过。以上失败均阻止了安装版打包步骤，需要后续完整CI通过才可发布。

[第三轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34146529304)的Linux两组、前端、规范和审计通过；Windows新增诊断测试在私有目录ACL设置时失败，安装包步骤仍未执行。该路径调用系统PowerShell 5，需与安装器共用隔离PowerShell 7继承模块的执行器；对应Windows测试继续作为门禁，不能以本机通过替代。

[第四轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34147661742)的实际PR合并快照为`63f408f59e77102829490af324ca82255488addb`（分支提交`ef5e31dc5b57f2ec216bc3f9dbda5e5eba6c0fe1`）。Windows Server 2025/Python3.13.15后端757通过、4项平台限定跳过，覆盖率87.69%；桌面185通过、2项跳过，工具35通过。安装器SHA256为`d4ca75aaeeadb5da00e02ff70ad4a493423b79a563048d4fd95bb293bc47a30f`，实际安装与既有smoke全部通过，包含旧资料备份及空库重装。独立工具冻结后在创建Job Object时触发TypeError，尚未安装其自身测试实例；失败记录的`cleanup_complete=true`。该构建不是合格联调发行包。

[第五轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34150511408)在打包前执行新增真实Windows Job测试，发现工具独立依赖清单未声明`pywin32`，四项测试在导入阶段失败。现将已由桌面锁固定的312版本及其哈希显式加入工具锁，保留Windows环境标记；不跳过平台测试。Job命名、正常退出与父死子清的最终结果仍需后续Windows执行确认。

## 测试方法与证据限制

- 模拟器拥有独立SQLite和受限配对资料，通过真实TLS与安装后的后台连接；页面操作不修改后台内部状态或数据库。
- 驱动以私有JSONL动作注入阶段和故障，不加速网络时钟。报告中的模拟数据保留`not_certified`。
- 数据预期使用固定人工核算样本，不用被测指标计算器生成预期。
- `acceptance.json`绑定版本、SHA、场景、断言和清理结果。发布门禁要求全部固定场景通过，不接受跳过。
- 公开证据仅包含白名单摘要、必要截图和合成实验导出，不包含真实配置、数据库、PSK、口令或会话凭据。
- CI先完成原安装冒烟及其清理，再运行工具自己的完整安装流程。两次安装增加执行时间，避免工具接管另一流程的对象。
- 生产程序先冻结，随后才安装独立工具依赖；工具冻结后的进程/浏览器自检前置于较长的安装冒烟，尽早发现冻结环境问题，完整业务流程仍在两者之后执行。

实施依据：[ADR-010](../decisions/ADR-010-installed-hostcomm-loop.md)、[任务清单](../../tasks/todo.md)。

## 故障覆盖层次

| 验证层次 | 用例与证据入口 |
|---|---|
| 传输/业务软件回归 | [租约代次](../../backend/tests/test_v2_lease_context.py)、[时限与单帧非法ACK](../../backend/tests/test_v2_control_timing.py)、[写锁与释放竞态](../../backend/tests/test_v2_control_runtime.py)、[真实TLS上传中断](../../backend/tests/test_v2_tls_application.py) |
| 恢复与数据软件回归 | [审查冲突/去重/工作进程恢复](../../backend/tests/test_v2_run_recovery.py)、[旧库迁移](../../backend/tests/test_run_recovery_migration.py)、[报告生成期版本漂移](../../backend/tests/test_recovery_reports.py)、[当前运行与停止归属](../../backend/tests/test_v2_command_lifecycle.py) |
| 安装版实际场景 | [SmdBench正常流程](../../tools/bench/smd_bench/scenarios.py)和[故障流程](../../tools/bench/smd_bench/faults.py)；结果须读取对应构建acceptance.json，软件单元测试不冒充安装后的场景执行 |

公开安装版证据还记录协议/design/场景版本、实际安装器SHA256及工具清单SHA256。相同源码提交的不同冻结字节不能共用验收结论。
