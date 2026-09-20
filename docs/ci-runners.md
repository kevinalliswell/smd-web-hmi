# CI 运行器与跨平台检查

2026-09-20 在 PR #83 接入 Mac ARM64 检查与 Windows Server 2019 纯编译运行器。执行入口仍为 [CI](../.github/workflows/ci.yml) 和 [Release](../.github/workflows/release.yml)，共用 [checks.yml](../.github/workflows/checks.yml)。本次只调整 CI 执行环境，不更改应用版本或发布资产。

## 任务分配

| 检查 | 执行环境 | 保留的验收要求 |
|---|---|---|
| `lint` | 本机 Mac ARM64 | 版本/锁文件、文档链接、HostComm 契约、Black、isort |
| `frontend` | 本机 Mac ARM64 | JS 固定向量、OpenAPI 漂移、生成类型/类型检查、lint、单测、生产构建；上传唯一 `frontend-dist` |
| `backend (local-mac, 3.13)` | 新增本机 Mac ARM64 | 安全测试、空库迁移、桌面事务/进程、独立联调工具单测、完整后端及覆盖率 ≥80% |
| `backend (ubuntu-latest, 3.13)` | GitHub-hosted Linux | 原有全部测试与覆盖率门槛 |
| `backend (ubuntu-latest, 3.11)` | GitHub-hosted Linux | 原有旧 Python 基线兼容与覆盖率门槛 |
| `backend (windows-latest, 3.13)` | GitHub-hosted Windows | 原有后端/桌面/联调工具测试、原生 SCM/ACL 依赖及 PowerShell 语法 |
| `audit` | GitHub-hosted Linux | backend/desktop/bench 三份锁文件审计和 npm high 门槛 |
| `windows-compile` | 本机 Windows Server 2019 x64，非管理员 Session 0 | 同提交前端编译、完整应用/Bench 冻结、Fixed WebView2 校验、NSIS 编译、文件清单与 SHA；不执行产品程序 |
| `windows-build` | 独立 GitHub-hosted Windows | PyInstaller、Fixed WebView2、NSIS、独立 SmdBench、构建字节身份 |
| `windows-package` | 独立 GitHub-hosted Windows | 安装、真实 RC 覆盖升级、保留数据/修复/恢复、TLS/实验/报告闭环与最终资产证据 |
| `publish` | GitHub-hosted Linux，仅标签发布 | 校验值、发布资格和实际资产身份核验 |

既有 job ID 和三组后端矩阵名称保持不变，Mac 后端是额外覆盖。Windows 构建仍等待 lint、全部 backend、frontend、audit；安装验收仍验证同次构建的候选字节。Mac 不能代替 Windows Service、安装器、WebView2 或 Linux 兼容检查。

## 本机选择与隔离

Mac 选择 `[self-hosted, macOS, ARM64, local-mac, smd-web]`，登记名称 `kevin-mac-smd`；Windows 选择 `[self-hosted, Windows, X64, local-windows, windows-build, smd-web]`，登记名称 `ustb-win-smd`。最终实际执行者以每次 Actions jobs API 的 `runner_name` 和任务运行摘要为准，不能只根据 YAML 标签认定已经在本机执行。

共享工作流的 `local_mac`、`local_windows` 默认 false。CI 调用仅对本仓库分支 PR（排除 Dependabot）及非 PR 事件启用；fork/Dependabot PR 的 lint/frontend 回到原 Ubuntu 环境，并省去新增的本机后端行，原有所有验收任务继续执行。Release 对受控标签/手动运行启用同一套本机检查。不得为了接收外部 PR 改用 `pull_request_target` 执行未审核代码。

本机 Python 通过固定提交的 setup-uv、uv `0.10.11` 和可移动 Python `3.13.12` 创建，避免要求创建 setup-python macOS 包的 `/Users/runner/hostedtoolcache` 系统路径；托管 Linux/Windows 保持 setup-python。每个任务在 `RUNNER_TEMP` 建立唯一 venv，禁用用户 site-packages，要求 pip 只在 venv 安装，并保留 requirements 锁文件哈希校验。Mac 任务断言 Darwin/ARM64、Python 3.13 和 `ssl.HAS_PSK`，确保 TLS-PSK 能力缺失时失败而非把跳过算通过。

Node 继续通过 setup-node 选择 24。checkout 不保留 Git 凭据；Mac 任务结束的 `always()` 步骤仅删除本任务的 venv 和该 checkout 未跟踪生成文件，保留源码及下载缓存。不更改主机 HOME、用户 Python、其他仓库工作区或运行器服务。取消/进程崩溃可能中断清理；下次 checkout 的清理和管理员对本运行器目录的维护仍需保留。

