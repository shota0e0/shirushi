#[path = "src/asset_stage.rs"]
mod asset_stage;

fn main() {
    println!("cargo:rerun-if-env-changed=SHIRUSHI_DEV_INSPECTION_MANIFEST_SHA256");
    println!("cargo:rerun-if-env-changed=SHIRUSHI_PREVIEW_INSPECTION_MANIFEST_SHA256");
    if std::env::var_os("CARGO_FEATURE_PREVIEW_RELEASE").is_some() {
        let digest = std::env::var("SHIRUSHI_PREVIEW_INSPECTION_MANIFEST_SHA256")
            .expect("Preview build requires the independently generated helper manifest digest");
        assert!(
            digest.len() == 64
                && digest
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "Preview helper manifest digest must be exactly 64 lowercase hex characters"
        );
        // Compile this immutable build input. Runtime environment and the sibling
        // manifest never supply the expected digest.
        println!("cargo:rustc-env=SHIRUSHI_PREVIEW_INSPECTION_MANIFEST_SHA256={digest}");
    }
    asset_stage::stage_desktop_assets().expect("failed to stage the audited desktop Web assets");
    let attributes = tauri_build::Attributes::new()
        .app_manifest(tauri_build::AppManifest::new().commands(&[
            "bridge_get_capabilities",
            "bridge_load_personal_mark",
            "bridge_inspect_limited",
            "bridge_select_image",
            "bridge_read_image",
            "bridge_take_explorer_request",
            "bridge_product_operation",
        ]))
        .windows_attributes(
            tauri_build::WindowsAttributes::new().window_icon_path("../assets/app_icon.ico"),
        );
    tauri_build::try_build(attributes).expect("failed to prepare the Tauri application");
}
