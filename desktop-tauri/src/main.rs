#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod policy;
mod probe;

use policy::{Diagnostic, FailureKind};
use serde::Serialize;
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
};
use tauri::{
    menu::{Menu, MenuItem},
    Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
};

struct ShellState {
    data_root: PathBuf,
    origin: Mutex<Option<url::Url>>,
    diagnostic: Mutex<Option<Diagnostic>>,
    first_load: AtomicBool,
    connecting: AtomicBool,
    self_check: bool,
}

fn local_diagnostics(url: &url::Url) -> bool {
    matches!(
        (url.scheme(), url.host_str(), url.path()),
        ("tauri", Some("localhost"), "/index.html" | "/")
            | (
                "http" | "https",
                Some("tauri.localhost"),
                "/index.html" | "/"
            )
    ) && url.port().is_none()
        && url.query().is_none()
        && url.fragment().is_none()
        && url.username().is_empty()
        && url.password().is_none()
}

fn require_local(window: &WebviewWindow) -> Result<(), String> {
    if window.label() == "main" && window.url().is_ok_and(|u| local_diagnostics(&u)) {
        Ok(())
    } else {
        Err("仅内置诊断页可使用此功能".into())
    }
}

#[derive(Serialize)]
struct DiagnosticView {
    diagnostic: Option<Diagnostic>,
    auto_connect: bool,
    self_check: bool,
}

#[tauri::command]
fn diagnostic_status(
    window: WebviewWindow,
    state: tauri::State<ShellState>,
) -> Result<DiagnosticView, String> {
    require_local(&window)?;
    Ok(DiagnosticView {
        diagnostic: state.diagnostic.lock().unwrap().clone(),
        auto_connect: state.first_load.swap(false, Ordering::SeqCst),
        self_check: state.self_check,
    })
}

#[tauri::command]
fn self_check_rendered(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: tauri::State<ShellState>,
) -> Result<(), String> {
    require_local(&window)?;
    if !state.self_check {
        return Err("未启用渲染自检".into());
    }
    app.exit(0);
    Ok(())
}

struct ConnectingGuard<'a>(&'a AtomicBool);
impl Drop for ConnectingGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::SeqCst);
    }
}

#[tauri::command]
async fn connect_service(
    window: WebviewWindow,
    state: tauri::State<'_, ShellState>,
) -> Result<(), Diagnostic> {
    require_local(&window).map_err(|_| {
        Diagnostic::new(
            FailureKind::ConfigurationInvalid,
            "仅内置诊断页可重试连接。",
        )
    })?;
    if state.connecting.swap(true, Ordering::SeqCst) {
        return Err(Diagnostic::new(
            FailureKind::ServiceUnreachable,
            "正在检查连接，请稍候。",
        ));
    }
    let _guard = ConnectingGuard(&state.connecting);
    let result = async {
        let origin = policy::read_origin(&state.data_root)?;
        probe::check_service(&origin).await?;
        *state.origin.lock().unwrap() = Some(origin.clone());
        window.navigate(origin).map_err(|_| {
            Diagnostic::new(FailureKind::PageLoadFailed, "无法加载后台页面，请重试。")
        })?;
        Ok(())
    }
    .await;
    *state.diagnostic.lock().unwrap() = result.as_ref().err().cloned();
    result
}

fn data_root() -> Result<PathBuf, Box<dyn std::error::Error>> {
    if let Some(path) = std::env::var_os("SMD_DATA_ROOT") {
        let path = PathBuf::from(path);
        if !path.is_absolute() {
            return Err("SMD_DATA_ROOT 必须是绝对路径".into());
        }
        return Ok(path);
    }
    #[cfg(windows)]
    {
        use winreg::{
            enums::{HKEY_LOCAL_MACHINE, KEY_READ, KEY_WOW64_64KEY},
            RegKey,
        };
        if let Ok(key) = RegKey::predef(HKEY_LOCAL_MACHINE)
            .open_subkey_with_flags("Software\\SmdHmi", KEY_READ | KEY_WOW64_64KEY)
        {
            if let Ok(value) = key.get_value::<String, _>("DataDir") {
                let path = PathBuf::from(value);
                if !path.is_absolute() {
                    return Err("注册 DataDir 必须是绝对路径".into());
                }
                return Ok(path);
            }
        }
    }
    Ok(PathBuf::from(std::env::var_os("PROGRAMDATA").ok_or("缺少 PROGRAMDATA")?).join("SmdHmi"))
}

