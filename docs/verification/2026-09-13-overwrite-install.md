# 2026-09-13 覆盖安装与软件正式版验证

维护角色：集成、Windows 测试与发布负责人。关联任务：OVER-01—07；范围依据[ADR-011](../decisions/ADR-011-overwrite-install-and-software-release.md)。目标应用版本为 `0.3.0`，协议保持 `2.0 / 2.0-design.1`、文档修订 `2.0-doc.3`。2026-09-14 预算恢复后的本轮 CI 已通过基础检查、实际安装冒烟及 rc.4/rc.5 升级套件；**安装版联调在离线安装确认场景超时，整轮失败待修复，证据聚合与正式发布未完成**。

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

随后定位为联调工具等待了错误字段：`/api/status` 的 `_v2.online` 属于保留的板端快照，断线会使状态缓存失效，但不会改写这份快照，因此等待该字段变成 false 会持续到超时。生产状态顶层此时已给出 `comm_quality=offline`、`data_fresh=false`、`control_ready=false`。修正仅让 SmdBench 使用这些当前状态字段判断离线，并增加实际路由与状态缓存的回归，生产状态语义不变。本记录尚未登记该修正的测试通过结果；完整 Windows 场景和最终封装仍需下一轮 CI 重新验收。

| 套件 | 本轮已经观察到的结果 | 当前状态 |
|---|---|---|
| 新安装与旧版覆盖 | 实际 LocalService 启动；rc.4/rc.5 覆盖后已改密码、账户、配方、配置和密钥保留 | 对应安装步骤通过 |
| 修复与恢复 | 同版受损程序修复、旧版特定拒绝、带真实备份的旧失败事务恢复；较新记录保留在额外快照，原资料恢复并继续升级 | 对应安装步骤通过 |
| 自定义数据路径 | 带中文和空格的外置数据库仍为应用实际写入目标，不改回默认库 | 对应安装步骤通过 |
| 安装版联调与维护 | TLS/页面/实验/报告、故障和忙碌拒绝的 18 项断言通过；离线安装确认场景超时 | 套件失败，待修复复验 |
| 证据与资源 | 两个套件均已完成归属清理；本轮未进入最终工具封装和[正式版证据聚合门禁](../release-acceptance.md)，没有九项完整通过证据 | 未完成，未判通过 |

本轮 CI 结论为失败。安装套件七项及 SmdBench 先前断言的通过不能代替未完成的离线确认场景，也不能代替最终资产核验。失败诊断与已通过步骤保留在该轮 `windows-package-diagnostics` artifact，后续修复须重新执行完整检查。PR #79 仍为 draft、尚未合并，未创建 `v0.3.0` 标签或正式 Release。

七项安装日志与两项 SmdBench 维护日志只有在对应真实断言成功时才进入汇总。CI 失败需保留该轮脱敏证据、失败阶段与恢复说明，不重用上一轮 passed，不上传密码、PSK、会话、完整数据库或原始服务配置。

## 正式资产与其他验收

`v0.3.0` 标签、正式 Release、安装器与工具 ZIP 摘要、软件/Windows 验收摘要均待本轮完整联调与机器门禁通过后记录。实际安装与旧版升级套件已通过，PR #79 尚未合并，不将部分步骤结果当作完整验收或发布完成。发布标签必须对应重新运行必要检查和实际安装版验收的提交，不移动旧标签。

Win10/11 干净断网 WebView2、普通操作员、中文路径和显示缩放的人工验收，以及可信签名、真实 STM32H750 固件、实物安全联锁、国标符合性和持续运行仍分别未验收。软件正式版资格不关闭这些项目，也不证明用户发生 rc.5 升级失败的那台测试机已经完成恢复。
