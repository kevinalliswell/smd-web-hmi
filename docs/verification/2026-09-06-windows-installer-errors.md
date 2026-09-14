# Windows 安装错误提示排查

2026-09-06，用户报告覆盖升级至 `0.3.0-rc.3` 时安装器失败，通用错误提示出现中文乱码。发布基线为 `af02a8857d5cc132ad25e3515835fa5353d1af73`。后续现场 `updater.log` 确认 `updater.run()` 读取 `C:\ProgramData\SmdHmi\maintenance.json` 时发生 `FileNotFoundError`：覆盖升级缺少准备票据，在本次数据库备份、迁移开始前停止。修复候选为 `0.3.0-rc.4`。

## 已确认的诊断缺陷

- [rc.3 发布构建](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34023028660/job/101459449931)实际记录 `Processing script file: "deploy/windows/installer.nsi" (ACP)`。源文件是无 BOM 的 UTF-8，原调用没有输入字符集参数；`Unicode true` 控制安装器输出类型，不指定源文件编码。
- 原通用弹窗指向 `ProgramData\SmdHmi\updates`，实际错误日志在默认数据目录的 `logs\updater.log`。`updates` 保存事务记录及备份。
- `ExecWait` 无法启动进程时设置错误标志，退出码变量不确定；原代码只检查退出码，未明确处理无法启动的情况。

