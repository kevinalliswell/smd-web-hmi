# Windows 安装错误提示排查

2026-09-06，用户报告覆盖升级至 `0.3.0-rc.3` 时安装器失败，通用错误提示出现中文乱码。发布基线为 `af02a8857d5cc132ad25e3515835fa5353d1af73`。截图证明安装失败和提示不可读，不能据此确定更新器内部失败原因；现场 `updater.log` 内容仍待取得。

## 已确认的诊断缺陷

- [rc.3 发布构建](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34023028660/job/101459449931)实际记录 `Processing script file: "deploy/windows/installer.nsi" (ACP)`。源文件是无 BOM 的 UTF-8，原调用没有输入字符集参数；`Unicode true` 控制安装器输出类型，不指定源文件编码。
- 原通用弹窗指向 `ProgramData\SmdHmi\updates`，实际错误日志在默认数据目录的 `logs\updater.log`。`updates` 保存事务记录及备份。
- `ExecWait` 无法启动进程时设置错误标志，退出码变量不确定；原代码只检查退出码，未明确处理无法启动的情况。

依据：[NSIS 输入字符集](https://nsis.sourceforge.io/Docs/Chapter3.html#3.1.1)、[脚本编码声明](https://nsis.sourceforge.io/Docs/Chapter4.html#4.1)、[ExecWait 失败语义](https://nsis.sourceforge.io/Docs/Chapter4.html#4.9.1.5)。

## 修正与验证范围

源码加入 UTF-8 声明，构建明确传入 `/INPUTCHARSET UTF8`。构建前通过实际 NSIS `/PPO` 预处理检查所有中文 MessageBox 文案；检查和正式编译共用参数列表。安装/卸载区分进程无法启动与运行后非零退出，显示退出码及实际日志位置，不再将通用失败直接解释为缺少维护准备。

本地 macOS / Python 3.13 桌面检查为 138 项通过、9 项因平台或未安装工具跳过，包含编码检查器的 9 项行为回归；格式和文档链接检查通过。本机没有 NSIS，真实预处理、安装器构建和 Windows 安装验证须以本次 PR 的 Windows CI 为准；这些本地测试不证明现场覆盖升级成功。

## 现场失败仍需定位

用户确认实际文件位于 `C:\ProgramData\SmdHmi\logs\updater.log`。读取方式及恢复边界见 [Windows 维护说明](../../deploy/windows/README.md#失败与断电恢复)。不删除配置、实验数据或维护记录来绕过失败；不要求发送密码、PSK 或 `service.env`。

覆盖升级需要旧后台为精确目标版本准备维护。旧 HostComm 1.0 后台要求设备在线、新鲜且空闲；它没有明确未配对的离线维护路径。rc.3 对未配对 2.0 的离线维护支持，不能反向改变旧后台的门禁。这是旧版离线升级路径的限制，但在获得本次日志前，不能将其定为本次安装失败原因。本次诊断修复未改变维护门禁。
