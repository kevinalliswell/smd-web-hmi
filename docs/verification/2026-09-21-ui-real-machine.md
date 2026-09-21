# PR #84 前端视觉体系升级 · 真机 UI 验收

本记录只覆盖已合并 PR #84 描述里列出的 7 项人工 UI 验收。第 1—6 项为浏览器侧，已在下述
Windows 11 实机执行并留证；第 7 项（WebView2 桌面壳）**未整体通过**，按用户批准的替代方案
只完成了零安装隔离验证，真实安装/卸载与断网离线仍未验收，逐项边界见[第 7 项](#7-webview2-桌面壳与浏览器双入口)。

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
| 后端 | 同提交 `backend/`，`__version__ = 0.3.0`，`HOSTCOMM_MOCK=true`，监听 `127.0.0.1:8100`，托管上述 CI `frontend-dist` |
| 设备 | 仓内 `app.hostcomm.mock_server`（`127.0.0.1:34299`，`--demo-alarms --extended-contract --time-scale 20`），**全程未接真实控制板** |
| 执行日期 | 2026-09-21 |

被测前端刻意使用 CI 产出的字节而不是本机重建产物：本机 `core.autocrlf=true`，重建后所有
文本资源的字节与内容哈希都会变（已实测 `favicon.svg` 去掉 CR 后与 CI 完全一致），用 CI 字节
才能把结论绑定到该提交的实际发行物。

本机隔离：本机另有一套**已安装**的 SmdHmi 0.3.0（`manifest.commit = 404ecb50…`，即已发布的
v0.3.0，非被测提交），服务以 `NT AUTHORITY\LocalService` 运行在 8000 端口。验收用后端另起在
8100 端口、独立 SQLite（`%LOCALAPPDATA%\..\.cache\smd-pr84\data\smd.db`）、Mock 另用 34299 端口，
全程未停止、未修改、未读写该既有安装。

## 结论一览

| # | 验收项 | 结论 |
|---|---|---|
| 1 | 深/浅主题 × 16 页面目检 | 已通过 |
| 2 | 每页切一次主题，重着色无旧色残留 | 已通过 |
| 3 | 1440×1000 窗口 125%/150%/200% 无页面级横向溢出 | 已通过 |
| 4 | 键盘/焦点回归（M1-05 基线四组） | 已通过（第四组"准备离线升级"入口已随 ADR-011 从产品移除，改测同类受控写入确认，差异见下） |
| 5 | `prefers-reduced-motion: reduce` 动画降级 | 已通过 |
| 6 | Cascadia Mono/Consolas 实际渲染 | 已通过 |
| 7 | WebView2 桌面壳与浏览器双入口 | **受阻**（仅完成零安装隔离对比；真实安装/卸载、安装器中文目录、断网离线未执行） |

另发现 1 个与本次视觉改动无关、但在被测提交上真实可见的界面缺陷，见[发现的问题](#发现的问题)。

## 复现环境与夹具

夹具脚本随本记录入库，位于 [assets/2026-09-21-ui/scripts/](assets/2026-09-21-ui/scripts/)：

| 脚本 | 作用 |
|---|---|
| `seed_ui_fixture.py` | 向验收专用 SQLite **追加**合成历史试验（两批共 16 个，每个 180 采样点），供历史页与数据分析页有内容可看。纯合成夹具，只 INSERT，不删改既有记录 |
| `prepare_device_fixture.py` | 在 Mock 控制板上保存/校验/下发一份标准候选配方（`start_test` 要求绑定已回读一致的配方版本；Mock 重启会丢该绑定） |
| `ui_acceptance.py` | 第 1—6 项的浏览器侧夹具：真实 Edge 有头窗口、真实渲染、逐项断言并输出 JSON |
| `capture_webview2_shell.ps1` | 第 7 项：运行解包出的 `SmdDesktop.exe` 并抓窗口图 |
| `capture_window_by_title.ps1` | 第 7 项：用与桌面壳相同的 DPI 感知 GDI 路径抓浏览器窗口，使取色比对同口径 |
| `compare_shell_vs_browser.py` | 第 7 项：对两侧截图做主题令牌取色统计 |

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

**受阻。** 本项要求的真实安装/卸载、安装器写入中文安装目录、断网离线三项均未执行，原因与缺口
见下。用户批准后只完成了**零安装隔离验证**，结论不得当作本项通过。

### 7.1 为什么没做真实安装

- 手头唯一的 Win10/11 机器就是本验收机 `KEVIN-X1`，其上**已有一套在用的 SmdHmi 0.3.0**
  （`manifest.commit = 404ecb50…`，与被测提交不同，不能顶替），服务在跑；清单明确要求不碰业务环境。
- 本次会话**不是管理员**，无法驱动 NSIS 安装器所需的 UAC 提权。
- 本机 C 盘仅剩约 3 GB 可用，解包后约 1.03 GB 的版本目录加上保留旧版本，安装存在空间不足风险。
- 断网离线一项会切断本会话网络。

**缺什么**：一台可牺牲的 Win10/11 测试机（或对本机的管理员授权 + 可接受覆盖现有安装），以及一个
可断网的时间窗口。补齐后应按 [deploy/windows/TEST-REINSTALL.md](../../deploy/windows/TEST-REINSTALL.md)
走完整备份、空库重装、中文目录安装、断网核验与卸载清理。

### 7.2 已完成的零安装隔离验证

用 7-Zip 解包被测安装器（不运行 NSIS），把版本目录放到**中文路径** `D:\熔滴炉UI验收-临时\版本\0.3.0\`，
用 `SMD_DATA_ROOT` 显式指向隔离数据目录（绕过注册表里已登记的安装路径），`LOCALAPPDATA` 重定向到
隔离目录，直接运行随包的 `SmdDesktop.exe`：

- `SmdDesktop.exe --self-check` 退出码 **0**：冻结壳实际加载 `webview.platforms.edgechromium`
  与随包 Fixed WebView2 `152.0.4191.62` 成功（中文程序路径下可用）。
- 壳内完成 `maint1` 登录并打开实时总览，深浅两主题各截一张；主题切换用壳内顶栏按钮点击。
- 安装器内 `frontend/index.html` 的 SHA-256 为 `10a6839f…84b`，**与后端托管给浏览器的 CI
  `frontend-dist` 字节完全一致**——两个入口渲染的是同一套前端资源。

对比截图（均为 2880×1800 物理像素、同一后端、同一页面、同一主题）：

| 入口 | 深色 | 浅色 |
|---|---|---|
| WebView2 桌面壳 | `item7-shell-overview-dark.png` | `item7-shell-overview-light.png` |
| 浏览器（Playwright 直出） | `item7-browser-overview-dark.png` | `item7-browser-overview-light.png` |
| 浏览器（与壳同一 GDI 采集路径） | `item7-browser-gdi-overview-dark.png` | `item7-browser-gdi-overview-light.png` |

另有壳的登录页截图 `item7-shell-login.png` 与浏览器对照 `item7-browser-login-{dark,light}.png`。

**目检结论：两个入口的版面、间距、字体、语义配色一致**，KPI 卡、CO 橙色角标、绿色状态灯、
侧栏分组与图表在两侧表现相同。

**取色数据及其口径说明（`results-item7-compare.json`）**：在同为 GDI 屏幕采集的两张图上，
背景/卡片/顶栏/正文色的最接近观测值逐通道差 ≤ 5（深色）/ ≤ 9（浅色）；但桌面壳截图里
`--green` / `--orange` / `--red` / `--series-1` 等高饱和色在 6/255 容差内找不到精确匹配，浏览器侧
则精确命中（如 `--orange` 观测值正好是 `rgb(249,115,22)`）。这是**色彩管理配置差异**，不是样式差异：
Playwright 启动 Edge 时固定加 `--force-color-profile=srgb`，桌面壳没有该开关，走显示器 ICC 配置；
而两侧加载的 CSS 与令牌取值由上面的字节一致性保证完全相同。该解释未通过"直接启动无该开关的 Edge
再对比"独立坐实（尝试时未能拿到该窗口），按未验证项记录。

### 7.3 隔离与清理

- 未安装/卸载任何程序，未创建或修改 `SmdHmi` 服务与 `SmdHmi-Recover` 任务，未写注册表、开始菜单、
  防火墙规则。
- 未读写既有 `C:\ProgramData\SmdHmi` 与 `C:\Program Files\SmdHmi`；验收后复核两者最后写入时间仍为
  2026-09-20，服务仍以原路径运行。
- `%LOCALAPPDATA%\SmdHmi` 为既有安装于 2026-09-06 创建，非本次产生。
- 临时根目录 `D:\熔滴炉UI验收-临时`（解包产物、隔离数据目录、WebView2 用户资料）已整体删除。
- 壳内登录用剪贴板粘贴（本机中文 IME 会把逐字符 `SendKeys` 转成中文，实测 `maint1` 变成"面条"），
  脚本在结束前恢复了原剪贴板文本。

## 发现的问题

### F-1 登录失败时把后端校验错误的 Python 字面量直接显示给操作员

- **严重度**：中（界面缺陷，非安全问题）。与 PR #84 的视觉改动无关，是被测提交上既有的行为。
- **复现**：登录页用户名填任意非 `[A-Za-z0-9_]` 字符（例如中文"面条"），密码随意，提交。
- **现象**：错误条显示
  `[{'location': 'body.username', 'message': "String should match pattern '^[A-Za-z0-9_]+$'", 'type': 'string_pattern_mismatch'}]`
  ——Python `repr` 出来的列表，含内部字段名与正则。截图 `finding-login-raw-validation-error.png`。
  桌面壳里同样可见（见 7.2 的登录过程），两个入口一致。
- **成因**：`backend/app/main.py:482` 用 `err("validation_error", str(safe_errors))` 把结构化错误列表
  `str()` 成一个字符串塞进 `message`；`frontend/src/pages/LoginPage.vue:23` 直接取
  `e.response?.data?.message` 渲染。前端已有的 `apiErrorMessage()` 能把 `detail` 数组拼成中文提示，
  但后端并没有把数组放在可解析的位置。
- **修复建议（未实施）**：后端把结构化列表放到可解析字段（如 `detail`）并让 `message` 保持一句
  面向操作员的中文；或前端登录页改用 `apiErrorMessage()`。本分支是验收分支，未改主线代码。

## 本次验收不代表什么

- 不代表第 7 项通过：真实安装/卸载、安装器中文安装目录、断网离线均未执行。
- 不代表真实 STM32、硬接线联锁、工艺安全或 GB/T 符合性通过：全程只连仓内 Mock 控制板。
- 历史试验与数据分析页的内容来自**合成夹具**，不是真实试验记录；相关指标数值无工艺含义。
- 不代表屏幕阅读器、完整 WCAG 或操作系统级"动画效果"开关的验收。
- 浏览器侧只覆盖 Edge 153；仓内锁定的 Playwright Chromium 151 在本机无法启动，未在该版本上复核。