依据：[NSIS 输入字符集](https://nsis.sourceforge.io/Docs/Chapter3.html#3.1.1)、[脚本编码声明](https://nsis.sourceforge.io/Docs/Chapter4.html#4.1)、[ExecWait 失败语义](https://nsis.sourceforge.io/Docs/Chapter4.html#4.9.1.5)。

## 修正与验证范围

源码加入 UTF-8 声明，构建明确传入 `/INPUTCHARSET UTF8`。构建前通过实际 NSIS `/PPO` 预处理检查所有中文 MessageBox 文案；检查和正式编译共用参数列表。安装/卸载区分进程无法启动与运行后非零退出，显示退出码及实际日志位置，不再将通用失败直接解释为缺少维护准备。

维护准备文件不存在或目标版本不匹配时，更新器明确记录准备要求并以退出码 `20` 返回。安装器据此提示旧版软件的“系统设置 → 离线升级”、精确目标版本和十分钟有效期；其他错误仍指向实际日志。不自动生成票据、不模拟设备在线，也不跳过原后台的领取检查。

本地 macOS / Python 3.13 桌面检查为 150 项通过、9 项因平台或未安装工具跳过，包含编码检查器的 16 项行为回归及维护准备的 5 项回归；格式和文档链接检查通过。维护准备回归先复现 4 项失败，修复后通过，并验证原 SQLite、配置及程序不变。编码检查器覆盖 NSIS 预处理的控制字符展开、字面美元符号及 Windows 换行，不重复解释编译器输出。

本机没有 NSIS，真实预处理、安装器构建和 Windows 安装验证以 [PR #72](https://github.com/kevinalliswell/smd-web-hmi/pull/72) 及标签流水线为准；这些本地测试不证明现场覆盖升级成功。Windows 安装检查增加实际 NSIS 重复安装的拒绝路径：缺少维护准备时返回 `20`、旧服务 PID 和配置不变、错误日志为可读 UTF-8；检查范围见[说明](../../deploy/windows/SMOKE-CI.md)。

## 现场重试与旧版限制

用户确认实际文件位于 `C:\ProgramData\SmdHmi\logs\updater.log`。读取方式及恢复边界见 [Windows 维护说明](../../deploy/windows/README.md#失败与断电恢复)。不删除配置、实验数据或维护记录来绕过失败；不要求发送密码、PSK 或 `service.env`。

覆盖升级需要旧后台为精确目标版本准备维护。旧 HostComm 1.0 后台要求设备在线、新鲜且空闲；它没有明确未配对的离线维护路径。rc.3 对未配对 2.0 的离线维护支持，不能反向改变旧后台的门禁。这是旧版离线升级路径的限制；本次日志只确认票据缺失，没有证明用户此前已尝试准备并被此限制拒绝。本次诊断修复未改变维护门禁，用户现场重试结果仍待确认。

## 2026-09-07 用户现场结果

后续用户确认旧1.0离线准备确实被拒绝，并选择保留资料、空库重装；这补充了上节当时尚缺的反馈。用户随后报告已卸载，但rc.4首次安装仍失败。新日志定位为 `CreateService` 返回1073，更新器报“已有同名服务路径不同，拒绝接管”；CIM输出确认残留 `SmdHmi` 服务已停止，仍指向 `C:\Program Files\SmdHmi\versions\0.3.0-rc.2\SmdService\SmdService.exe`。

针对已确认的无设备测试机残留服务，用户在提权管理员终端完成服务登记清理，输出 `DeleteService 成功`，随后查询返回1060（服务不存在）；此前普通终端返回5（拒绝访问）未完成删除。再次运行原rc.4安装器后，用户明确反馈“已经安装成功”。本次诊断不再归因于缺少维护文件或卸载完成日志。

该证据属于用户报告的本次安装成功，不证明正常覆盖升级、完整备份/空库来源、Windows安装版到模拟器的TLS通信、真实固件或完整实验已验收。没有证据表明用户执行过[独立重装工具](2026-09-06-test-reinstall.md)。服务残留处理仅适用于本次已核实对象，不能作为通用跳过维护许可的卸载流程。

## 2026-09-08 rc.5 升级及 rc.4 回退验活失败

用户提供的最新日志显示 `UpgradeTransaction.apply()` 在 `healthy("0.3.0-rc.5")` 失败，随后 `_rollback()` 在 `healthy("0.3.0-rc.4")` 再次失败，最终抛出“升级失败且回退未完成；保持维护锁”。安装器截图记录退出码 `-1073741510`。此前两段1073没有时间戳，不能证明残留服务冲突在本次升级中再次发生；此前删除rc.2服务的处理不适用于这次未完成事务。

源码审查基线为 `cc7ea07da1d29b29f0cb3379c6498dfa27bbc226`（标签 `v0.3.0-rc.5`）。该版本验活要求配置地址的 `/api/system/health` 返回目标版本且 `status=ready`，随后首页返回200及HTML。数据库/schema、数据目录可写与空间、自动备份共同决定就绪状态；HostComm离线不阻止应用就绪。默认可用空间下限为1 GiB，是否触发需查看实际结果，不能从笼统错误推断。

已确认诊断缺口：原验活循环吞掉连接、HTTP、解析和缺字段错误，版本不符、后端未就绪和首页检查失败也仅留下相同的超时异常。两版升级/回退/验活实现相同，共用 `client.json` 及数据目录；当前日志不足以确定现场根因或确认数据库已恢复成功。

现场后续证据与待补项见下节；读取步骤见 [Windows维护说明](../../deploy/windows/README.md#失败与断电恢复)。保留维护票据、事务记录、程序、配置和全部备份。查明并处理具体原因后，再尝试通过完整版本的 `SmdUpdate.exe --recover` 执行事务恢复，只有重新验活通过才算恢复完成；不手工删除锁、生成票据或降低验活条件。

本轮已发布的Windows CI证据覆盖新装、未准备升级的拒绝、测试机重装及安装版模拟实验；未执行保留rc.4原库的rc.5覆盖升级和实际回退。该现场问题归入M4-06c，恢复收尾尚未确认、未验收，不能以新装通过关闭。尚无证据确定截图退出码与两次验活失败的因果关系。

### 现场后续查询

用户返回的健康响应时间为 `2026-09-08T10:15:25+00:00`，SCM确认 `SmdHmi` 为Running，实际程序路径指向rc.4。健康响应为 `version=0.3.0-rc.4`、`status=ready`，database/schema/storage/backup全部为ok，数据卷剩余2,133,225,472字节（约1.99 GiB），HostComm为offline。服务日志尾部只包含四条健康接口200访问记录，没有提供升级期间的具体检查结果。

这些结果证明查询时rc.4后台已就绪，当前没有触发存储门槛；不能反推两次验活失败时的状态，也不能证明升级事务已收尾或首页通过。下一步只读查询配置地址首页的HTTP状态及Content-Type，以及 `updates/active.json` 的phase、target_version、backup_ready；不要求发送完整配置或票据。若事务仍未完成，恢复入口会再次停服务并按事务恢复数据库和配置，不能把它当作仅清除标志的命令。

用户随后提供首页结果 `200 / text/html; charset=utf-8`，并在单独读取phase后文字回复“rolled back failed”（按当前枚举对应 `rollback_failed`）。当前后台及首页均已通过查询，而事务仍未完成。下一步使用已安装rc.4的 `SmdUpdate.exe --recover --install "C:\Program Files\SmdHmi"` 单次尝试既有事务恢复；通过提升权限的PowerShell等待进程结束并核对退出码，以及phase变为 `rolled_back`。该操作会用本次升级前的事务备份恢复数据库和配置，成功后运行rc.4并解除对应维护票据；不等于安装rc.5。此时尚未取得恢复执行结果，原验活失败原因仍未知。

### 本地诊断修补验证

工作分支 `fix/upgrade-health-diagnostics` 在上述基线增加失败阶段、版本、检查项、磁盘可用字节、HTTP状态或固定网络错误类别；只记录白名单字段，重复诊断去重，最终异常保留最后摘要。更新器日志新增UTC时间。原版本/就绪/HTML判定、每请求3秒、0.5秒重试和外层等待期限保留；该修补尚未发布，不改变rc.5资产，也未解决尚待定位的现场原因。

执行环境为macOS 26.3 arm64、Python 3.13.12、SQLite 3.50.4。真实回环HTTP代表用例在原验活实现下因缺少诊断失败；修补后专项38项通过。仓库根执行 `../implementation-venv/bin/python -m pytest -c backend/pytest.ini desktop/tests -q`，结果182通过、26跳过（Windows/PowerShell/NTFS或缺少PyInstaller）。Black、isort、文档链接和独立审查通过。未在Windows上运行此修补。

测试时HEAD仍为上述基线，执行的是含修补的工作文件，不把基线提交误标为修补后的代码；所测Git blob如下：

| 文件（仓库相对路径） | Git blob |
|---|---|
| `desktop/smd_desktop/windows_platform.py` | `348e7cf716967a12274f5250027a6c0cad610ed2` |
| `desktop/smd_desktop/updater.py` | `bdcfcf8aa38fdc8382a3f2608b88bf5ede64bbef` |
| `desktop/tests/test_windows_health.py` | `42becd1d41fb69ce66e8dbe695691b707bfb3d06` |
| `desktop/tests/test_updater_maintenance.py` | `3e563c843d0e77db1da4b3e54828763e58bc57e1` |
