# Windows 桌面与本机 Web 交付

交付物为 `SmdHmi-<SemVer>-windows-x64.exe`，配套 `SHA256SUMS.txt`、`manifest.json`、`sbom.cdx.json`。目标是 Windows 10/11 x64，系统需要 .NET Framework 4.8。CPython 3.13、Python 依赖和固定 WebView2 随版本打包，目标机无需安装 Python、Node 或在线下载运行时。Windows 实机验收完成前属于候选交付，不能把 macOS 单元测试视为安装成功证明。

旧 Python 3.11 + wheels + 共享 venv 的批处理交付已移除。开发联调期没有需要兼容的正式历史安装；不要用旧批处理升级新服务。

## 进程和目录

| 项目 | 位置/行为 |
| --- | --- |
| 程序和独立依赖 | `%ProgramFiles%\SmdHmi\versions\<version>\SmdService、SmdDesktop、SmdUpdate` |
| 固定浏览器与前端 | 同版本目录下 `webview2`、`frontend` |
| 后台 | Windows 服务 `SmdHmi`，账户 `NT AUTHORITY\LocalService`，单 worker、系统级互斥锁 |
| 现场配置 | `%ProgramData%\SmdHmi\config\service.env`，仅管理员/SYSTEM 可修改，服务只读 |
| 默认数据库 | `%ProgramData%\SmdHmi\db\smd.db`；支持配置为另一个绝对路径 |
| 升级记录 | `%ProgramData%\SmdHmi\updates\active.json` 和每次升级的数据库/config 备份 |
| 维护门禁 | `%ProgramData%\SmdHmi\maintenance.json`，独立于数据库路径 |
| 当前版本 | `%ProgramData%\SmdHmi\installation.json` |
| 界面地址 | `%ProgramData%\SmdHmi\client.json`，普通用户只读，默认 `http://127.0.0.1:8000` |
| 界面浏览器缓存 | 当前用户 `%LocalAppData%\SmdHmi\WebView2` |
| 服务日志 | `%ProgramData%\SmdHmi\logs\service.log`，10 MB × 10 份轮转 |
| 安装、升级及恢复错误日志 | `%ProgramData%\SmdHmi\logs\updater.log`，5 MB × 6 份轮转；`updates` 保存事务及备份，不能代替此错误日志 |

桌面界面不提供 Python JS bridge，也不拥有服务停止能力。关窗口、退出登录、切换 Windows 用户都不会结束后台。设备控制仍由同一个后台网关处理。系统级恢复任务 `SmdHmi-Recover` 使用 SYSTEM，负责开机时检查未完成升级并回退；正常后台一直使用受限服务账户。

## 安装与日常使用

1. 在可信构建渠道核对安装器 SHA-256；以管理员运行安装器。
2. 新安装默认 HostComm 2.0、未配对且 `HOSTCOMM_MOCK=false`。管理员配置实际 STM32 IP、端口，并按随包 `HOSTCOMM-2-RUNTIME.md` 完成受控离线配对和固件侧凭据交接；仅设置 IP 不等于可以控制设备。升级保留原协议与已有配置，1.0 安装不会自动切换到 2.0。
3. 初始 `admin` 口令在受限文件 `config/bootstrap-admin-password.txt`。首次登录必须改密。
4. 普通用户从开始菜单打开 **SMD HMI**，或用本机浏览器访问 `http://127.0.0.1:8000`。后台健康不等于硬件实验许可：设备在线、新鲜状态和安全条件单独决定是否可操作。

默认仅监听本机。局域网接入复用同一 FastAPI 服务、账户和 WebSocket，不能另起第二个后台。先在设备空闲且无未闭合实验时准备维护；由管理员在 service.env 设置 SMD_HOST=0.0.0.0 和绝对路径 SMD_TLS_CERTFILE、SMD_TLS_KEYFILE，证书及私钥放在受限 config 目录，客户端必须信任证书且证书包含实际访问主机名及 localhost。相应把 client.json 改为 https://localhost:8000，再重启服务并只对现场子网放行防火墙 TCP 8000。服务拒绝无 TLS 的非本机监听，禁止跳过证书校验。安装器不会自动修改防火墙或安装另一套 Web 服务器。

