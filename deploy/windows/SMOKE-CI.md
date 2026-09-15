# Windows 安装冒烟的执行边界

`scripts/release/smoke-windows.ps1` 在本轮 NSIS 生成后运行，验证真实安装器、SCM LocalService 与生产后台启动。它只接受提升权限的 Windows x64 PowerShell 7 和 `GITHUB_ACTIONS=true`、`RUNNER_ENVIRONMENT=github-hosted`；没有跳过这些限制的开关。不能在开发机、现场机或自托管 runner 执行，也不能把手工设置这两个环境变量当作隔离。

`windows-build` 负责冻结、编译和工具自检；`windows-package` 在另一台干净的一次性runner上验证同一提交的产物。它按成功构建输出的实际artifact ID下载候选，核对来源与摘要并验证完整bench清单后，执行安装冒烟。构建未成功时，最终必需检查明确失败。联调工具传递包含浏览器所需隐藏文件；构建暂存数据库、配置和解包目录不进入候选。

安装验收job调用示例：

```powershell
$Version = (python scripts/release/metadata.py).Trim()
./scripts/release/smoke-windows.ps1 `
  -Installer "artifacts/SmdHmi-$Version-windows-x64.exe" `
  -Manifest artifacts/manifest.json `
  -Evidence artifacts/windows-install-smoke.json
```

脚本不改构建或 workflow；调用方应将 `windows-install-smoke.json` 作为始终收集的诊断产物。若包装未完成，不应调用安装冒烟。

安装前检查 `SmdHmi` 服务、`SmdHmi-Recover` 任务、默认 ProgramData/SmdHmi、默认产品目录、注册表、开始菜单和 8000 监听均不存在。拒绝 `SMD_DATA_ROOT` 的进程/用户/机器覆盖。测试在 Program Files 下随机目录以及默认 ProgramData 中创建归属标记，再运行本轮安装器；不接管已有安装。

测试为该版本的精确 `SmdService.exe` 路径添加临时出站阻断，覆盖默认 HostComm TCP 34211 端口；防火墙各 profile 原本必须启用。先验证未配对安装及测试机重装，再通过随包离线配对 CLI 写入随机测试设备身份及凭据。重装测试通过本机 API 登录、修改测试初始口令并创建一次性测试账户；不启用 Mock、不发设备命令。服务通过生产读取器验证密钥，脚本只读取不含密钥的成功事件。固定程序路径、端口与当前安装器默认配置共同构成隔离条件；未来若修改 HostComm 默认端口，必须同步审查此脚本。

验证包括：

- 安装器退出码为 0，实际服务路径属于本次随机目录，账户为 `NT AUTHORITY\LocalService` 且正在运行。
- 安装后清单的版本、提交和 schema 目标与本轮构建清单一致。
- 无认证 GET `/api/system/health` 返回同版本 ready，database/schema/storage/backup 均为 ok，HostComm 必须 offline。
- 静态页和它引用的实际 `/assets/` JavaScript 资源均能返回，避免把 SPA fallback HTML 当作成功脚本。
- 再次运行同一构建的 NSIS 安装器执行修复：无须先登录应用或填写目标版本，退出码必须为 `0`；精确路径对应的服务恢复运行，配置摘要保持一致，不残留维护票据。该场景不要求服务 PID 不变；配置内容与摘要不进入诊断 JSON。
- `test-reset-smoke.ps1` 在该防火墙隔离实例中将测试配置设为 1.0，验证已停用的准备接口返回 HTTP 410 且不创建维护票据；这只是 CI 夹具，不是现场修改协议的操作步骤。实际使用 Windows PowerShell 5.1 运行测试机工具，验证只读预览不修改旧服务/配置/备份目录，执行后完整备份保留旧配置和测试账户，再运行同一安装器创建空库。新口令须变化、旧测试账户仅存在于归档数据库、新配置恢复未配对 2.0。`test_reset` 只记录校验结论与工具/清单摘要，不上传私有备份。
- 停止服务后，已安装的 `SmdUpdate --pair-device` 成功执行；重启原 LocalService 后，出现与本次随机设备 ID 匹配的 `v2.tls_credentials_loaded` 事件，证明服务身份完成所有者、DACL、密钥格式及 TLS context 加载。HostComm 仍须 offline，证据明确标注未验证设备握手。

`test_reset_progress` 记录重置子进程的退出等待耗时，以及确认子进程退出、验证归档归属之后观察到的阶段。备份过程另记录13个固定子步骤及已完成步骤的单调耗时；只投影顺序合法、无重复、有限非负的计时值，失效记录不进入证据。当前步骤只是最后持久记录，不能单凭它认定主要耗时点。原始 `stage.json`、路径和私有归档不会上传；诊断失败不替代业务错误或更改清理条件。

安装进程默认最多 360 秒（允许 30–900 秒），重置工具最多 600 秒，配对 CLI、预览和每次健康检查最多 60 秒，单个 HTTP 请求有 3/5 秒期限。finally 对本次安装、重置及配对进程树、任务、服务、注册表、开始菜单、防火墙规则和带归属标记的测试目录做有限清理，包括测试归档。服务和任务必须通过精确可执行文件路径校验；目录拒绝未知归属和重解析点。服务或测试进程仍存在时保留设备阻断且不删除目录，记录清理失败并使 CI 失败。测试异常正文、环境变量、配置内容、密钥、初始口令和 JWT 不进入 JSON 证据。

这是一次性测试机器的清理，不调用产品卸载器，也不证明现场维护/卸载门禁已验收。它不覆盖桌面窗口交互、设备连接、工艺流程、升级回滚、断电恢复、真实无 Python 环境或断网安装；这些仍需要独立验收记录。只有实际 Windows job 的成功结果可以写作“安装冒烟通过”；其他平台的静态检查或跳过不能代替。

拒绝路径测试在 `desktop/tests/test_windows_smoke_guard.py`，现有 PowerShell 语法检查会自动扫描新增脚本。实际首次安装路径由包装后的调用验证。

官方依据：[GitHub runner 变量](https://docs.github.com/en/actions/reference/workflows-and-actions/variables)、[NSIS /S 与末尾不加引号的 /D 参数](https://nsis.sourceforge.io/Docs/Chapter3.html)、[Windows 程序/端口防火墙规则](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule?view=windowsserver2025-ps)。
