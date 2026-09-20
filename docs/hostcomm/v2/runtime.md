# HostComm 2.0 上位机运行、离线配对与升级

文档修订：`2.0-doc.4`（2026-09-20）；适用于已发布 SmdHmi `0.3.0`，设计基线 `2.0-design.1`、线协议 `2.0` 不变。上位机运行、配对及模拟器已有软件实现；STM32 固件开发中、尚未交付验收。用户确认 0.3.0 安装成功仅证明该次安装结果；配对、真实板卡 TLS、完整实验及 24 小时验收仍须分别留证。交底会议与首次板端联调见[技术交底](technical-briefing.md)；语义依据为 [wire.md](wire.md)、[state-and-recovery.md](state-and-recovery.md) 和 [data-and-recipe.md](data-and-recipe.md)。

## 1. 共用后台与配置

桌面壳和浏览器均连接唯一后台；只有后台持有 STM32 通信连接。关闭桌面窗口不停止 Windows Service。安装器首次安装默认选择协议 2.0；已有安装升级保留 `ProgramData\SmdHmi\config\service.env`，不会自动替换配对身份、密钥或旧协议配置。

未配对时后台仍可提供登录、历史查询和维护；设备显示离线且拒绝控制。不得把连接失败改成旧协议或模拟模式。初始管理员登录口令与下述设备 PSK 是不同凭据。

| 配置 | 用途 |
|---|---|
| `PROTOCOL_VERSION=2.0` | 明确启用 v2 适配器 |
| `HOSTCOMM_MOCK=false` | 真实网络连接；正式安装不使用内置模拟凭据 |
| `HOSTCOMM_HOST` / `HOSTCOMM_PORT` | 工控板网络地址和端口 |
| `HOSTCOMM_DEVICE_ID` | 已登记控制板身份，32 位小写 UUID 十六进制 |
| `HOSTCOMM_CONTROLLER_ID` / `HOSTCOMM_CONTROLLER_EPOCH` | 本后台身份和防重放代际，与实际数据库一致 |
| `HOSTCOMM_PSK_FILE` | 受限文件绝对路径；32 个随机字节以 64 位十六进制编码保存 |

生产连接要求 Python 3.13 的 TLS-PSK 能力、TLS 1.3、P-256 和协商的 AES-128-GCM；不具备能力、身份不符或密钥权限过宽均保持不可控制。源码 Web 部署采用相同配置和数据库迁移；不能通过前端配置注入设备密钥。局域网浏览器 HTTPS 与板端 TLS-PSK 分别配置。

## 2. Windows 离线配对工具

已安装版本的 `SmdUpdate.exe` 提供配对入口。必须在管理员终端执行，并先将设备按工程批准流程安全关闭、停止 `SmdHmi` 服务。工具不会代替设备安全处置，也不会自动重启服务。

工具实际检查 SCM 已停止，并持有 `SmdHmi.Updater` / `SmdHmi.Backend` 系统互斥锁；读取 `service.env` 指定的真实 SQLite，拒绝未闭合试验、v2 缺少安全完成依据的试验，以及任意在途或未知命令。数据库未迁移到包含 v2 持久表的版本也拒绝执行。不能用文本理由或一个安全确认布尔绕过这些检查。

已完成的 v2 命令按持久身份、请求摘要和板端终态判断，即使上位机受理记录仍显示 `accepted`，也不会永久阻止更换密钥。明确被板端拒绝且从未开始的启动，须同时匹配实验绑定、启动请求和拒绝结果，并且没有采样、运行状态或边界证据；此时不要求补造安全完成时间。只有文字状态、没有匹配终态的旧受理记录，以及仍为未知的结果均继续阻止离线配对；在线核查标记不会改变未知结果的性质。

在下列命令中用当前安装版本和设备登记值替换占位值：

```powershell
$PairingTool = "C:\Program Files\SmdHmi\versions\<已安装版本>\SmdUpdate\SmdUpdate.exe"
Stop-Service SmdHmi
& $PairingTool --install "C:\Program Files\SmdHmi" --pair-device "<设备32位小写UUID>" --reason "首次离线配对"
```

首次配置可省略 `--controller-id` 和 `--controller-epoch`；已有数据库中的同设备身份会被保留。需要导入已在线下分配的密钥时使用 `--import-psk <私有密钥文件路径>`，不在命令行放置密钥正文。该文件须符合运行时权限检查；工具不会修改原导入文件。

