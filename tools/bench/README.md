# SmdBench：安装版 HostComm 验收工具

SmdBench 在**无实体设备连接的专用 Windows 10/11 x64 测试机**上，新装同一构建的 SmdHmi，完成离线配对、真实 TLS 1.3 通信、浏览器操作及报告下载。模拟器只通过 loopback 提供 HostComm 2.0；测试动作通过工具的私有 stdin/stdout 管道注入，不增加上位机测试接口。

工具包自带 Python、Playwright 和 Chromium，无须另外安装 Python 或 Node。安装器与工具的版本、提交及 SHA256 必须匹配。将发布的安装器、`manifest.json` 和 `SHA256SUMS.txt` 放在同一目录，完整解压工具 ZIP 后，在管理员 PowerShell 中执行：

```powershell
.\SmdBench\SmdBench.exe self-check
.\SmdBench\SmdBench.exe preflight --installer .\SmdHmi-0.3.0-windows-x64.exe --manifest .\manifest.json
.\SmdBench\SmdBench.exe run --installer .\SmdHmi-0.3.0-windows-x64.exe --manifest .\manifest.json --evidence C:\SmdBenchEvidence\run-001 --scenario all
```

`0.3.0` 是本轮目标版本，此处示例不表示已经发布；须替换为实际下载版本。`preflight` 只读；`run` 才创建安装与数据。证据目录必须不存在，且不得位于程序、数据、工具或私有运行目录内。`--scenario full` 运行正常实验/报告、权限及实验中的安装器忙碌拒绝，`faults` 运行配方前置与故障恢复；两者结束时都会检查离线确认和同版修复。正式工具发布验收要求 `all`，同时覆盖完整实验和故障场景。

## 数据与所有权边界

检测到已有 SmdHmi 服务、默认数据目录、产品注册、恢复任务、快捷方式或测试重装备份时，工具拒绝运行。工具仅对自己本轮创建并能确认归属的安装执行同版覆盖修复，从不接管、重置、升级或删除用户已有安装。影响通信/存储位置的环境覆盖、占用的 8000/34212 端口、关闭的 Windows Firewall 也会拒绝。遇到拒绝应更换干净测试机，不应为了让工具运行而移走现场数据。

本次安装目录为系统 ProgramFiles 下的 `SmdHmi-CI-<run_id>`；应用数据使用实际 ProgramData 下的 `SmdHmi`。工具在创建目录时写入该次运行的精确标记，并检查路径、注册、服务/任务可执行文件及重解析点。防火墙仅阻止本次服务程序访问非 loopback 地址和旧设备端口；测试端口为 `127.0.0.1:34212`。

私有运行目录在实际 ProgramData 的 `SmdBench\runs\<run_id>`，仅 Administrators/SYSTEM 可读。它保留独立模拟器数据库、清理时复制的测试数据库和诊断日志，**含敏感测试数据，不是可公开证据**。初始管理员密码由实际安装器生成；工具通过真实首次登录页面更换为随机密码。PSK、密码和 token 不进入公开输出。

浏览器使用随包完整 Chromium 的 headless 模式，日志写入同一私有目录，不能上传。`self-check` 使用独立受限临时目录，实际验证浏览器日志写入及关闭后的目录清理；运行后仍严格检查工具包内每个文件，日志不应写入冻结目录。

正常结束和失败都会尝试关闭本次模拟器/浏览器，停用并移除本次安装。中断后的清理命令只认可该运行的持久标记：

```powershell
.\SmdBench\SmdBench.exe cleanup --run-id 0123456789abcdef0123456789abcdef
```

`run_id` 使用实际命令输出值。清理遇到外来注册、服务路径或链接会停止，保留文件与网络隔离；不会猜测所有权或递归删除未认领目录。私有证据目录会保留供维护者处理。

## 验收与证据

`acceptance.json` 包含版本/提交、安装器校验值、场景和清理结果；只有实际断言全部通过且清理完成时，`status` 才为 `passed`。退出码非零或 `cleanup_complete=false` 都不能算通过。失败结果的 `failure_frames` / `cleanup_failure_frames` 仅公开允许的代码文件名、函数名和行号，不含异常消息、源码行、局部变量或完整路径。完整异常保存在受限运行目录的 `failure-*.traceback.txt`；停止本次服务后，其 `service.log` / `updater.log` 及固定轮转文件先复制到私有 `installation-logs-*` 目录，再清理应用数据。复制失败会保留原数据和网络隔离，不会把诊断缺失当成清理成功。

失败时的 `service_shutdown`、`cleanup_service_shutdown` 只从本轮已归属服务日志末尾最多512KiB提取固定阶段代码，用于区分请求排空与应用退出。缺失或无法确认归属时记录不可用；不输出原始日志、PID、请求或路径。该尾部观察可能不完整，不能替代当前SCM状态或作为停止、清理成功的依据。

公开证据只包含这个结果、固定场景脱敏日志、操作后的截图及合成实验 HTML/PDF/XLSX 报告。不得另行收集数据库、服务配置、PSK、浏览器存储、HAR、cookie 或原始服务日志上传。报告使用独立固定数值核对 H600、T10/T40、Ts、首滴事件 Td、无滴落与缺失数据分支，并保留 `not_certified` 与模拟来源。

正常场景使用真实 UI 创建标准候选和非标版本、激活回读、启动、停止、结束确认、日志补传与下载；故障场景覆盖错误 PSK、指定命令回执丢失、配方传输中断、板端重启及陌生运行核查回放。JSONL 的每次注入有请求 ID 和返回的板端状态/样本引用；它不能执行自由 Python、HTTP 或任意脚本。

`all` 的维护场景再次运行本轮实际安装器：测定及冷却中分别确认拒绝；设备离线时先确认未获现场停机确认的静默安装返回 `20`，随后为专用惰性测试环境提供确认并完成同版修复。修复前后检查账户、配置与配对密钥、已有实验/采样数量和实际报告内容保留，页面不再提供目标版本输入框。不会用这些同版结果代替 rc.4/rc.5 的旧版升级验收。

两项实际结果分别保存为 `installer-busy-rejected.json` 和 `installer-offline-confirmation.json`，对应 `acceptance.json` 中的 `busy_rejected`、`offline_confirmation` 断言。日志绑定本轮运行编号、版本、提交、CI 编号（在 CI 中）及安装器摘要；断言引用实际日志的 SHA256。软件正式版流水线将它们与独立 Windows 旧版升级套件的七项证据按[发布证据契约](../../docs/release-acceptance.md)聚合；本轮执行进度见[覆盖安装验证记录](../../docs/verification/2026-09-13-overwrite-install.md)。

测定结束、置换完成和冷却完成由明确的惰性设备边界动作注入，不加速心跳或租约时钟。这些结果验证软件链路、权限与数据依据，**不证明实体传感器、加热/供气输出、实际热过程持续时间、Windows 显示缩放、实物安全联锁或国标符合性**。Windows 安装版验收以对应提交的 CI/现场证据为准；开发机源码浏览器检查不能替代。

开发者可运行 `python -m pytest tools/bench/tests`。工具与固件契约依赖分别冻结在本目录依赖锁和后端协议包；构建由 [`freeze.py`](freeze.py) 与仓库发布流水线完成。
