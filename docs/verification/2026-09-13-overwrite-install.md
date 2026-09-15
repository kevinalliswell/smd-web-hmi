# 2026-09-13 覆盖安装与软件正式版验证

维护角色：集成、Windows测试与发布负责人。关联任务OVER-01—07，范围依据[ADR-011](../decisions/ADR-011-overwrite-install-and-software-release.md)。[v0.3.0](https://github.com/kevinalliswell/smd-web-hmi/releases/tag/v0.3.0)已于 **2026-09-15 09:49:48 UTC** 发布，非草稿、非预发布；发布提交为 `404ecb50edf23864ed482e68cc4a1933deb3383f`，协议保持 `2.0 / 2.0-design.1`、文档修订 `2.0-doc.3`。主线与标签的实际验收均通过，21项实际发行资产的本地下载、完整门禁及独立集合核验均通过。历史失败、预算限制和各构建身份按下文保留，不以软件发布替代现场验收。

本机执行基线为 `0701a136d73560221e7d32dc4cf2747b3397ab86` 加测试当时尚未提交的实现和测试；这些结果不能归到未包含改动的基线提交。随后实现分别整理在维护协调 `ec363a8bb1b81865071ab87fb564f0b5d09253cd`、覆盖安装与恢复 `5cd9c9a401a9fa714b0a01bf531a6c45ff00bf23`、实际安装验收及门禁 `5ee5b27019b3a3ce2c9056bb7142778db44b5ea4`。最终集成、合并及标签构建后应追加实际构建完整 SHA、CI 编号、产物摘要与日志链接，不覆盖早期执行记录。rc.5 的[历史安装版联调](2026-09-08-installed-hostcomm-loop.md)不为本轮不同字节背书。

## 本机软件检查

以下为本轮集成者在 macOS / Python 3.13、Node 24 开发环境记录的结果。环境限定的跳过项保持跳过，不折算成通过；它们仍须在适用平台执行。

| 检查 | 已观察结果 | 适用范围与限制 |
|---|---|---|
| 桌面与安装事务单元回归 | 335 通过、27 环境限定跳过 | 本机 Python 测试；没有在此执行实际 Windows Service 或 NSIS 安装 |
| SmdBench 单元回归 | 82 通过、16 环境限定跳过 | 工具控制、所有权与证据行为；不等于冻结 Windows 工具完成实验 |
| 后端全量回归（较早工作区状态） | 814 通过、3 跳过，覆盖率 86.71% | 后续业务维护门禁仍有修改，必须重新执行后才能代表最终实现 |
| 前端 | 125 项测试通过；类型检查、lint、生产构建通过 | 维护页面和类型/构建检查；不替代 Windows WebView2 人工运行 |
| NSIS 3.12 本机语法夹具 | 编译语法检查通过 | 使用夹具载荷，仅验证脚本语法；CI 使用锁定 NSIS 3.11 和真实 Windows 载荷重新构建 |

复验入口为桌面 `python -m pytest -c backend/pytest.ini desktop/tests`、工具 `python -m pytest tools/bench/tests`，以及前端 `npm run test`、`npm run typecheck`、`npm run types:check`、`npm run lint`、`npm run build`。后端最终全量结果及业务门禁变更后的状态由集成者在后续执行完成后追加。命令入口不是新增的一次执行记录。

安装验收与发布门禁专项另在本机执行 84 项回归通过，覆盖实际文件摘要、不同 CI 身份、缺失/失败场景、RC 兼容、旧安装器特定拒绝日志和恢复快照差异等反例；相关 Python 文件的 Black/isort 及文档链接检查通过。它们检验自动化的判断逻辑，不产生 `windows-acceptance.json` 的真实执行证明。

### 提交前最终回归

业务维护门禁与忙碌初查补充已整理在 `4f374c24b82341618d427f4c35830f49167a754b`。提交前对该代码内容执行后端全量：**836 通过、3 项 Windows 专项跳过，覆盖率 86.89%**，保留 80% 门槛。桌面补充 SCM 陈旧 PID 回归后全量 **344 通过、27 项环境限定跳过**。全部必要 Python 目录 Black/isort、文档链接与 diff 检查通过。此前前端及联调工具结果对应的代码未再修改；这些本机结果仍不替代下面的实际 Windows 流水线。

独立交叉审查修正了迁移子进程重复申请后台锁、STOPPED 状态的无效 PID 判断、重装开始验活期间的控制门禁及自动启动断电窗口。维护中的业务写入统一暂停，已受理请求、报告任务与单批恢复回放先排空；已知忙碌提前拒绝维护，授权前仍在两个命令锁内重新核验。健康检查和只读访问保持可用。

## 本轮 Windows CI 与预算阻塞

PR [#79](https://github.com/kevinalliswell/smd-web-hmi/pull/79) 的首轮 [CI 34762470227](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34762470227) 对应 `be14acebf6e371ce383a4e7ad71660821452018d`：规范、前端、Linux Python 3.11/3.13 后端检查通过；审计和 Windows 桌面单元检查失败，实际安装打包尚未执行，不能算 Windows 验收完成。

- 审计发现 `js-yaml` 的 [GHSA-2883-xcg3-v3hh](https://github.com/nodeca/js-yaml/security/advisories/GHSA-2883-xcg3-v3hh)。在 `69f0818` 只更新两个传递依赖：`@redocly/openapi-core 1.34.20`、`js-yaml 4.3.2`。Node 24 本机审计变为 0 漏洞，125 项前端测试及类型、lint、构建通过，Vite 不变。
- Windows 原生测试发现暂存 ACL 使用了 pywin32 未提供的 `AddAce`；`f9e0d64` 改用其支持的允许/拒绝 ACE 方法，保留原权限意图。其余失败涉及测试夹具没有明确 UTF-8、把 Windows 反斜杠路径直接放进双引号 dotenv 值；夹具已修正，未放宽实际数据库路径校验。修正后的本机桌面回归仍为 344 通过、27 环境限定跳过；另在含字面反斜杠路径的临时目录执行重点 115 项通过，实际 Windows 结果待下一轮 CI。

第二轮 [CI 34762955145](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34762955145) 对应 `7deff07749490fedb46a6f4390a2d7be67ba6159`：审计、规范、前端和两个 Linux 后端通过；Windows 桌面 **371 通过**、联调工具 **98 通过**，确认上述修正。Windows 后端在原生请求权限夹具失败，实际安装包步骤仍未开始。夹具目录的管理员/SYSTEM ACE 缺少生产目录采用的 OI/CI 继承标志，设置子文件权限时被拒绝；修正应保持普通用户写入与父目录替换请求的拒绝断言，不修改生产许可规则。该轮覆盖率因 `--maxfail=1` 提前退出而未满足门槛，不记为后端全量通过。

第三轮 [CI 34763396781](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34763396781) 对应 `6de2960bd7d452cd0312e8c70c8db9d4c1641825`：上述目录继承修正通过，原生测试已成功验证只读许可和普通用户写文件拒绝；后续父目录删除子文件断言失败。夹具中的 SDDL `DC` 实际表示目录服务对象的 `0x2` 权限，不是文件目录的 `FILE_DELETE_CHILD (0x40)`。需使用明确的文件系统权限值复验；生产校验已检查 `0x40`，不能通过移除该断言放行。该轮 Windows 后端仍为提前失败，实际安装器未执行。

实际安装流程的交叉检查另发现两个旧脚本契约需要同步：新版已停用的准备接口应返回 HTTP 410 且不生成票据；旧测试机重置工具应精确接受新恢复任务增加的 `--non-interactive` 参数。已保留新旧两种精确任务格式和全部归属、备份、空库重装断言；新增原生 PowerShell 行为例须在 Windows 执行。本机实际 ASGI 调用验证了 410 与无票据行为。安装冒烟失败证据另补充白名单脚本名与行号，仍不公开异常正文、配置或口令。

第四轮 [CI 34763952033](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34763952033) 对应分支提交 `bf99bd5a79a33c20b489599a03d0e65afec7aded`，六项基础检查全部通过。Windows 后端 **835 通过、4 项 POSIX 限定跳过**，覆盖率日志显示 **88%**；Windows 桌面 **371 通过**、联调工具 **98 通过**。随后推送安装冒烟脚本修正触发并发规则取消旧运行，`windows-package` 在冻结程序期间被取消，**尚未执行实际安装**。单元检查通过不能替代实际安装、覆盖升级和安装版联调。

第五轮 [CI 34764506342](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34764506342) 对应分支提交 `672ba3f27c77cc113aef9544aeb0862b6dea3425`，六项基础检查均未启动。GitHub annotation 原文为：

> The job was not started because an Actions budget is preventing further use.

代码及 Windows 验收场景已提交并推送。集成者对当前 `672ba3f27c77cc113aef9544aeb0862b6dea3425` 再次执行全范围 Black/isort 通过；测试机重置工具的本机可运行检查通过，原生 Windows PowerShell 5.1 用例仍因环境跳过，不能当作本轮 Windows 执行证据。

该轮因 GitHub Actions 可用预算外部阻塞而未执行测试，不能记为测试失败或通过。此历史记录保留；不能通过跳过检查、降低门槛或复用旧构建证据发布。

2026-09-14 用户确认预算已恢复。分支提交 `5ebcb44e0d3b3a87bc32fc586d29798a13a7712b` 的 [CI 34764847328 第 2 次尝试](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34764847328/attempts/2) 实际启动且审计检查通过，随后推送记录预算恢复的文档提交 `ce70c3b5e508acdc3fc6ce38c9e3f80f412ae8d5`，该次尝试被并发规则自动取消。预算阻塞已解除；被取消的运行不记为完整验收通过。

### 恢复后的实际安装与升级

[CI 34815548895](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34815548895) 对应分支提交 `ce70c3b5e508acdc3fc6ce38c9e3f80f412ae8d5`；实际 PR 合并构建及产物清单提交为 `d20d85cfbc0f439bb2014dbef888b71b63ee7dc9`，两者不能混用。六项基础检查全部通过。Windows 桌面 **384 通过**、联调工具 **98 通过**、后端 **835 通过、4 项 POSIX 限定跳过**，覆盖率日志显示 **88%**。

2026-09-14 **07:34:37 UTC**，实际安装冒烟通过：新安装、同版修复、完整备份后空库重装，以及受限 LocalService 读取离线配对凭据均完成。**07:46:06 UTC**，真实 rc.4/rc.5 安装器升级套件的七项场景通过，包含账户/配置/密钥与 API 保存的配方保留、自定义数据库实际写入、旧失败事务额外保全与恢复。它们属于本轮 Windows CI 的实际安装结果，不是本机单元测试推断。

下载后的 `windows-overwrite-partial.json` 为 `status=passed`、`cleanup_complete=true`；七个场景原始日志的 SHA256 与各自引用逐一匹配，版本、实际构建提交、CI 编号及安装器摘要与联调套件一致。七项为 `fresh_install`、`same_version_repair`、`downgrade_rejected`、`upgrade_rc4`、`custom_database_preserved`、`rollback_recovery`、`upgrade_rc5`。

随后完整 SmdBench 在 `installer_offline_confirmation` 阶段以 `TimeoutError` 失败。`acceptance.json` 记录 `status=failed`、`cleanup_complete=true`，包含此前 18 项通过断言：实际 TLS、标准/非标配方、实验及报告、权限、故障、服务重启、未知运行恢复和测定/冷却忙碌拒绝。`busy_rejected` 原始日志摘要已核对；没有 `offline_confirmation` 通过断言。失败调用栈止于 `browser.py::eventually` 的有界等待，不能据此认定离线确认或修复成功。

随后定位为联调工具等待了错误字段：`/api/status` 的 `_v2.online` 属于保留的板端快照，断线会使状态缓存失效，但不会改写这份快照，因此等待该字段变成 false 会持续到超时。生产状态顶层此时已给出 `comm_quality=offline`、`data_fresh=false`、`control_ready=false`。修正仅让 SmdBench 使用这些当前状态字段判断离线，并增加实际路由与状态缓存的回归，生产状态语义不变。该次失败记录保留；修正后的完整 Windows 场景与封装结果见下节。

| 套件 | CI 34815548895 已观察到的结果 | 该轮状态 |
|---|---|---|
| 新安装与旧版覆盖 | 实际 LocalService 启动；rc.4/rc.5 覆盖后已改密码、账户、配方、配置和密钥保留 | 对应安装步骤通过 |
| 修复与恢复 | 同版受损程序修复、旧版特定拒绝、带真实备份的旧失败事务恢复；较新记录保留在额外快照，原资料恢复并继续升级 | 对应安装步骤通过 |
| 自定义数据路径 | 带中文和空格的外置数据库仍为应用实际写入目标，不改回默认库 | 对应安装步骤通过 |
| 安装版联调与维护 | TLS/页面/实验/报告、故障和忙碌拒绝的 18 项断言通过；离线安装确认场景超时 | 套件失败，待修复复验 |
| 证据与资源 | 两个套件均已完成归属清理；本轮未进入最终工具封装和[正式版证据聚合门禁](../release-acceptance.md)，没有九项完整通过证据 | 未完成，未判通过 |

CI 34815548895 的结论为失败。安装套件七项及 SmdBench 先前断言的通过不能代替当时未完成的离线确认场景，也不能代替最终资产核验。失败诊断与已通过步骤保留在该轮 `windows-package-diagnostics` artifact；当时 PR #79 仍为 draft、未合并，也未创建正式标签或 Release。后续成功记录不覆盖该次失败。

七项安装日志与两项 SmdBench 维护日志只有在对应真实断言成功时才进入汇总。CI 失败需保留该轮脱敏证据、失败阶段与恢复说明，不重用上一轮 passed，不上传密码、PSK、会话、完整数据库或原始服务配置。

## 修正后的完整 PR 验收与主线合并

[PR CI 34820643004](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34820643004) 的七项必要检查全部通过。实际 PR 合并构建与产物清单提交为 `ca48cd408686f45fdfb1885a8bc39d4c08febfe6`；这是 PR 构建身份，不是随后 squash 合入主线的提交，也不是最终标签发行身份。

该轮重新完成真实安装冒烟、rc.4/rc.5 七项覆盖安装与恢复场景、完整安装版 TLS 实验/报告和故障回归，以及测定/冷却忙碌拒绝与离线确认。同版离线修复先在未确认时返回 20，确认后返回 0；修复前后保留 **8 个实验、285 条采样、10 份报告**，账户、配置与配对密钥不变，模拟器重新连接并确认空闲。

下载的 `windows-acceptance.json` 九项场景全部 passed、`cleanup_complete=true`，`bench-acceptance.json` 的 19 项断言全部 passed、`cleanup_complete=true`。九份独立日志的实际 SHA256 与引用一致，版本、构建提交、CI 编号和安装器身份一致；元数据文件已按 `SHA256SUMS.txt` 核对。流水线严格工具封装、正式版证据聚合和实际文件门禁均通过。这关闭了上一轮因读取旧 `_v2.online` 快照而失败的工具等待条件，但不将模拟验收扩展成实体设备资格。

元数据保存在该轮 `windows-release-metadata` artifact，脱敏截图与合成报告在 `windows-package-diagnostics` artifact；本地对应归档为仓库外 `overwrite-metadata-34820643004`、`overwrite-diagnostics-34820643004`。这些 PR 证据绑定 `ca48cd408686f45fdfb1885a8bc39d4c08febfe6`，不能复用为重新冻结的主线或标签包背书。

2026-09-14 **09:06:09 UTC**，[PR #79](https://github.com/kevinalliswell/smd-web-hmi/pull/79) squash 合入 `main@b6cd21f4e9b89f6a0c050f415d05d0ce6795e79d`。随后启动主线复验，结果与本次 PR 构建分别记录如下；发布前必须对修复后的主线和不可移动标签重新验收。

## 主线复验失败与修复边界

[主线 CI 34826122511](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34826122511) 对应 `b6cd21f4e9b89f6a0c050f415d05d0ce6795e79d`。其他五项基础检查通过；Windows 桌面 **384 通过**、联调工具单元 **106 通过**。Windows 后端在 `test_cleared_unacknowledged_alarm_can_be_confirmed_before_run_ack[False]` 中失败：报警确认预期 HTTP 200，实际为 HTTP 504、`device_comm_timeout`，此前出现 `v2.source_recovery_incomplete` / `HostCommTimeoutError`。`--maxfail=1` 结束时为 **632 通过、1 失败、4 跳过**，不能记为后端全量通过。原始日志归档在仓库外 `overwrite-main-windows-tests-34826122511.log`。

该轮未进入主线安装打包，故没有本次主线的实际安装版闭环或可发布资产。PR CI 34820643004 的成功仍然有效，但仅对应其自身构建；不能替代此次失败，也不能据此打标签放行。

在原场景临时注入 **6–10 ms 受控异步数据库延迟**后，复现了相关时序问题：`log_request` 已发出，而此前实时回调尚未完成持久化；日志进度等待达到 3 秒后超时，迟到的持久化确认触发 `callback_failed` 并断开连接，之后报警确认返回 HTTP 409。**这不是主线 HTTP 504 的精确复现**；归档日志无法还原主线当时具体碰到哪个等待点。诊断证明的是本地持久化与源日志请求之间存在竞争，不能把两次不同响应写成同一条已重现故障轨迹。

在 `fix/hostcomm-alarm-ack-timeout` 修复分支已实现[有界本地回调准入](../hostcomm/v2/wire.md#8-原始日志与补传)：发送 `log_request` 前最多等待 3 秒，最多 4 个等待者，只等待进入准入时已有的回调（含正在落盘的回调）；后到消息不延长等待，连接、session 或 boot 变化使准入失效。线上 3 秒期限、8 秒租约和协议字段保持不变。修复另保留后台因本地容量限制暂缓的恢复任务：结束阶段回补被本地积压暂缓，随后 `ack_run` 已确认结束并回到 idle，后台仍自动补齐源日志。只有后台 `V2CapacityError` 设置待补传标志，取消、会话变化及其他失败不清除此标志，成功完成只读扫描或启动同步后才清除；不据此宣称新日志身份下的旧缺口也已恢复。

新增 **13 个行为用例**。空闲恢复用例先在旧代码进入 idle 后等待 8 秒仍未自动补齐，以失败结束（13.58 秒），新逻辑通过（6.51 秒）。受控 6 ms 异步数据库延迟的同一原场景修复前后记录分别归档在仓库外 `hostcomm-slow-sqlite-before-20260914.log`、`hostcomm-slow-sqlite-after-20260914.log`；准入和空闲恢复的先失败证据分别为 `hostcomm-callback-admission-red-20260914.log`、`hostcomm-source-idle-red-20260914.log`。

修复工作区在 macOS / Python 3.13 下执行后端全量 **848 通过、3 项 Windows 专项跳过，覆盖率 86.94%，耗时 94.05 秒**。命令为在 `backend/` 执行 `../../implementation-venv/bin/python -m pytest --maxfail=1 --cov=app --cov-report=term --cov-fail-under=80`，完整日志归档在仓库外 `hostcomm-callback-admission-backend-20260914.log`。这些结果对应当时修复分支工作区，不能归到未含修正的主线提交；其后首轮完整 Windows CI 结果及审查跟进见下节。该阶段OVER-01—04记录为implemented、OVER-05—07为doing，后续结果另行追加。

## 修复 PR 首轮验收与审查跟进

修复提交 `0f4397c92da7d21d669c13554cc9ec08069cd788` 的 [PR #81](https://github.com/kevinalliswell/smd-web-hmi/pull/81) 首轮 [CI 34829233667](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34829233667) 七项检查全部通过。实际 PR 合并构建及产物清单提交为 `9a0a0a29891adb99d87c7e66a0eb7ea444be898e`，不是修复分支提交或后续主线提交。Windows 桌面 **384 通过**、联调工具单元 **106 通过**、后端 **847 通过、4 项 POSIX 限定跳过，覆盖率 87.88%**。

该轮实际安装冒烟、rc.4/rc.5 七项升级与恢复、完整 SmdBench **19 项断言**、Windows 汇总 **9 项场景**均通过，归属清理完成。离线同版修复前后保留 **8 个实验、237 条采样、10 份报告**，账户、配置与配对密钥保留。九份独立日志与元数据摘要已在下载后逐一核对；安装器 EXE 与工具 ZIP 的完整字节由该轮 CI 门禁核验，本机未下载这两个大文件，不能声称又完成一次本机全包核验。

| PR 产物 | CI 34829233667 记录的 SHA256 |
|---|---|
| `SmdHmi-0.3.0-windows-x64.exe` | `1e32d0c6eacf7e7bd24b8b958f36caccee5c9f659d56e7f0a310083b9ab6c240` |
| `SmdBench-0.3.0-windows-x64.zip` | `907ec57bdd257aaabb7a4b2f399f927009ff9f78636235eb5665b718ff535f54` |
| `software-acceptance.json` | `42efcf78240f7b2be1a3d4a17dd182f9511ba5246275a8b334538267b5902cc3` |

元数据与脱敏诊断归档在该轮 `windows-release-metadata`、`windows-package-diagnostics` artifact；本地分别保存于仓库外 `hostcomm-fix-metadata-34829233667`、`hostcomm-fix-diagnostics-34829233667`。这些是 PR 构建证据，不是正式 Release 资产摘要。

首轮检查通过后，[审查 4003963541](https://github.com/kevinalliswell/smd-web-hmi/pull/81#discussion_r4003963541) 指出另一条遗漏路径：`_request()` 会把 `V2CapacityError` 转为 `CommandError(error_code="device_read_capacity")`，后台恢复处理仅识别前者，因而转换后的读容量拒绝没有登记待补传。首轮通过的已有场景不能证明当时这条遗漏已处理；审查阶段 PR #81 尚未合并，后续修正及合并见下节。

跟进修正统一使用本地读容量异常识别器，仅接受原生 `V2CapacityError` 或精确的 `CommandError(HTTP 503, device_read_capacity)`，源日志恢复与可选读取共用分类；未知结果、协议错误和远端 busy 不转为本地自动重试。在真实四个读取槽位占满的条件下，分别覆盖 `get_status` 前和日志暂存建立后两个入口，基线均因待补传标志仍为 false 而失败（0.77 / 0.72 秒）；修正后的 **13 项专项最终复验通过，耗时 4.86 秒**，包括新增的 **9 项**：2 项真实槽位、5 项反例和 2 项分类一致性。先失败日志为仓库外 `hostcomm-read-capacity-red-20260914.log`、`hostcomm-log-request-capacity-red-20260914.log`。补充修正工作区在 macOS / Python 3.13 下执行后端全量 **857 通过、3 项 Windows 专项跳过，覆盖率 87.00%，耗时 97.95 秒**，沿用上述全量命令和 80% 门槛，完整日志为仓库外 `hostcomm-read-capacity-backend-20260914.log`。本次仅统一恢复及可选读取的异常分类并补回归，传输层与控制路径不变；独立审查已通过。

这些本机结果对应审查跟进的补充工作区，不能归到前次 `0f4397c92da7d21d669c13554cc9ec08069cd788` 或 PR 产物 `9a0a0a29891adb99d87c7e66a0eb7ea444be898e`。补充修正当时已实现并通过本机回归；随后的完整 Windows CI 另见下节，不使用首轮 CI 为不同代码背书。

上述审查跟进阶段，主线仍为 `b6cd21f4e9b89f6a0c050f415d05d0ce6795e79d`，正式标签与 Release 未创建。随后的 PR 复验及主线合并使用各自实际构建身份记录，不覆盖本轮已通过证据。

## 补充提交与后续验收登记

PR #81 最终源提交为 `0a6baf0f0ae54eb6540e5edca3e790d8a9f04c0d`，原[审查线程](https://github.com/kevinalliswell/smd-web-hmi/pull/81#discussion_r4003963541)已回复并标记 resolved。最终 [CI 34835133703](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34835133703) 的七项检查全部通过，实际 PR 合并构建及产物清单提交为 `a147dba9fe812c9b5d5575c98aadc0b21c369a07`。Windows 桌面 **384 通过**、联调工具单元 **106 通过**、后端 **856 通过、4 项 POSIX 限定跳过，覆盖率 88.12%**。

该轮实际安装冒烟、rc.4/rc.5 升级与恢复、**9 项安装场景及 19 项联调断言**全部通过，归属清理完成；10 份实际报告文件的字节摘要已核验。离线同版修复前后保留 **8 个实验、262 条采样、10 份报告**，账户、配置和配对密钥保留。九份场景日志、元数据身份及摘要已核对，PR 产物的实际文件门禁通过；以下摘要仅属于本轮 PR，不能用作重新冻结的主线或正式标签包摘要。

| 最终 PR 产物 | CI 34835133703 记录的 SHA256 |
|---|---|
| `SmdHmi-0.3.0-windows-x64.exe` | `a2008c6c8fae86ceacc32624dad30551f8a2f3b00cf834aec1ff87e14e15d449` |
| `SmdBench-0.3.0-windows-x64.zip` | `5bea72ba8d134535b364881d6e8b2ebff7a919ead71b0ddd78e39e5e7a82bed3` |
| `software-acceptance.json` | `dd573c5b7a368f5cea88b182cbc08b2a917a9afdd06a65d760f8a8a3a604913d` |

该轮元数据及脱敏诊断保存在 `windows-release-metadata`、`windows-package-diagnostics` artifact；本地分别归档在仓库外 `hostcomm-capacity-metadata-34835133703`、`hostcomm-capacity-diagnostics-34835133703`。首轮 `9a0a0a29891adb99d87c7e66a0eb7ea444be898e` 和本轮 `a147dba9fe812c9b5d5575c98aadc0b21c369a07` 的证据分开保留。

2026-09-14 **11:55:58 UTC**，[PR #81](https://github.com/kevinalliswell/smd-web-hmi/pull/81) 正常通过受保护的 squash 合并进入 `main@404ecb50edf23864ed482e68cc4a1933deb3383f`；文件树与最终源 `0a6baf0f0ae54eb6540e5edca3e790d8a9f04c0d` 相同。提交身份仍不同；相同文件树和 PR 通过不代替主线及标签构建验收。

### 修复后主线的预算阻塞与恢复

[主线 CI 34840701388 第1次尝试](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34840701388/attempts/1) 的六项基础任务均未启动，安装打包任务 skipped。Check `103964751685` 的 annotation 原文为：

> The job was not started because an Actions budget is preventing further use.

第1次尝试因 GitHub Actions 预算阻塞而未执行，**不是代码测试失败，也没有该次主线测试通过结果**。[第2次尝试](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34840701388/attempts/2) 的重试记录时间为 **2026-09-14 14:34:42 UTC**，仍因预算未运行基础任务；GitHub 整体结论为 failure，不应转述为代码回归失败。两次尝试均对应 `404ecb50edf23864ed482e68cc4a1933deb3383f`；预算阻塞和此前已经实际完成的 PR 验收分别保留。

2026-09-15 用户再次上调 Actions 预算。[第3次尝试](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34840701388/attempts/3) 的 `run_started_at` 为 **2026-09-15 07:38:48 UTC**，构建源仍为 `404ecb50edf23864ed482e68cc4a1933deb3383f`。该次随后完成六项基础检查，均通过。[Windows 测试 job 104295030065](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34840701388/job/104295030065) 使用 Python 3.13.15，结果如下：

| Windows 检查 | 实际结果 |
|---|---|
| 桌面事务单元回归 | 384 通过，241.98 秒 |
| SmdBench 单元回归 | 106 通过，21.36 秒 |
| 后端全量回归 | 856 通过、4 项 POSIX 限定跳过、1 条警告，734.39 秒；覆盖率 88.02% |

原报警确认用例的 False/True 两个分支和新增的读槽位占满后回补用例均通过。1 条警告是 Starlette TestClient 使用 httpx 的弃用提醒，不是失败；不得将本轮写为无警告。完整原始日志在仓库外 `overwrite-main-windows-tests-34840701388-attempt3.log`。

[Windows 安装打包 job 104300196182](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34840701388/job/104300196182) 已完成程序冻结；真实 rc.4/rc.5 覆盖安装套件于 **2026-09-15 08:23:28—08:33:14 UTC** 执行并通过。完整安装版联调随后于 **08:33:14—08:44:09 UTC** 执行并通过，正式版资产门禁于 **08:44:21 UTC** 完成；该次主线 CI 七项检查全部通过。

该次主线构建身份为 `404ecb50edf23864ed482e68cc4a1933deb3383f`，版本 `0.3.0`。下载后已核验 **9 项 Windows 场景、19 项联调断言、10 份实际报告文件字节及摘要、13 份元数据文件摘要**，两个套件均记录 `cleanup_complete=true`。元数据归档在仓库外 `overwrite-main-metadata-34840701388-attempt3`，诊断与报告归档在 `overwrite-main-diagnostics-34840701388-attempt3`。安装器 EXE 与工具 ZIP 的完整字节由该轮 CI 资产门禁核验；本机只下载元数据和诊断，未再下载这两个大包。

主线离线确认场景的未确认安装返回20，服务PID保持7144，程序、配置、数据库与维护门禁不变；确认后安装返回0，服务PID由7144变为5392，保留 **8个实验、246条采样、10份报告**。档案ID、采样数、报告字节及摘要、账户登录、配置树与PSK均已比对，模拟器重新连接并确认idle。该证据只属于本次主线产物，不为标签重新构建的包背书。

| 主线产物 | CI 34840701388 第3次尝试记录的 SHA256 |
|---|---|
| `SmdHmi-0.3.0-windows-x64.exe` | `83c9dd4eeefaf2fe295e3c63536591d00c55e8466ea84a3ab21ce856e0d61852` |
| `SmdBench-0.3.0-windows-x64.zip` | `36f8c1229080565b18d9912e96567da38f91d5fa906f22965d7c0a73689cf360` |
| `software-acceptance.json` | `02061bc6134d546b5eb2a440661440c1821ccd26204b04deef1614c8bd260470` |

### 标签构建与正式发布

**2026-09-15 08:46 UTC** 已创建并推送 `v0.3.0`，标签对象为 `4a1760fc5fff9cdb6b6e11b762d12ecbb096085a`，剥离后的提交目标为已通过主线检查的 `404ecb50edf23864ed482e68cc4a1933deb3383f`。旧标签保持原样。

[Release run 34948794299](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34948794299) 已完成，**八项任务全部成功：七项必要检查与发布任务**。标签Windows测试job104314502230使用Python3.13.15：桌面384通过（172.79秒）、联调工具106通过（20.22秒）、后端856通过/4项POSIX跳过/1条Starlette TestClient弃用警告（363.08秒），覆盖率87.95%。报警确认两个场景通过，原始日志为仓库外 `overwrite-tag-windows-tests-34948794299.log`。

标签安装任务job104317694699依次完成：实际安装冒烟 **09:11:44—09:23:05 UTC**、rc.4/rc.5升级套件 **09:23:05—09:34:52 UTC**、完整安装版联调 **09:34:52—09:47:50 UTC**、正式版资产门禁 **09:47:50—09:48:00 UTC**，均通过。9项Windows场景、19条联调断言均为passed、清理完成；下载的13份元数据摘要和10份实际报告字节及摘要已经核验。该轮元数据、诊断分别归档在仓库外 `overwrite-tag-metadata-34948794299`、`overwrite-tag-diagnostics-34948794299`。

标签离线确认独立复核：未确认返回20、服务PID保持9164；确认后返回0、PID由9164变为7040，保留 **8个实验、282条采样、10份报告** 以及账户、配置和PSK，模拟器重连后为idle。该批采样数属于标签构建，不能与主线的246条混用。

[GitHub Release v0.3.0](https://github.com/kevinalliswell/smd-web-hmi/releases/tag/v0.3.0) 于 **2026-09-15 09:49:48 UTC** 发布，`draft=false`、`prerelease=false`，有 **21项资产**。实际21项Release文件已全部下载至仓库外 `release-v0.3.0-404ecb5`。在提交 `404ecb50edf23864ed482e68cc4a1933deb3383f`、`GITHUB_RUN_ID=34948794299` 下执行 `scripts/release/publish_gate.py` 返回0；实际安装器与工具ZIP摘要、ZIP内部逐文件清单和内嵌manifest核验通过。下表为实际下载后比对通过的发行摘要，不能与上文主线包混用。

| 标签发行文件 | SHA256 |
|---|---|
| `SmdHmi-0.3.0-windows-x64.exe` | `1574ca2819c1efdd4bfa32548c1482d2c196fd09e140a4687ca00aec31a9d926` |
| `SmdBench-0.3.0-windows-x64.zip` | `cd64945e7f7951ad0a7d287b96636d35a130ea9c487460d58973ba3cbd732005` |
| `software-acceptance.json` | `8991101bc9890ac2c22669c0efb4e27056ab148e377608ab923c2f00f07b2b11` |

验证范围须区分自动门禁与本轮补验：[package_bench.validate_evidence](../../scripts/release/package_bench.py)强制基础12项联调断言子集存在、名字唯一且所有提交断言passed；Windows发布门禁强制精确9项场景。本轮发行产物通过独立补验脚本 `verify-final-release-assets.py`：精确21项资产、15条校验值、精确9项Windows场景、精确19项联调断言集合及身份和证据均通过，脚本返回0。因此本轮19项完整结果有实际产物与独立复核支持；不能将生产门禁描述为自动强制完整19项集合。补验不替代生产门禁或成功的标签工作流，不修改协议或门禁行为。

另一次独立复核确认：19份标签CI附件与Release对应副本逐字节相同，9份场景日志与诊断归档相同；SmdBench ZIP的982条文件清单和内嵌manifest一致；10份实际报告（8份HTML、1份PDF、1份XLSX）的文件格式、长度和SHA一致。

最终下载门禁和独立补验日志分别保存在仓库外 `release-v0.3.0-publish-verification.log`、`release-v0.3.0-asset-verification.log`。OVER-01—07按本轮软件发行范围完成；历史失败、各次PR成功、主线与标签的不同产物摘要均保留。

### 文档收尾期间的 CI 脚本诊断

发布后的[PR #82](https://github.com/kevinalliswell/smd-web-hmi/pull/82)最初只更新四份文档，应用和安装器源码与标签相同。[CI 34955791654 第1次尝试](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34955791654/attempts/1)的六项基础检查通过，实际 PR 构建为 `b0f9f24d135387d4993fddb9865325de6d29b276`。首次安装、LocalService、数据库/schema/存储/备份验活、静态页面及同版修复均通过；独立测试机归档重置在 `test-reset-smoke.ps1` 的600秒子进程等待处超时，安全清理完成。原诊断没有保留重置内部阶段，不能确定具体原因。此前该阶段曾成功运行约404、455和551秒；这些时长不足以证明本次是环境原因。

同提交仅重跑失败作业的[第2次尝试](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34955791654/attempts/2)在安装前的只读 `Get-CimInstance` 服务查询处超时，未开始安装，也未产生安装结果 JSON。这与第一次归档重置超时是两个不同失败点。两轮原始日志、独立诊断分别保存于仓库外 `release-docs-windows-package-34955791654.log`、`release-docs-diagnostics-34955791654` 和带 `-attempt2` 后缀的对应记录；不覆盖已发布标签的验收。

后续修正只作用于 CI 脚本：只读 CIM 查询最多尝试三次，每次仍设5秒操作超时，仅 CIM 异常允许有限重试；持续错误必须抛出，只有成功查询才可认定服务不存在。归档重置保留600秒上限和原始超时，在子进程确认退出、现有归属校验通过之后、清理之前，复用已有阶段记录，仅输出固定阶段枚举与退出等待的单调耗时。未知或无效记录保留 `unknown`，不上传路径、配置、密钥或原始阶段内容，也不改变退出确认和清理判断。Windows 原生正反例及完整安装检查的执行结果见[该 PR 检查](https://github.com/kevinalliswell/smd-web-hmi/pull/82/checks)；本节不以本机静态检查替代 Windows 验证，不重新发布或移动 `v0.3.0`。

## 正式资产与其他验收

v0.3.0标签和GitHub Release已发布；主线、标签各自的完整验收及发布任务通过。21项实际发行资产的本地下载与校验值、ZIP内部清单、机器门禁及独立补验均通过。Release的机器验收与SHA256SUMS是发行字节的真源；上文PR及主线包只为自身构建背书。

2026-09-15 经 GitHub 仓库 API 核对，`private=true`、`visibility=private`。仓库可见性保持原样；GitHub Release 即使 `draft=false`，仍遵循该私有仓库的访问权限。发行资产下载需要相应仓库权限，发布记录不表示将仓库改为互联网公开。

Win10/11 干净断网 WebView2、普通操作员、中文路径和显示缩放的人工验收，以及可信签名、真实 STM32H750 固件、实物安全联锁、国标符合性和持续运行仍分别未验收。软件正式版资格不关闭这些项目，也不证明用户发生 rc.5 升级失败的那台测试机已经完成恢复。
