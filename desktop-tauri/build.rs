fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "connect_service",
            "diagnostic_status",
            "self_check_rendered",
        ]),
    ))
    .expect("Tauri build metadata");
}
