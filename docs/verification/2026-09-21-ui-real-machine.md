# PR #84 前端视觉体系升级 · 真机 UI 验收

本记录覆盖已合并 PR #84 描述里列出的 7 项人工 UI 验收，全部在下述 Windows 11 实机执行并留证。
第 7 项（WebView2 桌面壳）先做过一轮不改动系统的零安装隔离验证；用户随后卸载了机上既有的
0.3.0 并删除其数据目录，于是补做了**真实安装 → 桌面壳验收 → 断网离线 → 卸载清理**的完整流程，
本记录只写这一轮真实结果，零安装那轮已被取代（保留在本分支提交历史里）。

## 绑定信息

| 项 | 值 |
|---|---|
| 被测提交 | `0515727be58fec05ba02fa7129b65406d8264917`（`main`，PR #84 squash） |
| 被测前端字节 | CI run [35555764613](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/35555764613) 工件 `frontend-dist`；`index.html` SHA-256 `10a6839fd9eace0cedb661d58bf407715b2ba7b0379ab929d1fdbfad9802784b`，55 个文件逐一校验见 [frontend-dist-ci-SHA256SUMS.txt](assets/2026-09-21-ui/frontend-dist-ci-SHA256SUMS.txt) |
| 被测安装器 | 同 run 工件 `windows-local-build-1` 的 `SmdHmi-0.3.0-windows-x64.exe`，339 521 916 字节，SHA-256 `ea6f60840f57e3890f5183062856d516a71f51d18da8207582e8e57e1f5df4c5`（与工件内 `SHA256SUMS.txt` 核对通过）；内嵌 `manifest.json` 的 `commit` 为上述被测提交，`webview2_version` 为 `152.0.4191.62` |
| 验收机器 | `KEVIN-X1`，Windows 11 家庭中文版 10.0.26200（非 Server 2019） |
| 显示 | 2880×1800 物理像素，系统缩放 200% |
| 浏览器 | Microsoft Edge `153.0.4234.32`（Chromium 153），Playwright 1.62.0 驱动 |
| 机器常驻 WebView2 Runtime | `153.0.4234.48`（仅供参考；桌面壳用的是随包 Fixed `152.0.4191.62`） |
| 后端（第 1—6 项）| 同提交 `backend/` 源码运行，`__version__ = 0.3.0`，`HOSTCOMM_MOCK=true`，监听 `127.0.0.1:8100`，托管上述 CI `frontend-dist` |
| 后端（第 7 项）| 上述安装器装出的生产后台，`SmdHmi` 服务以 `NT AUTHORITY\LocalService` 运行，监听 `127.0.0.1:8000`，未配对、`hostcomm offline` |
| 设备 | 第 1—6 项用仓内 `app.hostcomm.mock_server`（`127.0.0.1:34299`，`--demo-alarms --extended-contract --time-scale 20`）；第 7 项无设备。**全程未接真实控制板** |
| 执行日期 | 2026-09-21 |

被测前端刻意使用 CI 产出的字节而不是本机重建产物：本机 `core.autocrlf=true`，重建后所有
文本资源的字节与内容哈希都会变（已实测 `favicon.svg` 去掉 CR 后与 CI 完全一致），用 CI 字节
才能把结论绑定到该提交的实际发行物。

本机隔离与时序：执行第 1—6 项时，本机另有一套**在用的**已安装 SmdHmi 0.3.0
（`manifest.commit = 404ecb50…`，即已发布的 v0.3.0，非被测提交），服务运行在 8000 端口；
验收用后端因此另起在 8100 端口、用独立 SQLite、Mock 另用 34299 端口，**全程未停止、未修改、
未读写该既有安装**。第 1—6 项完成后，用户自行卸载了那套 0.3.0 并删除其数据目录；机器回到
无既有安装的状态后，才执行第 7 项的真实安装（见该节）。两段工作互不重叠。

## 结论一览

| # | 验收项 | 结论 |
|---|---|---|
| 1 | 深/浅主题 × 16 页面目检 | 已通过 |
| 2 | 每页切一次主题，重着色无旧色残留 | 已通过 |
| 3 | 1440×1000 窗口 125%/150%/200% 无页面级横向溢出 | 已通过 |
| 4 | 键盘/焦点回归（M1-05 基线四组） | 已通过（第四组"准备离线升级"入口已随 ADR-011 从产品移除，改测同类受控写入确认，差异见下） |
| 5 | `prefers-reduced-motion: reduce` 动画降级 | 已通过 |
| 6 | Cascadia Mono/Consolas 实际渲染 | 已通过 |
| 7 | WebView2 桌面壳与浏览器双入口 | 已通过（中文安装目录真实首次安装、双入口同页同主题一致、断网离线可用、卸载清理；未覆盖范围见该节） |

