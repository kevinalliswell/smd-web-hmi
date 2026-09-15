# 无设备测试机：完整备份后以空库重装

本流程用于已明确选择空库重装、且与实体设备断开的专用测试机：保留旧程序和全部默认数据，清理旧安装登记，再用已发布的 `0.3.0` 安装包建立新数据库。本工具单独交付，不修改既有发行资产。早期rc.4场景见[历史专项记录](../../docs/verification/2026-09-06-test-reinstall.md)，当前脚本与安装包分别绑定的验证结果见[覆盖安装验收记录](../../docs/verification/2026-09-13-overwrite-install.md)。

保留原库的日常升级应使用[正常覆盖安装与恢复流程](README.md#人工离线升级)，无须执行本工具。已连接设备、可能仍在实验或承担现场采集的电脑须先完成设备安全处置，不能用空库重置替代该过程。

## 支持范围与备份内容

脚本为 [reset-test-installation.ps1](reset-test-installation.ps1)，使用管理员64位 Windows PowerShell 5.1，无需 Python。没有自定义路径参数。

| 项目 | 允许范围 |
|---|---|
| 程序目录 | 从64位注册表读取，限定系统实际Program Files下的`SmdHmi`，或CI专用`SmdHmi-CI-<32位十六进制>`目录 |
| 数据目录 | 系统实际CommonApplicationData下的`SmdHmi`，通常为`C:\ProgramData\SmdHmi` |
| 数据库 | 上述数据目录内的`db\smd.db` |
| 备份目录 | CommonApplicationData下的`SmdHmi-TestBackups\<UTC时间-唯一标识>`，位于待清理目录之外 |

进程、用户或系统环境中的目录覆盖，以及配置中的非默认数据库或维护路径不在本工具范围内。遇到这类拒绝应保留预览结果，按真实路径另行制定备份方案；不要删除或修改路径配置来让检查通过。

检测到现存安装事务记录、未完成的升级/配对/卸载事务或现存维护文件时，同样停止。应先按原事务的恢复流程处理，不能手改阶段或删除票据。备份范围不包括其他用户目录、浏览器缓存及默认目录外的实验导出；这些资料需要另行保留。

执行时先保存注册表、恢复任务、服务、原ACL和开始菜单入口信息，再禁用恢复任务、停止并禁用旧服务；确认后台进程已退出后备份完整程序和数据目录。生成文件清单并校验后，才删除服务、任务及旧安装登记。原程序和数据目录移入同卷备份目录内的`retired-program`、`retired-data`，与已验证副本一同保留。程序、数据和备份根须在同一卷，并有足够空间保存完整备份；本工具不处理跨卷迁移。

排查归档耗时时，私有 `stage.json` 在 `copying` 阶段记录当前备份子步骤和已完成步骤的耗时，供区分文件复制、校验和权限处理。同版修复保留的程序回退副本也属于完整备份范围，不能为提速删除。计时仅用于诊断，不改变校验、原文件保留或后续登记清理条件；原始阶段文件含本机路径，不应直接上传。

备份包含旧数据库、用户与实验记录、配置、JWT、初始口令及可能存在的设备配对密钥。备份目录仅允许Administrators和SYSTEM访问；不要把整个目录、数据库或`service.env`发到聊天、公开仓库或普通共享盘。支持排查时先提供阶段、退出码和脱敏后的错误。

## 1. 只读预览

将经审查的脚本放在下载目录等旧安装目录之外，核对交付时提供的脚本SHA-256。脚本与安装包各有独立校验值。关闭桌面窗口，确认这台电脑没有连接实体控制板，也不承担采集或实验。

以管理员打开64位 Windows PowerShell 5.1，进入脚本所在目录，执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\reset-test-installation.ps1"
$LASTEXITCODE
```

默认只读，不停止服务、不创建备份、不移动目录或删除登记。成功预览返回JSON，`mode`为`preview`、`phase`为`validated`。核对识别出的安装版本、程序目录和数据目录；预览拒绝或路径不符合预期时停止。

`ExecutionPolicy Bypass`仅用于这次子进程，不修改机器的长期执行策略。预览成功只表示前置检查通过，不表示备份或重装已经完成。

## 2. 执行完整备份与清理

确认已选择保留旧资料并用空库重装，且预览对象正确、无实体设备连接后，执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\reset-test-installation.ps1" -Apply -ConfirmNoDeviceAttached
$LASTEXITCODE
```

两个开关缺一不可。`-ConfirmNoDeviceAttached`声明使用范围，脚本不能替代对物理连接和实验状态的核实。

仅在退出码为`0`，且结果JSON的`mode`为`apply`、`phase`为`completed`时继续安装。保存以下结果：

- `version`、`install_dir`、`data_dir`：被处理的旧安装。
- `backup_dir`、`backup_manifest_sha256`：私有备份位置及清单摘要。
- `retired_program_dir`、`retired_data_dir`：保留的原始目录。

备份目录中的`program/`、`data/`为已验证副本，`metadata/`保存注册、任务、服务、原ACL及菜单资料；`retired-program`、`retired-data`是保留的原目录。`manifest.json`记录文件校验值，`stage.json`记录阶段。成功后仍保留整个备份目录；不要为了腾出空间立即删除它。

## 3. 用已发布正式版安装器新装

从[0.3.0发布页](https://github.com/kevinalliswell/smd-web-hmi/releases/tag/v0.3.0)取得安装器及`SHA256SUMS.txt`，校验后以管理员执行`SmdHmi-0.3.0-windows-x64.exe`。本次应进入首次安装路径，建立空数据库和新配置；不要使用旧维护票据。

安装器生成新的初始`admin`口令，默认位于：

```text
C:\ProgramData\SmdHmi\config\bootstrap-admin-password.txt
```

以该新口令登录，按页面要求修改密码；旧账户密码不属于新数据库。初始口令文件及密码均无需发送给他人。

核对应用版本为`0.3.0`，旧实验列表为空，后台服务和本机页面可访问。新安装默认HostComm 2.0、未配对、`HOSTCOMM_MOCK=false`；设备离线是预期状态，不影响登录和历史功能入口，不能据此开启实验。需要模拟器开发时另按[运行说明](../../docs/hostcomm/v2/runtime.md)配置，不能伪造设备在线来完成维护。

**不要把旧`service.env`、旧数据库、维护文件或配对目录复制回新安装。** 否则会带回旧协议、用户、未闭合实验或维护状态。旧记录继续在备份中保留，后续查看或迁移应作为独立、有审计的工作。

## 4. 失败后保留现场

脚本返回非零、`phase`不是`completed`，或备份校验不通过时，不运行安装器。保留控制台错误、备份路径、`stage.json`和已生成文件；失败可能发生在服务停止或部分登记清理之后。

脚本失败不会自动恢复旧服务或回滚所有步骤。不要手动删除备份、剩余程序、数据或登记来“完成清理”，也不要反复运行安装器掩盖原错误。先根据阶段和清单核对哪些步骤已经执行，再制定恢复或继续方案。完整文件备份不等于已经验证一键恢复旧安装。

本流程不关闭干净断网Windows、实际断电恢复或实体设备验收任务；既有发行包的构建与安装证据也不能替代脚本当前修订的原生Windows验证。
