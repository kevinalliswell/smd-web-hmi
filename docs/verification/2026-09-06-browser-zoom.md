# M1-05 浏览器缩放补充验收

验证日期：2026-09-06。被测提交为文档分支 `15bb12f48fdc9e48b8b193cf2ee945ee9d494110`，其前端与主线软件 `d995406104ff217aeb4ad5c33071bd1fb91aeac2` 相同。未修改业务代码。

固定1440×1000窗口，在macOS、Node24、Chromium151.0.7922.34独立无头配置运行实际Vue页面；HTTP、WebSocket与登录使用明确夹具。通过临时扩展调用浏览器原生 `chrome.tabs.setZoom` 并用 `getZoom` 回读，没有设置deviceScaleFactor、PageScale或CSS zoom。[Chrome API定义](https://developer.chrome.com/docs/extensions/reference/api/tabs#method-setZoom)与[Playwright扩展运行方式](https://playwright.dev/docs/chrome-extensions)提供复现依据。

| 浏览器缩放与回读 | CSS视口 | DPR | visualViewport.scale / CSS zoom | 结论 |
|---|---|---|---|---|
| 初始100% / 1 | 1440×913 | 1 | 1 / 1 | 基准 |
| 125% / 1.25 | 1152×730 | 1.25 | 1 / 1 | 通过 |
| 150% / 1.5 | 960×609 | 1.5 | 1 / 1 | 通过 |
| 200% / 2 | 720×456 | 2 | 1 / 1 | 通过 |

三档均验证：键盘访问历史/总览/系统设置，历史℃/Pa/mm/g四幅曲线及缺失点可滚动查看，页面和画布无关键横向溢出，停止/维护确认框全部位于可见视口内。确认框默认聚焦取消，双向Tab循环、Escape取消及焦点恢复通过。200%时用Tab/Enter打开折叠菜单再选择页面；不要求导航始终展开或全部曲线同时显示。

首轮脚本在200%仍等待常驻历史链接而超时，已保留诊断；改为实际折叠菜单路径后通过，未改界面代码。最终24张截图通过无clip的原生 `Page.captureScreenshot` 获取，均为1440×913像素。三档结果、缩放回读、窗口/DPR、尺寸断言与截图hash相互对应；无API修改请求、浏览器异常或失败请求。

独立证据编号：`artifact://smd-web-hmi/2026-09-06/browser-zoom-verification.zip`；SHA-256：`4ac22336044130c903ce0a0e89a2a2dfaf7e3a8451aae9d73db17c5f7c7d9f33`。本任务交付 `outputs/browser-zoom-verification.zip`，包含报告、首次诊断、最终结果、24张截图、复现脚本/最小扩展及文件校验清单，由维护负责人长期保管。原浏览器证据包未改动，见[软件验证](2026-09-06-software.md#最终集成软件验证)。临时浏览器/profile和Vite服务均已清理。

本证据补齐M1-05的浏览器缩放条件；此前的宽度回归不能替代这一项。该有界验收不代表操作系统显示缩放、Windows WebView2、屏幕阅读器、完整WCAG或真实设备验收，也不关闭M4/M5。没有发送启停或维护请求。
