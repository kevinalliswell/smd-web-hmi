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
