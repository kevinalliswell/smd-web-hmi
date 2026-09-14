# ADR-005：一个设备后台，多个界面，共享可追溯实验

## 状态与日期

2026-09-13 补充：[ADR-011](ADR-011-overwrite-install-and-software-release.md)更新覆盖安装的维护入口，并将软件正式版与指定设备组合资格分开；以下保留当时决策和验收范围。

已接受，2026-09-05；设计已落入本轮软件代码，Windows及真实组合仍以 M2—M5 分层证据为准；实验契约细化见[ADR-006](ADR-006-candidate-experiment-contracts.md)。

## 背景与决策

本地浏览器和 Windows 桌面都需要控制同一炉设备。窗口生命周期不能决定采集生命周期；多开窗口也不能产生多个设备控制连接。

1. 保留 Vue/FastAPI/SQLAlchemy/SQLite，后端按通信、操作、实验、指标、报告、运维分模块。每炉一个后台服务和数据库写入协调器；所有客户端共享服务端操作权限、状态与审计。
2. 桌面采用 pywebview + 固定 WebView2 Runtime；Python 3.13、PyInstaller onedir 与 NSIS 生成离线安装器。后台作为受限身份 Windows Service 运行，桌面以普通用户运行，关闭窗口不停止后台。
3. 程序与运行时按版本存放，持久配置及数据使用 ProgramData。人工升级在维护锁下执行验包、待机确认、备份、迁移、切换和验活；失败恢复程序、依赖、配置和数据库，断电能从持久升级记录恢复。
4. STM32 执行实时流程和联锁。上位机保存操作 ID、请求、执行结果和结果未知状态；固件未验证去重前不自动重发有副作用的请求。
5. 标准/非标配方均版本化并随实验保存不可变快照，禁止自由脚本和解除设备安全上限。报告保存算法版本、依据和偏离项。
6. [ADR-002](ADR-002-utc-timestamps-on-sqlite.md) 继续用于业务 UTC 时间；采集另外保存设备原始时间、接收时间、序号/重启标识及质量，不从秒精度接收时间推导丢失的设备事件顺序。

## 替代方案与后果

为桌面复制第二套后台会造成控制竞争；Electron 或 Tauri 引入额外运行时/语言维护面，在当前 Python 团队中暂不采用；浏览器快捷方式不能满足独立离线安装要求。固定 WebView2 允许离线和回退，但必须随版本包及时更新运行时并记录组件清单。

参考：[pywebview 打包](https://pywebview.flowrl.com/guide/freezing.html)、[Runtime 设置](https://pywebview.flowrl.com/api/)、[Microsoft WebView2 分发](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)。目标选型不代表包签名、Windows Service 或真机兼容性已获验证。