生成结果位于受保护的 `config\pairings\<事务ID>\`：

- `firmware-pairing.json`：协议、三项身份、TLS 身份、控制角色和密钥文件名；不含密钥正文。
- `controller-psk.hex`：本次私有密钥。固件离线导入需要它与上述元数据一致。
- `previous-service.env`、`previous-database.sqlite`，以及已有密钥时的 `previous-psk.hex`：保留变更前的恢复依据。
- `next-service.env`：本次冻结的目标配置。不得在事务执行期间编辑。

程序只输出身份和受保护路径，不输出 PSK，不通过 HTTP 或浏览器提供配对材料。文件继承安装器限制的 ProgramData ACL；写入秘密前先校验继承权限。离线向固件交接只需本次元数据及密钥文件，不应复制含用户、实验和 JWT 的备份目录。固件侧导入与本地物理维护授权仍须由后续固件实现并验收。

## 3. 更换密钥、设备或控制器

已有配对必须显式使用 `--replace-pairing`，并填写维护理由。默认保留同设备 `controller_id`、`controller_epoch` 和完整 `last_seq`，包括超过 SQLite 有符号整数范围的水位；换密钥不会自动清零序号。

只有明确指定 `--controller-epoch` 才更换既有代际。更换 `controller_id` 时必须同时明确指定新的 epoch；新代际须在线下与板端同步。更换设备同样执行完整停机及数据库检查。历史操作、源日志和配方/运行绑定保留，旧密钥文件不删除；新 epoch 的序号从 0 开始不会改写旧 epoch 的操作历史。

配对成功只表示本机身份、数据库和密钥材料一致。完成板端导入后才手动启动服务，核对握手、设备身份、能力、批准的工程配置、状态及操作水位。未通过工程配置批准时不能启动实验。

## 4. 中断恢复与版本升级

配对事务保存 `updates\pairing.json`，修改数据库前写入 `maintenance.json` 维护锁。恢复时继续使用已暂存的身份、密钥和配置，不重新生成密钥；阶段间断电不会授权后台发送普通控制命令。

```powershell
& $PairingTool --install "C:\Program Files\SmdHmi" --recover-pairing
```

恢复仍要求服务已停止和进程锁可获取。配置、密钥摘要或数据库身份与冻结事务冲突时保持维护锁并报错，不能通过删除日志/数据库或手改 epoch 放行。该入口完成配对事务；安装升级恢复仍使用原有 `--recover`，不得混用。

离线软件升级沿用版本独立目录、实际数据库和配置备份、Alembic 迁移、切换验活与失败回退流程。新增 v2 表和采样源字段以追加迁移保留旧记录；旧采样缺少源身份时保持 NULL，不补造源顺序或完整性。配对目录属于配置备份范围，升级包不得内置现场密钥。

## 5. 操作核查与源日志恢复

“操作记录”保存命令、时间、操作 ID 和执行证据。响应超时后先查询控制板；查询不会重发原命令。前置租约也有与原请求绑定的持久身份，租约回执丢失时仍能从该记录查询。租约核查完成但主命令未发送时，页面明确显示这一结果。

维护人员可填写依据核查已经无法查询的结果。后台重新读取认证状态并校验安全条件后才接受核查；历史结果仍为未知，不补写“执行成功”。核查标记持久保存，刷新页面后仍有效；后续动作需要用户重新明确确认，并产生新的操作 ID。

连接恢复及测定/安全完成后自动补传源日志。“操作记录”提供管理员的“设备源日志补传”入口，采用后台任务，避免阻塞命令回执。`POST /api/system/maintenance/source-logs` 可指定十进制 uint64 字符串 `first_record_seq` 重新扫描；通过返回的 `task_id` 查询 `/api/system/maintenance/source-logs/{task_id}`。

“扫描完成”只表示本次日志传输完成。扫描水位可越过板端已确认丢失的区间，但验证水位只覆盖连续且已验证的记录；报告仍根据源边界、缺口、质量和首滴事件独立判定完整性。重新扫描不删除既有缺口证据、不覆盖原始记录，也不刷新实时控制状态。

## 6. 本地验证与待验边界

操作测试覆盖先持久化再发送、并发序号、未知结果、历史结果不可回退、租约读回；日志测试覆盖原始字节去重、分块落盘后 ACK、终态摘要校验和事务回滚；归档测试覆盖源边界、全局报警、固定修订对账及补传不回退当前报警。桌面配对测试使用真实临时 SQLite 验证停机/互斥、未闭合状态拒绝、替换水位保留、旧材料保留及中断恢复。

这些测试不代替 Windows SCM/DACL、打包程序、断网安装、固件 TLS 栈、真实工程配置、安全联锁和全程实验验收。正式发布证据应分开记录本机软件测试、模拟器测试、Windows 验收和指定硬件/固件组合的实际结果。

## 7. 独立Windows联调工具

独立[SmdBench](../../../tools/bench/README.md)随0.3.0提供工具ZIP，内含Python、Playwright/Chromium和TLS模拟器。先在全新测试VM中运行preflight，再使用与工具同版本同提交的安装器执行run。工具拒绝已有SmdHmi安装/服务/数据，使用私有模拟器目录与凭据；不要求安装开发环境。生产安装包不内置模拟器，现场设备资料不交给该工具。

0.3.0已通过Windows CI中的冻结自检、实际安装版TLS/Chromium固定流程、报告核验及归属清理，实际发行资产核验见[正式版验证](../../verification/2026-09-13-overwrite-install.md)。结论只适用于`acceptance.json`绑定的提交、安装器和工具摘要；rc.5的历史证据见[安装版闭环记录](../../verification/2026-09-08-installed-hostcomm-loop.md)。工具只能接入回环模拟器，不能直接用于真实板卡联调；Win10/11 WebView2人工验收和真实STM32联调仍须分别完成。轻量离线技术资料包不包含SmdBench二进制或上述运行环境。

## 8. 源码开发方式的同机TLS模拟器

保留下面的源码CLI供协议开发人员手动诊断；此方式需源码及Python3.13，日常独立工具使用上节ZIP。以下命令不是SmdBench自动验收结果，完整组合是否通过须查对应证据。

模拟器始终禁止实体 I/O，明文与 TLS 模式都只允许 loopback 地址。`--storage` 必填，使用独立模拟器 SQLite，绝不能指向上位机数据库。当前 CLI 自动生成样本；默认合成工程 profile 未批准，`--approve-synthetic-profile` 仅允许在软件模拟值上运行，不构成设备工程批准。

1. 记录包版本、后台服务与登录结果，明确本次数据是保留旧库还是新建；设备尚未配对时显示离线是预期行为。
2. 在停止服务后按第 2 节为本次软件设备配对。将 `service.env` 的地址/端口设为 `127.0.0.1` / `34212`，保持 `PROTOCOL_VERSION=2.0`、`HOSTCOMM_MOCK=false`；配对工具不会代填地址。使用生成的三项身份和 PSK，不能混用模拟器的默认身份或现场设备材料。
3. 在能读取受限配对文件的管理员 PowerShell 中启动模拟器，再启动 `SmdHmi` 服务。按下面模板替换路径；`$SimPython` 是另备的源码环境，不能用 `SmdService.exe` 替代。

```powershell
Set-Location "<源码目录>\backend"
$SimPython = "<源码虚拟环境>\Scripts\python.exe"
$SimWork = "<已创建的独立模拟器目录>"
$PairingFile = "C:\ProgramData\SmdHmi\config\pairings\<事务ID>\firmware-pairing.json"
$Pairing = Get-Content -LiteralPath $PairingFile -Raw | ConvertFrom-Json
$PskFile = Join-Path (Split-Path $PairingFile) $Pairing.psk_file
$PolicyFile = Join-Path $SimWork "openssl-hostcomm.cnf"
& $SimPython -c "import pathlib,sys; from app.hostcomm.v2_security import OPENSSL_AES128_POLICY; pathlib.Path(sys.argv[1]).write_text(OPENSSL_AES128_POLICY, encoding='ascii')" $PolicyFile
if ($LASTEXITCODE -ne 0) { throw "Cannot prepare simulator TLS policy" }
$PreviousOpenSslConf = $env:OPENSSL_CONF
try {
    $env:OPENSSL_CONF = $PolicyFile
    & $SimPython -m app.hostcomm.v2_simulator --storage (Join-Path $SimWork "device.sqlite") --host 127.0.0.1 --port 34212 --psk-file $PskFile --device-id $Pairing.device_id --controller-id $Pairing.controller_id --controller-epoch $Pairing.controller_epoch --approve-synthetic-profile
} finally {
    $env:OPENSSL_CONF = $PreviousOpenSslConf
}
```

TLS 模拟器需要 Python 的 `ssl.HAS_PSK`。CPython 的上下文 API 不能直接限定 TLS 1.3 密码套件，因此在**新模拟器进程启动前**加载源码中的 `OPENSSL_AES128_POLICY`；模板只改变当前终端环境并在退出后恢复，不修改系统或服务的 TLS 配置。策略未生效、PSK/身份不符或 ACL 不合要求时应诊断失败原因，不能关闭校验或改用 `--test-plaintext` 代替这项 TLS 验收。

4. 在另一管理员终端启动服务；确认后台在线、握手身份/能力正确、恢复完成且状态新鲜，然后验证工程 profile 只读、标准/非标配方校验与原字节回读成功。TCP 连通、应用登录和设备可控制是不同检查点。
5. 在“操作记录”显式触发日志补传，保存任务终态、设备/控制器身份、版本及截图；扫描完成仍按第 5 节解释。停止测试时先按状态完成处置/确认，再停止服务和模拟器，保留双方独立数据库与受限配对材料供复验。

**独立测试驱动已由SmdBench实现。** `finish_measurement()`、`complete_purge()`、`complete_cooling()` 是 [Python 模拟器接口](../../../backend/app/hostcomm/v2_simulator/server.py)，用于控制测试轨迹；它们不是 HostComm 网络命令，也没有对应上述源码 CLI 参数或生产页面按钮。[正常场景](../../../tools/bench/smd_bench/scenarios.py)与[故障场景](../../../tools/bench/smd_bench/faults.py)通过工具私有管道注入首滴、测定、置换、冷却、报警、断线/重启和日志缺口，再通过真实页面与接口核对结果。启动源码CLI不等于完成这些场景；安装版验收以同提交的实际执行证据为准。私有测试动作不进入生产控制接口。

软件链路取得证据后继续[固件路线](firmware-plan.md)的 C/Python 向量、持久操作与目标板接入；合成 profile 和模拟器数据不能作为真实传感器、MFC、联锁或国标实验合格依据。
