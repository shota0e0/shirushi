use std::fs;
use std::io;
use std::path::{Path, PathBuf};

const ASSET_ALLOWLIST: &[(&str, &str)] = &[
    ("desktop.html", "index.html"),
    ("desktop.js", "desktop.js"),
    ("styles.css", "styles.css"),
    ("bootstrap.js", "bootstrap.js"),
    ("shell.js", "shell.js"),
    ("contracts.js", "contracts.js"),
    ("generation-guard.js", "generation-guard.js"),
    ("i18n.js", "i18n.js"),
    ("mark.js", "mark.js"),
    ("motion.js", "motion.js"),
    ("adapters/browser-foundation-adapter.js", "adapters/browser-foundation-adapter.js"),
    ("adapters/desktop-adapter.js", "adapters/desktop-adapter.js"),
    ("desktop/transport.js", "desktop/transport.js"),
    ("desktop/contract.js", "desktop/contract.js"),
    ("desktop/presentation.js", "desktop/presentation.js"),
    ("desktop/i18n.js", "desktop/i18n.js"),
    ("desktop/controller.js", "desktop/controller.js"),
    ("personal-mark-v2/index.js", "personal-mark-v2/index.js"),
    ("personal-mark-v2/contract.js", "personal-mark-v2/contract.js"),
    ("personal-mark-v2/parser.js", "personal-mark-v2/parser.js"),
    ("personal-mark-v2/errors.js", "personal-mark-v2/errors.js"),
    ("personal-mark-v2/profile-registry.js", "personal-mark-v2/profile-registry.js"),
    ("personal-mark-v2/typed-save.js", "personal-mark-v2/typed-save.js"),
    ("personal-mark-v2/geometry.js", "personal-mark-v2/geometry.js"),
    ("personal-mark-v2/embedding.js", "personal-mark-v2/embedding.js"),
    ("personal-mark-v2/legacy.js", "personal-mark-v2/legacy.js"),
    ("personal-mark-v2/unicode16.js", "personal-mark-v2/unicode16.js"),
    ("personal-mark-v2/unicode16-data.js", "personal-mark-v2/unicode16-data.js"),
    ("personal-mark-v2/Unicode-LICENSE.txt", "personal-mark-v2/Unicode-LICENSE.txt"),
];

pub fn stage_desktop_assets() -> io::Result<()> {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    stage_from(&manifest.join("../web"), &manifest.join(".generated/web"))
}

fn stage_from(source_root: &Path, destination_root: &Path) -> io::Result<()> {
    let generated_parent = destination_root.parent().ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidInput, "desktop staging path has no parent")
    })?;
    fs::create_dir_all(generated_parent)?;
    let expected_parent = fs::canonicalize(generated_parent)?;
    let manifest = fs::canonicalize(env!("CARGO_MANIFEST_DIR"))?;
    if expected_parent != manifest.join(".generated") {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "desktop staging parent escaped the expected manifest directory",
        ));
    }
    if destination_root.exists() {
        let metadata = fs::symlink_metadata(destination_root)?;
        if metadata.file_type().is_symlink() || is_reparse_point(&metadata) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "refusing to replace a linked/reparse desktop asset directory",
            ));
        }
        if fs::canonicalize(destination_root)? != expected_parent.join("web") {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "desktop asset directory resolved outside its exact staging path",
            ));
        }
        fs::remove_dir_all(destination_root)?;
    }
    fs::create_dir_all(destination_root)?;

    for (source_relative, destination_relative) in ASSET_ALLOWLIST {
        let source = source_root.join(source_relative);
        let destination = destination_root.join(destination_relative);
        println!("cargo:rerun-if-changed={}", source.display());
        let metadata = fs::metadata(&source).map_err(|error| {
            io::Error::new(
                error.kind(),
                format!("required desktop asset {} is unavailable: {error}", source.display()),
            )
        })?;
        if !metadata.is_file() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("desktop asset is not a regular file: {}", source.display()),
            ));
        }
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::copy(source, destination)?;
    }
    Ok(())
}

#[cfg(windows)]
fn is_reparse_point(metadata: &fs::Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0400;
    metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
}

#[cfg(not(windows))]
fn is_reparse_point(_: &fs::Metadata) -> bool {
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;

    #[test]
    fn production_allowlist_excludes_preview_and_test_assets() {
        let names = ASSET_ALLOWLIST
            .iter()
            .map(|(source, _)| *source)
            .collect::<BTreeSet<_>>();
        assert_eq!(names.len(), ASSET_ALLOWLIST.len());
        for name in names {
            assert!(!name.contains("dev-preview"));
            assert!(!name.starts_with("dev/"));
            assert!(!name.ends_with(".mjs"));
            assert!(!name.contains("fixture"));
            assert!(!name.contains("verify"));
            assert!(!name.ends_with("README.md"));
        }
    }
}
