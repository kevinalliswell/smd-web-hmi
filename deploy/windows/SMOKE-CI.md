# Windows 安装冒烟的执行边界

`scripts/release/smoke-windows.ps1` 在本轮 NSIS 生成后运行，验证真实安装器、SCM LocalService 与生产后台启动。它只接受提升权限的 Windows x64 PowerShell 7 和 `GITHUB_ACTIONS=true`、`RUNNER_ENVIRONMENT=github-hosted`；没有跳过这些限制的开关。不能在开发机、现场机或自托管 runner 执行，也不能把手工设置这两个环境变量当作隔离。

由包装 job 在同一个一次性 runner 上调用，例如：

```powershell
$Version = (python scripts/release/metadata.py).Trim()
./scripts/release/smoke-windows.ps1 `
  -Installer "artifacts/SmdHmi-$Version-windows-x64.exe" `
  -Manifest artifacts/manifest.json `
  -Evidence artifacts/windows-install-smoke.json
```

脚本不改构建或 workflow；调用方应将 `windows-install-smoke.json` 作为始终收集的诊断产物。若包装未完成，不应调用安装冒烟。

安装前检查 `SmdHmi` 服务、`SmdHmi-Recover` 任务、默认 ProgramData/SmdHmi、默认产品目录、注册表、开始菜单和 8000 监听均不存在。拒绝 `SMD_DATA_ROOT` 的进程/用户/机器覆盖。测试在 Program Files 下随机目录以及默认 ProgramData 中创建归属标记，再运行本轮安装器；不接管已有安装。

测试为该版本的精确 `SmdService.exe` 路径添加临时出站阻断，覆盖默认 HostComm TCP 34211 端口；防火墙各 profile 原本必须启用。先验证未配对安装，再通过随包离线配对 CLI 写入随机测试设备身份及凭据。服务通过生产读取器验证密钥，脚本只读取不含密钥的成功事件；不启用 Mock、不登录、不发设备命令。固定程序路径、端口与当前安装器默认配置共同构成隔离条件；未来若修改 HostComm 默认端口，必须同步审查此脚本。

验证包括：

- 安装器退出码为 0，实际服务路径属于本次随机目录，账户为 `NT AUTHORITY\LocalService` 且正在运行。
- 安装后清单的版本、提交和 schema 目标与本轮构建清单一致。
- 无认证 GET `/api/system/health` 返回同版本 ready，database/schema/storage/backup 均为 ok，HostComm 必须 offline。
- 静态页和它引用的实际 `/assets/` JavaScript 资源均能返回，避免把 SPA fallback HTML 当作成功脚本。
- 停止服务后，已安装的 `SmdUpdate --pair-device` 成功执行；重启原 LocalService 后，出现与本次随机设备 ID 匹配的 `v2.tls_credentials_loaded` 事件，证明服务身份完成所有者、DACL、密钥格式及 TLS context 加载。HostComm 仍须 offline，证据明确标注未验证设备握手。

安装进程默认最多 360 秒（允许 30–900 秒），配对 CLI 和每次健康检查最多 60 秒，单个 HTTP 请求有 3/5 秒期限。finally 对本次安装及配对进程树、任务、服务、注册表、开始菜单、防火墙规则和带归属标记的测试目录做有限清理。服务和任务必须通过精确可执行文件路径校验；目录拒绝未知归属和重解析点。服务或测试进程仍存在时保留设备阻断且不删除目录，记录清理失败并使 CI 失败。测试异常正文、环境变量、配置内容、密钥、初始口令和 JWT 不进入 JSON 证据。

这是一次性测试机器的清理，不调用产品卸载器，也不证明现场维护/卸载门禁已验收。它不覆盖桌面窗口交互、设备连接、工艺流程、升级回滚、断电恢复、真实无 Python 环境或断网安装；这些仍需要独立验收记录。只有实际 Windows job 的成功结果可以写作“安装冒烟通过”；其他平台的静态检查或跳过不能代替。

拒绝路径测试在 `desktop/tests/test_windows_smoke_guard.py`，现有 PowerShell 语法检查会自动扫描新增脚本。实际首次安装路径由包装后的调用验证。

官方依据：[GitHub runner 变量](https://docs.github.com/en/actions/reference/workflows-and-actions/variables)、[NSIS /S 与末尾不加引号的 /D 参数](https://nsis.sourceforge.io/Docs/Chapter3.html)、[Windows 程序/端口防火墙规则](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule?view=windowsserver2025-ps)。