## 人工离线升级

1. 以应用管理员进入系统维护，填写包内版本并准备升级。所有协议均要求不存在未闭合或待核查实验。HostComm 2.0 还要求没有未经核查的设备操作；明确未配对的新安装可离线维护，已配对设备必须在线获取状态并确认 `idle` 且无运行 ID，冷却或尚未确认结束的运行均拒绝。1.0 兼容路径要求在线、新鲜且明确空闲的设备状态。
2. 把经过校验的新安装器带到本机，以 Windows 管理员运行。安装器用本机受限票据再次检查条件。准备票据十分钟内有效；领取后不允许网页取消。
3. 升级器先验证所有文件、版本与固定运行时，再建立独立版本目录。确认旧服务已停止后，用 SQLite backup API 备份**旧服务实际配置的数据库**和 config，记录摘要，运行新版本随附迁移。
4. 切换服务路径并检查新版本、数据库/schema、存储、备份和前端 HTML。成功才提交事务并解锁。旧版本程序和全部依赖保留。

覆盖升级的目标版本必须与安装包一致，例如使用 `0.3.0-rc.4` 安装包时填写 `0.3.0-rc.4`。自 rc.4 起，更新器退出码 `20` 表示未找到准备文件或目标版本不匹配，安装器会明确引导回旧版软件准备；不能通过手工创建 `maintenance.json` 获得有效升级许可。

网页准备后尚未领取，可由应用管理员取消。领取后发生失败必须通过本机恢复；不能手工删除维护文件。升级期间不接收实验控制与改参。

用户已现场确认旧1.0离线测试机无法通过上述准备检查，并选择完整保留旧资料、用空库重装。仅此类无实体设备连接的专用测试机使用[完整备份与测试机重装流程](TEST-REINSTALL.md)：先预览，备份校验并清理成功后再以已发布rc.4安装器首次安装。该脚本单独验证，不修改既有发布资产，不适用于正在采集或连接设备的安装；当前验证边界见[专项记录](../../docs/verification/2026-09-06-test-reinstall.md)。

## 失败与断电恢复

安装失败先读取更新器日志，区分首次安装、覆盖升级和恢复失败。在管理员 PowerShell 中运行：

```powershell
Get-Content "$env:ProgramData\SmdHmi\logs\updater.log" -Tail 80 -Encoding UTF8
```

如果显式设置了 `SMD_DATA_ROOT`，使用该目录下的 `logs\updater.log`。反馈错误时提供安装方式、原版本及末尾错误，不提供 `service.env`、初始密码或配对密钥。日志不存在可能表示更新器尚未启动，或日志目录尚未创建成功；需结合安装器退出码、Windows 应用拦截记录和磁盘/目录访问情况继续定位，不能直接推断是维护票据缺失。

`0.3.0-rc.3` 安装器的通用错误提示存在中文乱码，并把错误日志路径写成了 `updates`；这不代表安装失败的具体原因。不要仅凭该提示删除数据或维护记录。已安装版本的覆盖升级仍须按上述维护步骤准备目标版本，不能通过重新双击安装器跳过门禁。

异常时升级器停止服务，校验并恢复迁移前数据库及配置，切回旧版本目录，验证健康后解除维护锁。不会使用降级迁移代替整库恢复。断电后由开机任务读取阶段日志执行同一路径；恢复动作可重复执行。

若自动恢复未完成，在管理员 PowerShell 中运行一个仍完整保留版本的恢复程序：

```powershell
& 'C:\Program Files\SmdHmi\versions\<完整版本>\SmdUpdate\SmdUpdate.exe' --recover --install 'C:\Program Files\SmdHmi'
```

