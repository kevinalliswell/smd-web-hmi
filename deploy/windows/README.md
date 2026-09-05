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

桌面界面不提供 Python JS bridge，也不拥有服务停止能力。关窗口、退出登录、切换 Windows 用户都不会结束后台。设备控制仍由同一个后台网关处理。系统级恢复任务 `SmdHmi-Recover` 使用 SYSTEM，负责开机时检查未完成升级并回退；正常后台一直使用受限服务账户。

## 安装与日常使用

1. 在可信构建渠道核对安装器 SHA-256；以管理员运行安装器。
2. 由管理员在 `config/service.env` 设置实际 STM32 IP、端口。默认 `HOSTCOMM_MOCK=false`；状态离线不会被伪装为就绪设备。
3. 初始 `admin` 口令在受限文件 `config/bootstrap-admin-password.txt`。首次登录必须改密。
4. 普通用户从开始菜单打开 **SMD HMI**，或用本机浏览器访问 `http://127.0.0.1:8000`。后台健康不等于硬件实验许可：设备在线、新鲜状态和安全条件单独决定是否可操作。

默认仅监听本机。局域网接入复用同一 FastAPI 服务、账户和 WebSocket，不能另起第二个后台。先在设备空闲且无未闭合实验时准备维护；由管理员在 service.env 设置 SMD_HOST=0.0.0.0 和绝对路径 SMD_TLS_CERTFILE、SMD_TLS_KEYFILE，证书及私钥放在受限 config 目录，客户端必须信任证书且证书包含实际访问主机名及 localhost。相应把 client.json 改为 https://localhost:8000，再重启服务并只对现场子网放行防火墙 TCP 8000。服务拒绝无 TLS 的非本机监听，禁止跳过证书校验。安装器不会自动修改防火墙或安装另一套 Web 服务器。

## 人工离线升级

1. 以应用管理员进入系统维护，填写包内版本（例如 `0.4.0-rc.1`）并准备升级。后台必须获得新鲜、在线、明确空闲的设备状态，且数据库中不存在 `end_time IS NULL` 的实验。冷却中、状态未知和待核查实验均拒绝。
2. 把经过校验的新安装器带到本机，以 Windows 管理员运行。安装器用本机受限票据再次检查条件。准备票据十分钟内有效；领取后不允许网页取消。
3. 升级器先验证所有文件、版本与固定运行时，再建立独立版本目录。确认旧服务已停止后，用 SQLite backup API 备份**旧服务实际配置的数据库**和 config，记录摘要，运行新版本随附迁移。
4. 切换服务路径并检查新版本、数据库/schema、存储、备份和前端 HTML。成功才提交事务并解锁。旧版本程序和全部依赖保留。

网页准备后尚未领取，可由应用管理员取消。领取后发生失败必须通过本机恢复；不能手工删除维护文件。升级期间不接收实验控制与改参。

## 失败与断电恢复

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
- 两个启动进程/两个用户只产生一个 HostComm 网关；真实 STM32 掉线后禁止准备维护。
- 测试开始、停止后冷却、未知状态和 `needs_review` 阻止升级；维护与控制并发不能越过门禁。
- 空库安装、上一候选版本升级、自定义 DB 路径、中文与空格路径、低空间、迁移失败、服务停止超时、健康失败、每个持久化阶段强制断电后恢复。
- Windows Task Scheduler/SCM 故障恢复、卸载保留数据、Fixed WebView2 原生窗口，以及选定现场 LAN/TLS 策略验收。

参考：[pywebview API](https://pywebview.flowrl.com/api/)、[Microsoft Fixed Runtime 分发](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution)、[PyInstaller onedir](https://www.pyinstaller.org/en/stable/usage.html)。
