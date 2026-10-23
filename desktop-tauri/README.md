# Tauri 2 普通用户桌面壳

阶段状态见[任务台账](../tasks/tauri-modernization.md)。此工程加载独立后台提供的 Vue 页面，不读取业务库、设备密钥或 service.env，不启动、停止或升级服务。旧壳保留用于对照；实际 WebView2、下载/打印、安装和窗口生命周期通过前，不视为替换完成。

## 配置与安装布局

保持 `<版本目录>/SmdDesktop/SmdDesktop.exe`、`<版本目录>/webview2/`。
数据根目录沿用 `SMD_DATA_ROOT`（绝对路径）、64 位 HKLM `Software\SmdHmi` 的 `DataDir`、`%PROGRAMDATA%/SmdHmi` 的优先级。只读取 `client.json`：

```json
{ "url": "http://127.0.0.1:8000" }
```

安装的 HTTPS 地址保留原协议和端口，不自动降级。配置仅允许本机 origin；不能包含凭据、页面路径、查询或片段。页面导航限制为该 origin。远程业务内容没有原生 IPC 权限；内置诊断仅可检查连接及重试。菜单“连接诊断”（Ctrl+Shift+D）可返回内置页。第二个进程只激活已有窗口，不处理其地址参数。

Fixed WebView2 必须来自现有 [运行时锁](../desktop/webview2.lock.json) 及发布脚本的哈希、微软签名与版本核验；不使用系统 Evergreen 回退。运行时不存在时只能使用原生提示框，可修复安装后点击重试。其余诊断通过内置 HTML 显示，不泄露配置原文或认证信息。

## 开发检查

Windows MSVC、Windows SDK 和根目录锁定 Rust 工具链：

```powershell
cargo fmt --manifest-path desktop-tauri/Cargo.toml --check
cargo clippy --manifest-path desktop-tauri/Cargo.toml --locked --all-targets -- -D warnings
cargo test --manifest-path desktop-tauri/Cargo.toml --locked
cargo build --manifest-path desktop-tauri/Cargo.toml --locked --release
```

`--self-check` 必须从完整布局运行：创建隐藏 WebView，加载内置脚本并完成受限 IPC 往返后退出 0；运行时缺失或 20 秒未完成则失败。自检不连接业务服务。编译成功不等于此项验收。

版本由后端真源校验，Cargo 与 Tauri 配置保持一致。此工程不提供独立 updater 或另一个 NSIS 安装器，样包继续走原维护事务。P1 未验项记录在[验证记录](../docs/verification/2026-10-23-tauri-p1.md)。

阶段样包使用 `scripts/release/build-windows.ps1 -OutputDir artifacts/tauri-sample -DesktopShell tauri`，默认构建仍为原 pywebview 对照。Tauri 样包同样执行三个入口自检、迁移探针、清单/SBOM、NSIS；只替换壳，保留服务和维护工具。Cargo 锁随壳归档并加入依赖清单。