查看 `updates/active.json` 的 `phase`。`rollback_failed` 时保持维护锁；备份损坏或无法确认服务退出会拒绝继续。保留日志和原始备份排查。首次安装中断使用同一安装器重试，已有配置/初始口令不重置。

卸载同样要求先在系统维护中准备当前版本并通过安全检查。`updates/uninstall.json` 在领取维护许可前保存卸载意图；领取后中断重试继续删除任务与服务，恢复入口不会把正在卸载的后台重新启动。SCM 确认服务实际不存在后才归档安装状态。程序文件仍被窗口占用时，NSIS 保留卸载入口和完成标记，可关闭窗口后重试。ProgramData 数据、配置和备份保留；重新启用这些数据需要明确的维护导入，不能静默覆盖。

## 可复现构建

在干净 Windows x64 构建机使用 Python 3.13、Node 24、NSIS 3.11：

```powershell
python -m pip install --require-hashes -r backend/requirements.lock -r desktop/requirements.lock
npm --prefix frontend ci
npm --prefix frontend run build
.\scripts\release\build-windows.ps1
```

PR 和 release 都调用 `.github/workflows/checks.yml`：声明/锁版本一致性、格式检查、Linux/Windows 后端与迁移、80% 覆盖门槛、桌面事务测试、Node 24 前端测试/构建、依赖审计，然后 Windows 冻结与 NSIS 构建。冻结程序会用临时数据库执行迁移，不启动 HostComm。tag、Python版本常量、package.json 和 package-lock 版本必须一致；RC 标签生成 prerelease。

Fixed WebView2 来源和 SHA-256 锁定在 `desktop/webview2.lock.json`。构建再检查微软签名和四段产品版本，缺失或不匹配直接失败。清单覆盖每个安装文件并拒绝额外文件、目录穿越及私钥。SBOM 标注 Python 冻结环境和前端构建锁的依赖清单，不能将该清单误读成逐字节确定的最小运行依赖集合。

依赖 patch/minor 分组每周更新，主版本由人工单独评估。更新 Python 声明后必须同时更新哈希锁；桌面锁采用后台 lock 作为约束。CI 的 `check_locks.py` 会拒绝声明/锁不同步。

## M5 必做验收

- 干净 zh-CN Windows 10/11 x64，断网安装，不预装 Python/Node/Evergreen WebView2；普通用户登录、关窗口、注销与跨用户切换。
- 实测 ACL：普通用户不能读 JWT/初始口令/数据库，不能改版本程序、配置、维护票据或停止服务。
- 两个启动进程/两个用户只产生一个 HostComm 网关；已配对 STM32 掉线后禁止准备维护；明确未配对的新安装可维护，但不能控制设备。
- 测试开始、停止后冷却、未知状态和 `needs_review` 阻止升级；维护与控制并发不能越过门禁。
- 空库安装、上一候选版本升级、自定义 DB 路径、中文与空格路径、低空间、迁移失败、服务停止超时、健康失败、每个持久化阶段强制断电后恢复。
- Windows Task Scheduler/SCM 故障恢复、卸载保留数据、Fixed WebView2 原生窗口，以及选定现场 LAN/TLS 策略验收。

参考：[pywebview API](https://pywebview.flowrl.com/api/)、[Microsoft Fixed Runtime 分发](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution)、[PyInstaller onedir](https://www.pyinstaller.org/en/stable/usage.html)。

## 独立软件联调工具

本轮提供单独的 `SmdBench-<SemVer>-windows-x64.zip`，自带模拟器和Chromium。它只接受干净专用测试机，完整操作安装、配对、页面实验、报告和故障恢复，并清理本轮安装。已有现场安装会被拒绝接管。命令、证据白名单和清理办法见[工具说明](../../tools/bench/README.md)。工具不随生产安装器运行，也不代替Windows桌面人工验收或固件/真机测试。发布验收以同版本的 `bench-acceptance.json` 为准。