本机与另一仓库运行器共享主机资源；接入前可用磁盘约 8.5 GiB。Windows 非交互编译在 Server 2019 执行；实际程序运行、安装和桌面验收仍留在独立环境。运行器以用户 LaunchAgent 运行，主机重启后需该用户登录；不能将服务登记“在线”视为跨重启可用性验收。


## Windows 纯编译边界与依赖

`windows-compile` 是额外的兼容构建，不代替原 `windows-build`、`windows-package` 或 Windows 后端矩阵。它独立编译同一 SHA 的前端，以便托管预算或跨任务产物传递被阻塞时仍能取得实际编译结果。原前端类型、测试、构建及 `frontend-dist` 上传门禁保留；原发布仍等待共享工作流全部必要任务成功。fork/Dependabot PR 不进入两个长期主机。

Server 2019 使用 `NT AUTHORITY\NETWORK SERVICE`，非管理员、Session 0。工具在本任务 `RUNNER_TEMP/smd-hmi-<run>-<attempt>-<job>-<随机值>` 下准备：checkout 前下载并核验 MinGit 2.55.0.5；随后准备 PowerShell 7.4.20、NSIS 3.11、uv 0.10.11 与 Python 3.13.12，均不安装到 Program Files、不修改系统 PATH/业务 Python。ZIP 的 SHA-256 固定在工作流和[准备动作](../.github/actions/local-windows-tools/action.yml)；Node 24 通过 setup-node 使用运行器工具目录。Python 必须支持 TLS-PSK，依赖仍按现有锁文件及哈希安装。准备步骤记录实际账户、Session、OS、架构、Git、Python、OpenSSL、PowerShell、NSIS；最终记录源码 SHA 和输出文件摘要。

编译入口显式使用 `build-windows.ps1 -CompileOnly -MakeNsis <任务内路径>`，自托管环境遗漏 `-CompileOnly` 会在下载或冻结前拒绝。该模式仅省去新增任务中的冻结程序执行；托管 `windows-build` 仍使用默认完整模式，继续运行迁移、服务/更新器/桌面 renderer 自检、冻结 Bench 的 Chromium 自检。`windows-compile` 会检查拒绝路径、PowerShell 语法并完成应用和 Bench 编译，但不会启动生成的应用、安装器或浏览器。

原因包括：冻结服务迁移会使用产品全局互斥锁，Windows 后端测试会创建 LocalService 计划任务，配对/Bench 自检需要管理员所有者权限。这些行为不能在共享业务服务器上视为普通单测。安装、修复、升级、卸载及 GUI 验收继续遵守[独立环境要求](../deploy/windows/SMOKE-CI.md)，不伪造 `RUNNER_ENVIRONMENT`，不提升运行器服务权限。Server 2019 编译通过不证明 Win10/11 运行通过。

新增编译任务只把构建 SHA、文件摘要和结果写入 Actions 日志与 Summary，不上传另一套候选安装包；任务结束删除本任务工具、venv 和 checkout 生成文件。正式候选仍由原托管构建产生，经实际 artifact ID、摘要和完整 Bench 文件清单交接给独立验收任务。没有候选传递或原验收证据，不能发布。清理不覆盖业务目录、其他仓库目录或服务。checkout 尚未成功或任务被强制中断时，仓库内清理动作可能无法运行，残留仅在该运行器临时/工作目录，由后续任务清理或管理员按归属处理。Windows 与另一仓库共享 NetworkService 和主机资源，目录分开不构成账户级隔离。

## 验证记录与故障处理

工作流修改前运行 `actionlint` 和 `git diff --check`，文档修改运行 `python scripts/check_docs.py`。推送本 PR 最新提交后，核对 Actions 的 head SHA、PR 合并测试 SHA、每个 job 的 runner 名称/标签及结果；以最新提交的真实 CI 结果为准，不能沿用旧提交绿灯。

首次接入前，文档提交 `39263dbde97743befbc5a6b08d710c7fe81b8fc7` 的 [CI 35491754893](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/35491754893) 已在原 hosted 环境完成八项检查。新的本机接入结果在 [PR #83](https://github.com/kevinalliswell/smd-web-hmi/pull/83) 最新检查及本次交付的机器记录中逐项登记；原结果不算本机执行证据。

任务失败保留日志和实际错误，修复后推送并验证新的提交；平台服务不可用时记录未完成任务及原因，不能删除 matrix、降低覆盖率、跳过安全测试或把 Windows 安装验收挪到 Mac 以获得绿灯。固件/真机、Win10/11 人工 WebView2 验收依然单独进行。

PR #83 先前交付的 doc.4 离线包固定于 `39263dbde97743befbc5a6b08d710c7fe81b8fc7`，保持原摘要；本次继续修改 CI 后，该包是文档交付快照，不代表最新工作流提交。本轮先完成 CI 接入与验证，不重新发布应用或覆盖原离线包。
