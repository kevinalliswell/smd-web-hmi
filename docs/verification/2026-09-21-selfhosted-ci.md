# 自托管 CI 接入验证（macOS ARM64 + Windows x64）

绑定提交:`3f90ef01bcd6b2a9478d7531c63c50aa8a87c77d`(PR #84 分支 `claude/nifty-dirac-ipu0bx`)
证据 run:[35530682712](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/35530682712)
运行器:`kevin-mac-smd`(macOS ARM64)、`ustb-win-smd`(Windows Server 2019 x64,NetworkService 非管理员服务)

## 结果(6/6 自托管 job 真实通过)

| Job | 结论 | 关键数字 |
|---|---|---|
| lint-mac / audit-mac / frontend-mac / backend-mac | 全绿 | 与托管同命令全量执行 |
| backend-win-local | 绿(7 分 56 秒) | 桌面 396 过/35 跳过;bench 92 过/14 跳过;后端 816 过/45 跳过;覆盖率 86.65% ≥ 80%;PowerShell 语法过 |
| windows-build-local | 绿(22 分 53 秒) | 全链构建并产出安装器(见下) |

产物留存(Actions 工件配额受限时的服务器留存点):
`D:\CodexCI\runners\smd\_work\smd-retained-artifacts\run-35530682712-1`

| 文件 | 字节 | SHA256 |
|---|---|---|
| SmdHmi-0.3.0-windows-x64.exe | 339455061 | `eee2fc5dda06bd13d2b144af04056b0b4224dd35b1ab530b1d47da9f6d13087c` |
| manifest.json | 195898 | `6afc38d9393e1303afa9e93a9428bc7b1f8b5882b1bf63ea5fce9d7da0a2fd77` |
| sbom.cdx.json | 121517 | `dc3d420592aed66df7c601482cd23734e112201d08f7a47f153f6bf7a3089631` |
| SHA256SUMS.txt | 96 | `a9541747a1539e6a3313a35b4c62edefff6c1f2660552df1eeafad2a8afbb15a` |

## 供给设计(ustb-win 网络与权限约束下的实测方案)

- 该服务器网络对 `objects.githubusercontent.com`、`www.python.org` 建连后响应体挂死(实测两轮);
  PowerShell 5.1 `Invoke-WebRequest -TimeoutSec` 不覆盖响应体挂死,下载一律用系统自带
  `curl.exe --max-time` 硬性墙钟超时,镜像优先。
- CPython 3.13.15:python-build-standalone `install_only_stripped` 免安装归档,SHA256 与
  工作流内固定值一致方可解包(npmmirror 与 GitHub 双独立源核验一致);官方 python.org
  安装器在 NetworkService 服务会话报 0x80070005,MSI 每用户安装不适用于服务账户,弃用。
- NSIS 3.11:官方 zip 随仓库下发(`third_party/nsis/`,双源核验,见其 README),使用前重校
  SHA256,解包到 runner 自有目录,经 `SMD_MAKENSIS` 传入构建脚本;不做系统级安装。
- runner 无 git ≥2.18,checkout 走 REST 归档(无 `.git`):构建元数据统一"git 优先,回退
  CI 注入的 `GITHUB_SHA`(同一棵树)"。
- NetworkService 档案 TEMP 基路径过长会突破 MAX_PATH:两个 Windows job 将 TMP/TEMP 指到
  runner `_temp` 短路径(经步骤写 GITHUB_ENV;runner 上下文不允许用于 job 级 env)。
- 工作流 PowerShell 脚本体保持纯 ASCII:runner 写无 BOM UTF-8 临时 .ps1,PS 5.1 按 ANSI
  码页读取,中文文案经 cp1252 误读会产生弯引号定界符导致 ParserError(实测)。

## 未执行(该 runner 能力受限,均显式记录,验收口径不变)

四类特权能力在非管理员 NetworkService 下不可为,相关用例以生产入口实探 + skipif,
托管 Windows(管理员)与 POSIX 通道照常全量执行:

1. 把文件所有者设为 Administrators(配对密钥 `_private_fd`、重置备份 Set-Acl、bench
   `secure_directory` acl_set;含 SmdBench 运行时自检——工作流按探测有特权则完整自检,
   无特权则 step summary 记录 NOT EXECUTED,仍做非特权 bundle 清单校验);
2. 创建符号链接(SeCreateSymbolicLink);
3. 注册 LocalService 计划任务(网关互斥用例,skipif 由环境变量近似改为 IsUserAnAdmin 实探);
4. 保护 DACL 下的原子替换(本地维护桥 `write_protected_json`)。

windows-package 与安装/覆盖/卸载验收按项目隔离硬门禁(`RUNNER_ENVIRONMENT=github-hosted`)
仅在托管执行,自托管不越权模拟。

## 当时受阻(非代码问题)

- 托管 7 job 因账号 Actions 分钟数额度耗尽秒败(2–4 秒无日志);`upload-artifact` 报
  "Artifact storage quota has been hit"(2026-09-20T19:19Z),故产物走服务器留存。
  额度恢复后的托管结果以其后的 Actions run 为准。
- Codex 真机验收清单(PR #84 描述 7 项)另行留证。