另发现 2 个与本次视觉改动无关、但在被测提交上真实可见的问题，见[发现的问题](#发现的问题)。

## 复现环境与夹具

夹具脚本随本记录入库，位于 [assets/2026-09-21-ui/scripts/](assets/2026-09-21-ui/scripts/)：

| 脚本 | 作用 |
|---|---|
| `seed_ui_fixture.py` | 向验收专用 SQLite **追加**合成历史试验（两批共 16 个，每个 180 采样点），供历史页与数据分析页有内容可看。纯合成夹具，只 INSERT，不删改既有记录 |
| `prepare_device_fixture.py` | 在 Mock 控制板上保存/校验/下发一份标准候选配方（`start_test` 要求绑定已回读一致的配方版本；Mock 重启会丢该绑定） |
| `ui_acceptance.py` | 第 1—6 项的浏览器侧夹具：真实 Edge 有头窗口、真实渲染、逐项断言并输出 JSON |
| `item7_install.ps1` | 第 7 项：提权前置检查 → 静默安装到中文目录 → 采集安装后事实 → 一次性初始口令换成验收口令（口令不进任何输出） |
| `item7_offline_probe.ps1` | 第 7 项：开壳并登录后按固定间隔记录网络状态/健康检查/静态资源/窗口截图，供断网窗口取证 |
| `item7_uninstall.ps1` | 第 7 项：提权静默卸载并核查残留，`-RemoveDataDir` 时一并删除本轮空库数据目录 |
| `capture_webview2_shell.ps1` | 第 7 项：启动 `SmdDesktop.exe`、壳内登录、切主题并抓窗口图 |
| `capture_window_by_title.ps1` | 第 7 项：用与桌面壳相同的 DPI 感知 GDI 路径抓浏览器窗口，使取色比对同口径 |
| `compare_shell_vs_browser.py` | 第 7 项：对两侧截图做主题令牌取色统计 |

第 7 项的两个提权脚本必须存成 **UTF-8 with BOM**：本机 ANSI 代码页是 gb2312，无 BOM 的 UTF-8
会被 PowerShell 5.1 按 GBK 误读，脚本里的中文安装路径会变成乱码。

逐项断言原始输出在 [assets/2026-09-21-ui/results/](assets/2026-09-21-ui/results/)，截图在
[assets/2026-09-21-ui/shots/](assets/2026-09-21-ui/shots/)，全部文件的 SHA-256 见
[assets/2026-09-21-ui/SHA256SUMS.txt](assets/2026-09-21-ui/SHA256SUMS.txt)。

浏览器窗口固定 `--window-size=1440,1000`；第 1—6 项额外加 `--force-device-scale-factor=1`，
把 DPR 锁到 1，使 100% 档的 CSS 视口（1416×861）与 [M1-05 缩放基线](2026-09-06-browser-zoom.md)
的 1440×913 / DPR 1 同口径可比。第 7 项对照组不加该参数，与桌面壳同为 DPR 2。

仓内 `tools/bench` 锁定的 Playwright 1.62.0 已安装，但其自带 Chromium 151.0.7922.34 在本机
**无法启动**：`chrome.exe` 并行配置解析失败（事件日志 SideBySide：找不到从属程序集
`151.0.7922.34`），强制重装无效。改用本机实装的 Edge 153（同为 Chromium，且与桌面壳 WebView2
同引擎族），并在上表记录实际版本。

## 1. 深/浅主题 × 16 页面目检

**已通过。** 16 个页面（与 `frontend/src/router/index.js` 一一对应）各出深浅两张截图，命名
`<页面>-<dark|light>.png`。应用外壳的内容区 `.content` 自带滚动，首屏之外的内容另出一张
`-b` 结尾的滚到底截图（12 个页面需要），不足部分不隐瞒。

| 页面 | 路由 | 截图 |
|---|---|---|
| 登录 | `/login` | `login-{dark,light}.png` |
| 修改初始密码 | `/change-password` | `change-password-{dark,light}.png`（用首次登录账号 `operator1` 进入，未提交修改） |
| 实时总览 | `/overview` | `overview-{dark,light}{,-b}.png` |
| 趋势曲线 | `/trend` | `trend-{dark,light}.png` |
| 当前试验 | `/test` | `test-{dark,light}{,-b}.png` |
| 报警事件 | `/alarms` | `alarms-{dark,light}.png` |
| 历史试验 | `/history` | `history-{dark,light}{,-b}.png` |
| 运行恢复 | `/run-recoveries` | `run-recoveries-{dark,light}.png` |
| 实验配方 | `/recipes` | `recipes-{dark,light}.png` |
| 操作记录 | `/operations` | `operations-{dark,light}{,-b}.png` |
| 参数配置 | `/parameters` | `parameters-{dark,light}{,-b}.png` |
| 设备诊断 | `/diagnostics` | `diagnostics-{dark,light}.png` |
| 系统设置 | `/settings` | `settings-{dark,light}{,-b}.png` |
| 数据分析 | `/analytics` | `analytics-{dark,light}{,-b}.png` |
| 报告生成 | `/reports` | `reports-{dark,light}.png` |
| 帮助 | `/help` | `help-{dark,light}{,-b}.png` |

清单点名的重点项，除目检外都取了计算样式（`results-item1.json` 的 `key_elements`）：

- **总览 KPI 与状态灯**：6 张 KPI 卡读数用等宽栈（见第 6 项），`.dot-green` 计算背景深色
  `rgb(34,197,94)` = `--green` `#22c55e`、浅色 `rgb(21,128,61)` = `#15803d`，`animation-name: pulse`。
- **当前试验 StageStepper 与 CO 橙色角标**：步骤条 9 阶段齐全，第 5/6/7 阶段（500 ℃ 切换还原气、
  程序升温、1580 ℃ 保持）带 CO 角标。`.co-tag` 计算背景深色 `rgb(249,115,22)` = `--orange` `#f97316`，
  浅色 `rgb(194,65,12)` = `#c2410c`，**两主题下都与 `--red` / `--yellow` / `--green` / `--accent` / `--purple`
  互不相同**，未与任何报警状态色混同。总览页 CO 流量 KPI 的角标同色同值。
- **报警页 L3 脉冲横幅**：经 Mock 注入真实 L3 报警（`ALM-EXHAUST`/`ALM-OVERTEMP`，`--demo-alarms`），
  `.critical-banner` 实际出现，计算样式 `background rgb(72,19,19)`/`border rgb(239,68,68)`/
  `color rgb(252,165,165)`、`animation-name: pulse`（浅色为 `rgb(254,226,226)`/`rgb(220,38,38)`/`rgb(185,28,28)`）。
- **历史页双栏**：`grid-template-columns: 360px 1fr` 左列表右详情，截图可见。
- **参数页修改值黄色高亮**：改 3 个字段后 `.field input.changed` 计算 `color`/`border-color`
  深色 `rgb(245,158,11)` = `--yellow` `#f59e0b`，浅色 `rgb(180,83,9)` = `#b45309`。
- **数据分析 8 试验叠加图**：勾选 8 个合成夹具试验（`T20260903-001` … `T20260910-008`，各 180 点，
  升温指数逐个不同故曲线明显分开），图例 8 色、曲线 8 色可区分；像素级取色验证见第 2 项。

## 2. 切换主题后的重着色

**已通过，14 个需登录页面全部无旧色残留。** 判定方法不依赖肉眼：

1. 先在运行时分别读出 `data-theme=dark` 与 `light` 下的 38 个颜色令牌，算出"某主题独有"的颜色集合
   （另一主题不存在该值）。
2. 打开页面、布置好内容后，**点击顶栏主题切换按钮**（不是直接改 DOM）切换一次。
3. 切换后扫描页面全部元素的 `color`/`background-color`/四边 `border-color`/`outline-color`/`box-shadow`，
   若出现任何"切换前主题独有"的颜色即判失败。
4. 对每个 `<canvas>` 直接 `getImageData` 读实际绘制像素，逐一统计 8 个 `--series-N` 槽位色在
   新旧主题下的命中像素数（容差 6/255）。

登录页与修改密码页不在本项统计内（无业务图表，且本项要求的是登录后的 14 个页面）。为覆盖两个
方向，按页序交替从深色/浅色起切。

| 页面 | 切换方向 | canvas 数 | 残留旧主题独有色 | 命中新主题独有色 | 结论 |
|---|---|---|---|---|---|
| overview | dark→light | 1 | 0 | 15 | 通过 |
| trend | light→dark | 1 | 0 | 21 | 通过 |
| test | dark→light | 4 | 0 | 15 | 通过 |
| alarms | light→dark | 0 | 0 | 18 | 通过 |
| history | dark→light | 0 | 0 | 12 | 通过 |
| run-recoveries | light→dark | 0 | 0 | 13 | 通过 |
| recipes | dark→light | 0 | 0 | 12 | 通过 |
| operations | light→dark | 0 | 0 | 14 | 通过 |
| parameters | dark→light | 0 | 0 | 13 | 通过 |
| diagnostics | light→dark | 0 | 0 | 14 | 通过 |
| settings | dark→light | 0 | 0 | 12 | 通过 |
| analytics | light→dark | 1 | 0 | 14 | 通过 |
| reports | dark→light | 0 | 0 | 12 | 通过 |
| help | light→dark | 0 | 0 | 18 | 通过 |

**本次修复点（数据分析叠加图）单独留证。** 勾选 8 个试验对比后，从浅色切到深色：

- 切换后画布上 8 个深色系列槽位的命中像素分别为 `605 / 593 / 618 / 592 / 594 / 601 / 623 / 615`，
  **8 条曲线全部换成了当前主题系列色**；
- 同一张画布上，8 个浅色系列槽位的命中像素**全为 0**，无旧色残留。

对照截图：`item2-analytics-overlay-dark.png`、`item2-analytics-overlay-light.png`、
`item2-analytics-overlay-back-dark.png`（深→浅→深往返各一张）。

`test` 页 4 张实时图在切换后分别只命中 1 个新主题槽位色（槽 1/3/4/5）、旧色 0，符合"每图一条
按槽着色的曲线"的设计。

## 3. 浏览器缩放下的页面级横向溢出

**已通过，12 个组合全部 `scrollWidth <= clientWidth`。** 窗口固定 1440×1000，缩放用最小 MV3 扩展
调 `chrome.tabs.setZoom` 并用 `getZoom` 回读（与 [M1-05 缩放基线](2026-09-06-browser-zoom.md)
同方法，是浏览器原生缩放，不是 `deviceScaleFactor` 或 CSS `zoom`——`visualViewport.scale` 全程为 1）。

| 缩放 | 回读 | CSS 视口 | DPR | `visualViewport.scale` | Overview | History | Parameters |
|---|---|---|---|---|---|---|---|
| 100% | 1 | 1416×861 | 1 | 1 | 1416 ≤ 1416 ✅ | 1416 ≤ 1416 ✅ | 1416 ≤ 1416 ✅ |
| 125% | 1.25 | 1133×689 | 1.25 | 1 | 1133 ≤ 1133 ✅ | 1133 ≤ 1133 ✅ | 1133 ≤ 1133 ✅ |
| 150% | 1.5 | 944×574 | 1.5 | 1 | 944 ≤ 944 ✅ | 944 ≤ 944 ✅ | 944 ≤ 944 ✅ |
| 200% | 2 | 708×430 | 2 | 1 | 708 ≤ 708 ✅ | 708 ≤ 708 ✅ | 708 ≤ 708 ✅ |

截图 `item3-<页面>-zoom<档位>.png` 共 12 张；参数页在 125% 及以上档位仍带有已修改字段（黄色高亮）。
本项只判定页面级横向溢出；卡片内部允许的局部横向滚动（`.card { overflow-x: auto }`）不计入。

## 4. 键盘与焦点回归（M1-05 基线）

**已通过。** 逐组按键序列与 `document.activeElement` 断言见 `results-item4.json`。

### 4.1 停止试验（`StopTestModal`，`danger` 二次确认）

前置：通过界面真实下发一次 `start_test`（对象为本地 Mock，状态推进到 `Heating`），使"停止试验"可用。

| 步骤 | 观察 |
|---|---|
| 焦点置于"■ 停止试验"并点击 | 弹窗打开，`[role=dialog]` 计数 1 |
| 打开时默认焦点 | `BUTTON "取消"`，带 `data-dialog-cancel`，位于弹窗内 ✅ |
| Tab ×8 | 循环序列恒为 `确认停止 → 取消 → 确认停止 → …`，未逃出弹窗 |
| Shift+Tab ×8 | 反向同样只在两个按钮间循环 |
| Escape | `[role=dialog]` 计数归 0 |
| 关闭后焦点 | 回到触发按钮 `BUTTON "■ 停止试验"` ✅ |

截图 `item4-stop-dialog.png`。

### 4.2 启动试验及其嵌套确认（`StartTestModal` + 内层 `ConfirmDialog`）

| 步骤 | 观察 |
|---|---|
| 焦点置于"▶ 启动试验"并点击 | 外层弹窗打开，计数 1 |
| 外层打开时默认焦点 | `INPUT#start-test-id`（外层非安全确认框，取第一个可聚焦项） |
| 外层 Tab ×10 | `试验编号 → 原始料层高度 → 样品标识 → 配方 summary → 备注 → 取消 → 试验编号 → …` 循环，未逃逸；此时"下一步"因必填项未满足处于 disabled，正确地不进入焦点环 |
| 填高度后点"下一步" | 嵌套确认打开，`[role=dialog]` 计数 2 |
| 嵌套打开时默认焦点 | `BUTTON "取消"`，带 `data-dialog-cancel` ✅（CO 安全确认默认落在取消） |
| 嵌套 Tab ×6 / Shift+Tab ×6 | 只在 `确认启动 ↔ 取消` 间循环，未落回外层 |
| Escape（第一次） | 计数 2→1，焦点回到嵌套触发按钮 `BUTTON "下一步"` ✅ |
| Escape（第二次） | 计数 1→0，焦点回到 `BUTTON "▶ 启动试验"` ✅ |

截图 `item4-start-nested-dialog.png`、`item4-start-modal-recipe.png`。

### 4.3 密码重置（`PasswordResetDialog`）

| 步骤 | 观察 |
|---|---|
| 系统设置页焦点置于"重置密码"并点击 | 弹窗打开，标题"重置 admin 的密码"，计数 1 |
| 打开时默认焦点 | `INPUT#reset-password`（非安全确认框，取第一个可聚焦项） |
| Tab ×8 | `新密码 → 确认新密码 → 取消 → 新密码 → …` 循环，未逃逸 |
| Shift+Tab ×8 | 反向循环一致 |
| Escape | 计数归 0 |
| 关闭后焦点 | 回到 `BUTTON "重置密码"` ✅ |

截图 `item4-password-reset-dialog.png`。**未提交任何密码修改。**

### 4.4 第四组：基线的"准备离线升级"在被测构建中已不存在

M1-05 基线（`docs/verification/2026-09-06-software.md`）第四组是"准备离线升级"弹窗。该入口已随
[ADR-011](../decisions/ADR-011-overwrite-install-and-software-release.md) 的覆盖安装改造从产品移除：

- 对 14 个需登录页面逐页扫描 `button/a/summary/[role=button]` 的可见文案，匹配
  `准备升级|准备离线升级|离线升级|升级准备` 的控件数为 **0**；
- 系统设置页"版本与维护状态"面板实际文案为"更新软件时，直接运行新版 Windows 安装器。安装器
  自动识别版本，保留数据库、配置和账号，**无需在此填写版本号或准备升级**。"，面板只剩"刷新状态"
  一个按钮（截图 `item4-settings-maintenance-panel.png`）。

因此该组按"入口已移除、不适用"记录，**不作为通过项计数**；为不留空档，补测了同一页面族里仍
存在的受控写入二次确认——参数下发（`ParametersPage` 的 `ConfirmDialog`，需非运行状态且设备
`can_set_parameters`）：

| 步骤 | 观察 |
|---|---|
| 改 3 个参数后焦点置于"下发参数"并点击 | 弹窗打开，计数 1 |
| 打开时默认焦点 | `BUTTON "取消"`，带 `data-dialog-cancel` ✅ |
| Tab ×6 / Shift+Tab ×6 | 只在 `确认下发 ↔ 取消` 间循环 |
| Escape | 计数归 0，焦点回到 `BUTTON "下发参数"` ✅ |

截图 `item4-parameters-confirm-dialog.png`。**未点击"确认下发"，未真实下发参数。**

### 4.5 本组产生的设备侧动作

为构造前置状态，本组通过界面对 **Mock 控制板** 真实下发了 `start_test`（绑定标准候选配方 v1，
`original_height_mm = 75.0`）与 `stop_test` 各一次，结束状态为 `End`。全部命令走正常的二次确认
+ `confirm-intent` 令牌路径，无真实设备参与。

## 5. `prefers-reduced-motion: reduce` 下的动画降级

**已通过。** 分别以 `reduced_motion = no-preference` / `reduce` 启动两个浏览器上下文（Playwright
经 CDP 下发 `Emulation.setEmulatedMedia`，页面内 `matchMedia('(prefers-reduced-motion: reduce)').matches`
分别为 `false` / `true`，已断言）。

| 元素 | 常规 | reduce |
|---|---|---|
| 状态灯 `.dot-green` | `animation: pulse 2s infinite` | `pulse 0.00001s`，`iteration-count 1` |
| L3 `.critical-banner` | `animation: pulse 1.5s infinite` | `pulse 0.00001s`，`iteration-count 1` |
| 过渡 `transition-duration` | `0s`（该元素本无过渡） | `0.00001s`（全局 `*` 规则） |

`animation-duration` 从秒级降到 1e-5 s 且迭代 1 次，视觉上等价静态。对比截图：
`item5-overview-{no-preference,reduce}.png`、`item5-alarms-{no-preference,reduce}.png`
（报警页两档都等到 L3 横幅实际出现后再截）。

本项用的是媒体特性模拟，不是改系统"动画效果"开关；两者在 Chromium 走同一条 `prefers-reduced-motion`
判定，但操作系统级设置未单独验证。

## 6. 等宽字体实际渲染

**已通过，实际命中 Cascadia Mono，未回退 Courier New。** `--font-mono` 声明为
`'Cascadia Mono', Consolas, 'Courier New', monospace`。判定用 Canvas `measureText` 在同字号下量同一串
字符的宽度，比较"实际生效字体族"与三个候选各自的宽度：

| 位置 | 文本样例 | 字号 | 实际宽度 | Cascadia Mono | Consolas | Courier New | 等宽 |
|---|---|---|---|---|---|---|---|
| 总览 KPI 数字 `.kpi-val` | `25.0℃` | 38px | 445.312 | **445.312** | 417.852 | 456.074 | 是 |
| 历史试验编号 `.mono` | `T20260919-008` | 13px | 152.344 | **152.344** | 142.949 | 156.025 | 是 |
| 历史时间戳 `td.mono` | `2026-09-19 16:30:00` | 11px | 128.906 | **128.906** | 120.957 | 132.021 | 是 |
| 当前试验计时 `.elapsed` | `⏱ —` | 14px | 164.062 | **164.062** | 153.945 | 168.027 | 是 |
| 当前试验过程量 `.proc-val` | `0.00 / 0.00 L/min` | 14px | 164.062 | **164.062** | 153.945 | 168.027 | 是 |
| 报警码 `.mono` | `ALM-OVERTEMP` | 12px | 140.625 | **140.625** | 131.953 | 144.023 | 是 |

实际宽度与 Cascadia Mono 逐位相等、与 Consolas 和 Courier New 都不等，即栈的第一顺位生效。
`document.fonts.check` 对 Cascadia Mono / Consolas / Courier New / Segoe UI 均为 `true`（三个候选都在，
不是"只剩一个只能选它"）。同字号四行并排样张见 `item6-font-stack-compare.png`，肉眼可见
`var(--font-mono)` 行与 Cascadia Mono 行完全重合、与 Courier New 行明显不同。

截图：`item6-overview-kpi{,-zoom}.png`、`item6-history-ids{,-zoom}.png`、`item6-test-mono.png`、
`item6-test-proc-zoom.png`。

当前试验页的 `.test-id`（试验编号）在本次截图时无进行中试验故未渲染；试验编号的等宽渲染以
历史试验页的 `T20260919-008` 为证。

## 7. WebView2 桌面壳与浏览器双入口

**已通过。** 用户先卸载了机上既有的 0.3.0（`manifest.commit = 404ecb50…`，与被测提交不同）并删除
其数据目录，机器回到无既有安装的状态，本项随后按真实安装流程执行。逐项原始输出见
`results-item7-install.json` / `results-item7-compare.json` / `results-item7-colorprofile.json` /
`results-item7-offline-probe.ndjson` / `results-item7-uninstall.json`。

### 7.1 中文安装目录下的真实首次安装

安装前复核机器干净：无 `SmdHmi` 服务、无 `HKLM\SOFTWARE\SmdHmi`、无 `SmdHmi*` 计划任务、
无 `C:\ProgramData\SmdHmi`、8000 端口无监听、目标安装目录不存在（脚本把这些做成硬前置，
不满足就拒绝安装）。

静默安装到中文目录（NSIS 的 `/D` 放最后且不加引号）：

```text
SmdHmi-0.3.0-windows-x64.exe /S /D=C:\Program Files\熔滴炉上位机
```

| 检查项 | 实际 |
|---|---|
| 安装器退出码 / 耗时 | `0` / 60.0 s |
| 服务 | `SmdHmi`，`Running`，账户 `NT AUTHORITY\LocalService` |
| 服务可执行文件 | `C:\Program Files\熔滴炉上位机\versions\0.3.0\SmdService\SmdService.exe`（在中文安装目录内）|
| 注册表登记 | `InstallDir = C:\Program Files\熔滴炉上位机`、`DataDir = C:\ProgramData\SmdHmi`、`Version = 0.3.0` |
| 安装后清单 | `version 0.3.0`、`commit 0515727be58fec05ba02fa7129b65406d8264917`、`prerelease false`、`webview2_version 152.0.4191.62` |
| 版本目录 | 974.2 MB，含 `frontend,SmdDesktop,SmdService,SmdUpdate,webview2,manifest.json,sbom.cdx.json,…` |
| 开始菜单 | `SMD HMI.lnk` |
| 健康检查 | `status ready`、`version 0.3.0`、`database/schema/storage/backup` 均 `ok`、`hostcomm offline` |
| 静态页与实际 JS 资源 | `/login` 200；`/assets/index-CunTPT79.js` 200、`Content-Type` 为 JavaScript、180 291 字节 |
| 初始口令 | 安装器在 `C:\ProgramData\SmdHmi\config\bootstrap-admin-password.txt` 生成；首登 `must_change_password=true`，改密后重登 `false` |
| 新库为空 | 试验数 0，用户只有 `admin` —— 走的是首次安装路径，没有沿用任何旧数据 |

`hostcomm offline` 是预期状态：新安装默认 HostComm 2.0、未配对，**全程没有真实控制板**。
安装器生成的一次性口令只在提权脚本进程内使用，未写入任何证据文件或日志。

`/assets/index-CunTPT79.js` 与第 1—6 项里后端托管的 CI `frontend-dist` 工件是同一个文件名和字节，
即桌面壳、浏览器与前六项测的是同一套前端资源。

### 7.2 桌面壳与浏览器双入口同页同主题

桌面壳直接运行已安装的 `versions\0.3.0\SmdDesktop\SmdDesktop.exe`（不注入任何环境变量，
由程序自己读注册表登记的 `DataDir`），窗口 1440×900 逻辑像素、本机 200% 缩放即 2880×1800 物理像素。
浏览器侧用同尺寸窗口，并经**与桌面壳完全相同**的 DPI 感知 GDI 屏幕采集，保证取色同口径。

| 入口 | 登录页 | 总览·深色 | 总览·浅色 |
|---|---|---|---|
| WebView2 桌面壳 | `item7-installed-shell-login.png` | `item7-installed-shell-overview-dark.png` | `item7-installed-shell-overview-light.png` |
| 浏览器（GDI 采集） | — | `item7-installed-browser-gdi-overview-dark.png` | `item7-installed-browser-gdi-overview-light.png` |
| 浏览器（去掉强制 sRGB） | — | `item7-installed-browser-nosrgb-overview-dark.png` | `item7-installed-browser-nosrgb-overview-light.png` |

目检：两个入口的版面、间距、字体、语义配色一致——KPI 卡、CO 橙色角标、侧栏分组、图表与
“未知 / 数据过期”离线态在两侧表现相同。

**取色差异的归因做了对照实验。** 初次比对时，桌面壳截图里高饱和令牌色与 Playwright 驱动的
Edge 有 3–6/255 的偏移。怀疑来自色彩管理而非样式，于是只改一个变量复测：用
`ignore_default_args=["--force-color-profile=srgb"]` 去掉 Playwright 默认注入的 sRGB 强制，
其余（同一后端、同一页面、同一主题、同一窗口尺寸、同一 GDI 采集）不变：

| 令牌 | 桌面壳观测 | 浏览器·强制 sRGB | 逐通道差 | 浏览器·去掉强制 | 逐通道差 |
|---|---|---|---|---|---|
| `--bg-base` | `12,14,20` | `11,14,21` | 1 | `12,14,20` | **0** |
| `--bg-chrome` | `18,20,28` | `16,20,29` | 2 | `18,20,28` | **0** |
| `--text-pri` | `234,237,245` | `232,237,246` | 2 | `234,237,245` | **0** |
| `--accent` | `56,192,245` | `56,189,248` | 3 | `56,192,245` | **0** |

去掉强制 sRGB 后逐通道差全部归零，**偏差来自色彩管理配置而不是样式差异**，这一点已实测坐实，
不再是推测。另外两侧加载的 CSS 字节由 7.1 的资源一致性保证完全相同，样式层面不存在差异空间。

### 7.3 断网离线

探针脚本打开桌面壳并登录后，每 10 秒记录一次：物理网卡状态与连接配置文件、后台健康检查、
静态页与其引用的 JS 资源、桌面壳进程存活与窗口截图。操作员在运行期间关闭 Wi-Fi 再恢复。

| 阶段 | 采样点 | 物理网卡 Up | Internet 连通性 | 健康检查 | `/login` | `/assets/*.js` | 桌面壳 |
|---|---|---|---|---|---|---|---|
| 断网前 | tick 0–8（9 点，约 90 s） | 1 | 有（`USTB_Wi-Fi=Internet`）| `ready` / db `ok` / storage `ok` | 200 | 200 | 存活 |
| **断网中** | **tick 9–27（19 点，约 190 s）** | **0** | **无（仅剩 `LocalNetwork` 虚拟适配器）** | **`ready` / db `ok` / storage `ok`** | **200** | **200** | **存活** |
| 恢复后 | tick 28–29 | 1 | 有 | `ready` | 200 | 200 | 存活 |

离线 19 个采样点全部通过，无一次降级或报错。截图对照：
`item7-offline-t005-online-before.png`（断网前）、`item7-offline-t018-offline.png`（断网中，
任务栏网络图标已变为无网络状态、页面仍完整渲染且保持登录）、
`item7-offline-t029-online-after.png`（恢复后）。完整逐点日志见 `results-item7-offline-probe.ndjson`。

口径说明：这里的“断网”是关闭本机物理网卡（Wi-Fi），验证的是**已安装实例在无外网时照常可用**；
不等于“从未联网的全新机器上安装”，安装过程本身是在有网环境完成的。

### 7.4 卸载与清理

静默卸载 `C:\Program Files\熔滴炉上位机\Uninstall.exe /S`，退出码 `0`：

| 对象 | 卸载前 | 卸载后 |
|---|---|---|
| `SmdHmi` 服务 | 有 | 无 |
| `HKLM\SOFTWARE\SmdHmi` | 有 | 无 |
| `SmdHmi*` 计划任务 | 1 | 0 |
| 开始菜单项 | 1 | 0 |
| 8000 端口监听 | 1 | 0 |
| 版本目录（974 MB） | 有 | 无 |
| 安装目录本身 | 有 | **仍在，只剩空的 `.staging`**（见 F-2）|
| `C:\ProgramData\SmdHmi` | 有 | 已随 `-RemoveDataDir` 一并删除 |

数据目录是本轮空库首次安装产生的，里面只有验收过程的内容，故一并删除，删除前的条目清单记在
`results-item7-uninstall.json`。C 盘可用空间回到 3.5 GB。

卸载器进程 0.3 秒即返回（NSIS 惯例：把自身复制到临时目录后由副本执行），因此上表“卸载后”是
返回后再等 5 秒采集的结果，不是靠进程退出时刻判定的。

### 7.5 本项未覆盖

- **升级与回滚**：本轮是首次安装路径，没有验证覆盖升级、维护事务或失败回退。
- **真实设备**：`hostcomm` 全程 `offline`、未配对，不代表 HostComm 联调或安全联锁验收。
- **TEST-REINSTALL 备份流程**：机上已无既有安装可备份（注册登记已被先前的卸载清除，
  `reset-test-installation.ps1` 需要从注册表读取已登记安装，此时不适用）。
- **代码签名与现场信任**：未验证（对应 M4-05b，仍 blocked）。
- **断网安装**：只验证了已安装实例的离线可用性，见 7.3 口径说明。

## 发现的问题

### F-1 登录失败时把后端校验错误的 Python 字面量直接显示给操作员

- **严重度**：中（界面缺陷，非安全问题）。与 PR #84 的视觉改动无关，是被测提交上既有的行为。
- **复现**：登录页用户名填任意非 `[A-Za-z0-9_]` 字符（例如中文"面条"），密码随意，提交。
- **现象**：错误条显示
  `[{'location': 'body.username', 'message': "String should match pattern '^[A-Za-z0-9_]+$'", 'type': 'string_pattern_mismatch'}]`
  ——Python `repr` 出来的列表，含内部字段名与正则。截图 `finding-login-raw-validation-error.png`。
  最初是在 WebView2 桌面壳里撞到的：本机中文输入法把自动输入的 `maint1` 转成了"面条"，
  壳内当场显示出同一段字面量。两个入口渲染同一套前端资源，表现一致。
- **成因**：`backend/app/main.py:482` 用 `err("validation_error", str(safe_errors))` 把结构化错误列表
  `str()` 成一个字符串塞进 `message`；`frontend/src/pages/LoginPage.vue:23` 直接取
  `e.response?.data?.message` 渲染。前端已有的 `apiErrorMessage()` 能把 `detail` 数组拼成中文提示，
  但后端并没有把数组放在可解析的位置。
- **修复建议（未实施）**：后端把结构化列表放到可解析字段（如 `detail`）并让 `message` 保持一句
  面向操作员的中文；或前端登录页改用 `apiErrorMessage()`。本分支是验收分支，未改主线代码。

### F-2 卸载后残留空的安装目录与 `.staging` 子目录

- **严重度**：低（清理不彻底，不影响功能与重装）。
- **复现**：安装 0.3.0 后以 `Uninstall.exe /S` 静默卸载，退出码 `0`。
- **现象**：服务、注册表登记、计划任务、开始菜单项、版本目录都清理干净了，但安装目录本身留下，
  里面剩一个空的 `.staging` 子目录。本轮在 `C:\Program Files\熔滴炉上位机` 复现；用户此前卸载
  上一套安装后，`C:\Program Files\SmdHmi\.staging` 也以同样形态留着——同一现象出现过两次，
  与安装目录名无关。
- **影响**：残留目录为空，不阻止再次安装（本轮的前置检查只看服务/注册表/数据目录/端口，
  已实测可以在 `SmdHmi\.staging` 存在的情况下正常装到另一个目录）。仅是卸载后现场不干净。
- **修复建议（未实施）**：卸载器在删除版本目录后，若 `.staging` 为空则一并删除，并在安装目录
  为空时移除该目录。本分支未改主线代码。

## 本次验收不代表什么

- 不代表真实 STM32、硬接线联锁、工艺安全或 GB/T 符合性通过：第 1—6 项只连仓内 Mock 控制板，
  第 7 项的安装实例未配对、`hostcomm` 全程 `offline`。
- 第 7 项只覆盖首次安装路径，不代表覆盖升级、维护事务、失败回退或代码签名/现场信任验收
  （逐条见 7.5）。
- 历史试验与数据分析页的内容来自**合成夹具**，不是真实试验记录；相关指标数值无工艺含义。
- 不代表屏幕阅读器、完整 WCAG 或操作系统级"动画效果"开关的验收。
- 浏览器侧只覆盖 Edge 153；仓内锁定的 Playwright Chromium 151 在本机无法启动，未在该版本上复核。
