#[path = "src/asset_stage.rs"]
mod asset_stage;

fn main() {
    asset_stage::stage_desktop_assets().expect("failed to stage the audited desktop Web assets");
    let attributes = tauri_build::Attributes::new()
        .app_manifest(tauri_build::AppManifest::new().commands(&[
            "bridge_get_capabilities",
            "bridge_load_personal_mark",
        ]))
        .windows_attributes(tauri_build::WindowsAttributes::new().window_icon_path("../assets/app_icon.ico"));
    tauri_build::try_build(attributes).expect("failed to prepare the Tauri application");
}