fn main() {
    let self_check = std::env::args().any(|arg| arg == "--self-check");
    if let Err(error) = run(self_check) {
        // Never include file contents, tokens or service.env in diagnostics.
        if !self_check {
            native_error(&error.to_string());
        }
        std::process::exit(2);
    }
}

fn run(self_check: bool) -> Result<(), Box<dyn std::error::Error>> {
    let exe = std::env::current_exe()?;
    let version = exe
        .parent()
        .and_then(|p| p.parent())
        .ok_or("无法确定版本目录")?;
    let runtime = loop {
        match policy::require_runtime(version) {
            Ok(runtime) => break runtime,
            Err(error) => {
                // No WebView can display a page when its runtime is absent.
                if self_check || !native_retry(&error.message) {
                    return Err(error.message.into());
                }
            }
        }
    };
    // Set before any WebView/threads exist. Do not inherit flags disabling TLS/web security.
    std::env::set_var("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", runtime);
    std::env::remove_var("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS");
    std::env::remove_var("WEBVIEW2_RELEASE_CHANNEL_PREFERENCE");
    let profile = PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or("缺少 LOCALAPPDATA")?)
        .join("SmdHmi/TauriWebView2");
    let state = ShellState {
        data_root: data_root()?,
        origin: Mutex::new(None),
        diagnostic: Mutex::new(None),
        first_load: AtomicBool::new(!self_check),
        connecting: AtomicBool::new(false),
        self_check,
    };
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            // Ignore incoming arguments: a second process can only activate the window.
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .manage(state)
        .invoke_handler(tauri::generate_handler![
            diagnostic_status,
            connect_service,
            self_check_rendered
        ])
        .setup(move |app| {
            let diagnostics =
                MenuItem::with_id(app, "diagnostics", "连接诊断", true, Some("Ctrl+Shift+D"))?;
            app.set_menu(Menu::with_items(app, &[&diagnostics])?)?;
            app.on_menu_event(|app, event| {
                if event.id().as_ref() == "diagnostics" {
                    if let Some(window) = app.get_webview_window("main") {
                        let _ =
                            window.navigate("http://tauri.localhost/index.html".parse().unwrap());
                    }
                }
            });
            let handle = app.handle().clone();
            let window =
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("SMD 软熔滴落实验")
                    .inner_size(1440.0, 900.0)
                    .min_inner_size(1100.0, 700.0)
                    .visible(!self_check)
                    .devtools(false)
                    .data_directory(profile)
                    .on_navigation(move |target| {
                        local_diagnostics(target)
                            || handle
                                .state::<ShellState>()
                                .origin
                                .lock()
                                .unwrap()
                                .as_ref()
                                .is_some_and(|origin| policy::allows_navigation(origin, target))
                    })
                    .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
                    .build()?;
            #[cfg(windows)]
            watch_navigation(&window)?;
            if self_check {
                let handle = app.handle().clone();
                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_secs(20));
                    handle.exit(2);
                });
            }
            Ok(())
        })
        .run(tauri::generate_context!())?;
    Ok(())
}

