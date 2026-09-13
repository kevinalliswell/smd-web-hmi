# Windows 桌面与本机 Web 交付

交付物为 `SmdHmi-<SemVer>-windows-x64.exe`，配套 `SHA256SUMS.txt`、`manifest.json`、`sbom.cdx.json`。目标是 Windows 10/11 x64，系统需要 .NET Framework 4.8。CPython 3.13、Python 依赖和固定 WebView2 随版本打包，目标机无需安装 Python、Node 或在线下载运行时。0.3.0 的软件正式发布要求实际 Windows CI 安装升级及 SmdBench 验收；Win10/11 原生桌面和设备组合另行验收，不能把 macOS 单元测试视为安装成功证明。

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

默认仅监听本机。局域网接入复用同一 FastAPI 服务、账户和 WebSocket，不能另起第二个后台。先确认设备已安全停机并停止后台服务，再由管理员在 service.env 设置 SMD_HOST=0.0.0.0 和绝对路径 SMD_TLS_CERTFILE、SMD_TLS_KEYFILE，证书及私钥放在受限 config 目录，客户端必须信任证书且证书包含实际访问主机名及 localhost。相应把 client.json 改为 https://localhost:8000，再重启服务并只对现场子网放行防火墙 TCP 8000。服务拒绝无 TLS 的非本机监听，禁止跳过证书校验。安装器不会自动修改防火墙或安装另一套 Web 服务器。

## 人工离线升级

0.3.0 起采用覆盖安装与同版修复。

1. 下载与校验新安装器，关闭桌面窗口，以 Windows 管理员运行。无需卸载、登录旧版、填写版本号或提前准备维护。
2. 安装器识别原程序和数据目录，自动读取包内版本。设备明确正在实验、安全处置或冷却时拒绝安装。设备离线或状态未知时，管理员在现场确认已经停止加热、供气和运动、无需继续采集后，才能继续。这个确认不会把未知实验标记为已完成。
3. 安装器预检空间，将完整程序仅暂存一次；自动进入维护，等待服务进程完全退出，备份实际配置的数据库及配置，执行迁移并检查新程序、页面和存储。
4. 完成后从开始菜单打开原软件。账户密码、网络、配对密钥、配方、实验和报告保留。配置新增默认值不会覆盖已有值；旧1.0设备配置不会自动改为2.0。

再次运行同一个版本、同一个发布构建的安装器可修复损坏的程序。不同构建不得复用同一版本号；普通安装不允许降级。安装器保留当前与上一回退程序版本及未完成事务引用的程序，**不自动删除升级数据库和配置备份**。备份占用空间不足时会说明缺额，不能通过降低数据库空间门槛强行安装。

系统设置中的“版本与维护状态”仅用于查询。旧的准备、取消和领取维护 API 返回410，提示通过安装器操作。维护请求只在本机受保护文件间传递，不新增网络授权端口。

## 失败与断电恢复

升级失败时自动恢复对应旧程序、数据库和配置，检查成功后解除维护。重新运行安装器会识别未完成事务。针对rc.4/rc.5遗留的 `rollback_failed`，先额外保存当前数据库和配置，再提示确认恢复旧快照；快照之后的数据保留在额外备份中，不会自动合并。只差旧版健康检查时，重试不会重复覆盖已恢复的数据库。

正常恢复不需要 PowerShell。再次运行安装器仍失败时，向维护人员提供日志末尾错误，保留 `updates` 下的事务和备份；不要手工删除维护锁或仅根据旧1073日志重复删除服务。默认诊断命令：

```powershell
Get-Content -LiteralPath 'C:\ProgramData\SmdHmi\logs\updater.log' -Tail 80 -Encoding UTF8
```

日志使用UTC时间，健康失败会列明检查阶段及数据库/schema/存储/备份/前端原因，设备 `hostcomm=offline` 本身不影响应用就绪。使用自定义数据目录时读取该目录下的日志。不要发送完整配置、密码、PSK或完整数据库。

卸载也由 Windows 管理员直接运行，不填写版本号；卸载默认保留数据库、配置、报告和备份。只有明确运行卸载程序才继续未完成卸载，安装不会代为删除服务。保留资料的重新安装须匹配原安装器保存的卸载归属证据；没有归属证据的陌生配置不会被接管。

旧rc测试机的空库重装属于历史专用流程，见[测试机备份重装](TEST-REINSTALL.md)，不再作为日常升级方法。

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

## Windows 现场与设备组合验收

- 干净 zh-CN Windows 10/11 x64，断网安装，不预装 Python/Node/Evergreen WebView2；普通用户登录、关窗口、注销与跨用户切换。
- 实测 ACL：普通用户不能读 JWT/初始口令/数据库，不能改版本程序、配置、维护票据或停止服务。
- 两个启动进程/两个用户只产生一个 HostComm 网关；已配对 STM32 掉线后需要现场停机确认；明确未配对的新安装可维护，但不能控制设备。
- 实验及停止后的冷却明确忙碌时拒绝升级；未知状态需管理员确认且保留待核查记录，维护与控制并发不能越过门禁。
- 空库安装、上一候选版本升级、自定义 DB 路径、中文与空格路径、低空间、迁移失败、服务停止超时、健康失败、每个持久化阶段强制断电后恢复。
- Windows Task Scheduler/SCM 故障恢复、卸载保留数据、Fixed WebView2 原生窗口，以及选定现场 LAN/TLS 策略验收。

参考：[pywebview API](https://pywebview.flowrl.com/api/)、[Microsoft Fixed Runtime 分发](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution)、[PyInstaller onedir](https://www.pyinstaller.org/en/stable/usage.html)。

## 独立软件联调工具

本轮提供单独的 `SmdBench-<SemVer>-windows-x64.zip`，自带模拟器和Chromium。它只接受干净专用测试机，完整操作安装、配对、页面实验、报告和故障恢复，并清理本轮安装。已有现场安装会被拒绝接管。命令、证据白名单和清理办法见[工具说明](../../tools/bench/README.md)。工具不随生产安装器运行，也不代替Windows桌面人工验收或固件/真机测试。发布验收以同版本的 `bench-acceptance.json` 为准。
