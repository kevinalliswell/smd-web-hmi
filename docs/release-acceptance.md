# 软件正式版发布证据契约

维护角色：发布/Windows 测试负责人。契约版本：`1`，2026-09-13。范围依据 [ADR-011](decisions/ADR-011-overwrite-install-and-software-release.md)。本文定义门禁输入，**不是已完成的验收记录**。

## 执行来源

标签流水线在必要检查通过后下载本次 Windows job 的实际资产。Windows 测试脚本只能在真实安装器、服务和页面断言通过后写入结果；失败或跳过不得转成 passed。发布门禁再次读取实际文件并校验 SHA-256，不接受清单里的自由文本、布尔标记或另一构建的通过结果。

Windows CI 用 [assemble_acceptance.py](../scripts/release/assemble_acceptance.py) 聚合安装器七项原始报告与 SmdBench 两项维护报告：

```text
python scripts/release/assemble_acceptance.py --artifacts artifacts --overwrite-evidence artifacts/overwrite-evidence --bench-evidence artifacts/bench-evidence
```

聚合器校验本次提交、CI 编号、实际日志摘要与清理结果，只复制原始通过断言和九份独立脱敏日志。随后验证正式发布资格，生成两份汇总并向 SHA256SUMS 追加摘要；相同输入重跑不重复条目，不替换已有不同内容。任一子套件失败、缺场景或清理未完成则拒绝，不按场景名补造 passed。

```text
python scripts/release/publish_gate.py --manifest artifacts/manifest.json --tag v0.3.0 --artifacts-dir artifacts --software-acceptance artifacts/software-acceptance.json
```

RC 继续支持只提供 `--manifest` 和 `--tag` 的已有入口；RC 流水线保留安装冒烟与完整 SmdBench，不运行本文新增的七项旧版覆盖安装套件及正式版聚合器。RC 必须符合 `X.Y.Z-rc.N` 且 prerelease 为 JSON true。软件正式版必须是 `X.Y.Z` 且 prerelease 为 JSON false；缺少机器证据则失败。标签/应用/前端/包清单版本和提交完全一致。

## 文件与字段

结构定义：[软件汇总 Schema](schemas/software-acceptance-v1.schema.json)、[Windows 场景 Schema](schemas/windows-acceptance-v1.schema.json)。Schema 检查结构；实际文件、摘要、ZIP 清单和本次 CI 身份仍由发布门禁执行。

`software-acceptance.json` 是发布汇总，字段如下：

| 字段 | 约束 |
|---|---|
| schema_version | 整数 1 |
| release_scope | 固定 `software-stable` |
| version / commit | 与标签版本、40 位小写提交 SHA 一致 |
| ci_run_id | 当前标签工作流 GITHUB_RUN_ID 的十进制字符串 |
| artifacts | installer、bench、bench_manifest、bench_acceptance、windows_acceptance 五个文件引用 |
| limitations | 必须包含下面四个未验收标识，不得写成通过 |

每个文件引用是 `{ "path": "简单文件名", "sha256": "64位小写摘要" }`。必须是资产目录内的非空普通文件；拒绝路径穿越、绝对路径、软链接和目录联接。安装器、工具 ZIP 与三份清单/证据使用固定文件名。工具 ZIP 内实际清单必须与外置 bench-manifest 一致，逐文件字节必须符合该清单。

未验收标识固定为 `win10_win11_desktop_unverified`、`firmware_unverified`、`physical_interlocks_unverified`、`gb_conformance_unverified`。它们分别表示：真实 Win10/11 离线桌面与现场环境、目标固件、安全联锁、国标符合性尚未验收。软件版本号与这些资格独立。

`windows-acceptance.json` 必须包含整数 schema_version=1、相同 version/commit/ci_run_id、runner_os=`Windows`、execution=`actual-installed-service`、实际 installer_sha256、整体 status=`passed`、cleanup_complete=JSON true，以及 assertions 列表。任何子套件失败或清理未完成，汇总都不能写为通过。每项为唯一 name、status=`passed` 和 evidence 文件引用。每个引用绑定同目录内本次产生的非空脱敏日志及其摘要。

| 必要场景 name | 实际行为与证据 |
|---|---|
| fresh_install | 无旧实例的安装、受限服务身份及就绪 |
| upgrade_rc4 | 发布的 rc.4 覆盖升级，原账户/配置/密钥及API保存的配方仍可用 |
| upgrade_rc5 | 发布的 rc.5 覆盖升级，原账户/配置/密钥及API保存的配方仍可用 |
| same_version_repair | 同发行清单修复受损程序且保留数据；不同构建拒绝另由引擎回归验证 |
| downgrade_rejected | 实际旧rc.5安装器以20及本次MaintenanceRequired日志拒绝覆盖；新版本排序策略另由引擎回归验证 |
| offline_confirmation | 未知/离线状态按管理员现场停机确认继续，未确认拒绝 |
| busy_rejected | 已知运行/冷却状态拒绝维护，不自动结束实验 |
| rollback_recovery | 有真实备份的旧失败事务恢复后更新；备份后API新增记录保留在额外快照，原业务恢复到当前库；仅待验活续跑另由引擎回归验证 |
| custom_database_preserved | 实际自定义数据库路径与配置保留 |

安装专用脚本[windows-overwrite-acceptance.ps1](../scripts/release/windows-overwrite-acceptance.ps1)通过实际API改密、创建用户及保存配方，构造既有业务资料；工程夹具只移动已停服的实际数据库或注入有真实备份的恢复日志，不直接添加实验采样。旧rc升级的完整实验数据保留尚未由该套件覆盖；SmdBench在真实模拟实验之后验证同版修复的数据保留，两种证据不混写。

聚合后的发布目录包含七个按场景名命名的 `.log` JSON 日志，以及 `installer-busy-rejected.json`、`installer-offline-confirmation.json`；这九个文件与两份正式版汇总同时发布并加入 SHA256SUMS。RC 不生成这组根目录文件，发布脚本不得把未匹配的文件通配符当作资产路径。

独立 SmdBench 使用既有 bench-acceptance schema：完整场景、唯一通过断言、cleanup_complete=true、匹配安装器及工具清单摘要。门禁复用[联调工具验收校验](../scripts/release/package_bench.py)，不会把工具清单存在本身当成联调通过。

## 可信边界与归档

JSON、日志和摘要不是数字签名。可信执行来源是受保护仓库上同一次 tag 工作流、已审核的测试脚本和必要检查；拥有工作流写权限的人仍必须接受代码审查。门禁用于阻止资产混用、漏场景、失败结果、空文件和旧 CI 证据复用，不能证明伪造测试代码的真实性。正式设备资格继续按 [M5 任务](../tasks/todo.md)留证，不通过修改说明文字解锁。

公开证据白名单只包含版本、提交、环境、固定断言和脱敏日志；不上传密码、PSK、JWT、service.env、完整数据库或私人路径。证据与包同时归档，SHA256SUMS 包含这些文件。遇到失败保留本轮证据和恢复说明，不删除其他运行创建的资源。