/// WebView2 reports navigation failure separately from Tauri's page-finished event.
#[cfg(windows)]
fn watch_navigation(window: &WebviewWindow) -> tauri::Result<()> {
    use webview2_com::{Microsoft::Web::WebView2::Win32::*, NavigationCompletedEventHandler};
    let window = window.clone();
    let callback_window = window.clone();
    window.with_webview(move |platform| {
        let handler = NavigationCompletedEventHandler::create(Box::new(move |_, args| {
            let Some(args) = args else {
                return Ok(());
            };
            let mut success = windows::core::BOOL::default();
            let mut status = COREWEBVIEW2_WEB_ERROR_STATUS_UNKNOWN;
            // SAFETY: WebView2 invokes this COM callback on its owning UI thread;
            // both output pointers remain valid until these calls return.
            unsafe {
                args.IsSuccess(&mut success)?;
                args.WebErrorStatus(&mut status)?;
            }
            if !success.as_bool() && status != COREWEBVIEW2_WEB_ERROR_STATUS_OPERATION_CANCELED {
                let state = callback_window.state::<ShellState>();
                let error = match status {
                    COREWEBVIEW2_WEB_ERROR_STATUS_CERTIFICATE_COMMON_NAME_IS_INCORRECT
                    | COREWEBVIEW2_WEB_ERROR_STATUS_CERTIFICATE_EXPIRED
                    | COREWEBVIEW2_WEB_ERROR_STATUS_CERTIFICATE_IS_INVALID
                    | COREWEBVIEW2_WEB_ERROR_STATUS_CERTIFICATE_REVOKED
                    | COREWEBVIEW2_WEB_ERROR_STATUS_CLIENT_CERTIFICATE_CONTAINS_ERRORS => {
                        Diagnostic::new(
                            FailureKind::CertificateError,
                            "页面证书校验失败。请核对证书与本机地址，然后重试。",
                        )
                    }
                    _ => Diagnostic::new(
                        FailureKind::PageLoadFailed,
                        "后台页面未能加载。请核对服务连接，然后重试。",
                    ),
                };
                *state.diagnostic.lock().unwrap() = Some(error);
                let window = callback_window.clone();
                // Queue a new navigation after the COM event finishes.
                let app = window.app_handle().clone();
                let _ = app.run_on_main_thread(move || {
                    if window.url().is_ok_and(|url| !local_diagnostics(&url)) {
                        let _ =
                            window.navigate("http://tauri.localhost/index.html".parse().unwrap());
                    }
                });
            }
            Ok(())
        }));
        let mut token = 0;
        // SAFETY: controller and callback belong to this UI-thread WebView.
        // The WebView retains the handler until the window is destroyed.
        let result = unsafe {
            platform
                .controller()
                .CoreWebView2()
                .and_then(|view| view.add_NavigationCompleted(&handler, &mut token))
        };
        if result.is_err() {
            callback_install_failed();
        }
    })
}

#[cfg(windows)]
fn callback_install_failed() {
    native_error("无法安装页面加载诊断回调，请关闭窗口并联系维护人员。");
}

#[cfg(windows)]
fn native_retry(message: &str) -> bool {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        MessageBoxW, IDRETRY, MB_ICONERROR, MB_RETRYCANCEL,
    };
    let text: Vec<u16> = format!("{message}\n\n后台采集独立运行。\0")
        .encode_utf16()
        .collect();
    let title: Vec<u16> = "SMD · 运行时缺失\0".encode_utf16().collect();
    // SAFETY: both strings are NUL-terminated and remain alive throughout this synchronous call.
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            text.as_ptr(),
            title.as_ptr(),
            MB_ICONERROR | MB_RETRYCANCEL,
        ) == IDRETRY
    }
}
#[cfg(not(windows))]
fn native_retry(_: &str) -> bool {
    false
}

#[cfg(windows)]
fn native_error(message: &str) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};
    let text: Vec<u16> = format!("无法打开界面。后台采集独立运行。\n{message}\0")
        .encode_utf16()
        .collect();
    let title: Vec<u16> = "SMD · 启动诊断\0".encode_utf16().collect();
    // SAFETY: pointers reference valid NUL-terminated UTF-16 strings for the duration of the call.
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            text.as_ptr(),
            title.as_ptr(),
            MB_ICONERROR | MB_OK,
        );
    }
}
#[cfg(not(windows))]
fn native_error(message: &str) {
    eprintln!("{message}");
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bundled_ipc_cannot_be_claimed_by_remote_navigation() {
        for input in [
            "http://tauri.localhost:8000/",
            "https://tauri.localhost.evil/",
            "http://localhost/",
            "http://tauri.localhost/login",
            "http://user@tauri.localhost/",
            "http://tauri.localhost/?url=https://evil",
        ] {
            assert!(!local_diagnostics(&input.parse().unwrap()));
        }
        assert!(local_diagnostics(
            &"http://tauri.localhost/index.html".parse().unwrap()
        ));
    }
}
