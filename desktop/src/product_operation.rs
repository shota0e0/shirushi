//! Development product operations reuse the existing single-owner supervisor.
//! Helper verifies a private stage; only this controller publishes no-clobber.
#![cfg(all(windows, any(debug_assertions, feature = "preview-release")))]
use crate::{
    inspection_protocol::{self, HelperOutcome, RequestIdentity, ServiceFailure},
    inspection_supervisor::Control,
    limited_inspection::{pin_input, resolve_compiled_package},
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Read,
    os::windows::fs::{MetadataExt, OpenOptionsExt},
    path::{Path, PathBuf},
    time::Duration,
};

fn regular(metadata: &fs::Metadata) -> bool {
    metadata.is_file() && metadata.file_attributes() & 0x400 == 0
}
fn bytes(path: &Path, cap: u64) -> Result<Vec<u8>, ServiceFailure> {
    let _guard = pin_input(path)?;
    let mut raw = Vec::new();
    fs::File::open(path)
        .map_err(|_| ServiceFailure::InputUnavailable)?
        .take(cap + 1)
        .read_to_end(&mut raw)
        .map_err(|_| ServiceFailure::InputUnavailable)?;
    if raw.len() as u64 > cap {
        return Err(ServiceFailure::ResourceLimitExceeded);
    }
    Ok(raw)
}
fn hash(raw: &[u8]) -> String {
    format!("{:x}", Sha256::digest(raw))
}
pub(crate) fn output_for(input: &Path) -> Result<PathBuf, ServiceFailure> {
    let stem = input
        .file_stem()
        .and_then(|s| s.to_str())
        .ok_or(ServiceFailure::InputUnavailable)?;
    let extension = input
        .extension()
        .and_then(|s| s.to_str())
        .ok_or(ServiceFailure::UnsupportedFormat)?;
    Ok(input.with_file_name(format!("{stem}_rights.{extension}")))
}
struct Stage {
    root: PathBuf,
    file: PathBuf,
    guard: Option<fs::File>,
    cleaned: bool,
}
impl Stage {
    fn new(output: &Path, id: RequestIdentity) -> Result<Self, ServiceFailure> {
        let parent = output.parent().ok_or(ServiceFailure::InputUnavailable)?;
        let root = parent.join(format!(
            ".shirushi-add-{}-{}-{}",
            std::process::id(),
            id.request,
            id.generation
        ));
        fs::create_dir(&root).map_err(|_| ServiceFailure::InputUnavailable)?;
        let file = root.join(format!(
            "staged.{}",
            output
                .extension()
                .and_then(|s| s.to_str())
                .ok_or(ServiceFailure::UnsupportedFormat)?
        ));
        let mut stage = Self {
            root,
            file,
            guard: None,
            cleaned: false,
        };
        let directory = fs::OpenOptions::new()
            .read(true)
            .share_mode(3)
            .custom_flags(0x02200000)
            .open(&stage.root)
            .map_err(|_| ServiceFailure::InputUnavailable)?;
        let metadata = directory
            .metadata()
            .map_err(|_| ServiceFailure::InputUnavailable)?;
        if !metadata.is_dir() || metadata.file_attributes() & 0x400 != 0 {
            return Err(ServiceFailure::InputUnavailable);
        }
        stage.guard = Some(directory);
        Ok(stage)
    }
    fn cleanup(&mut self) -> Result<(), ServiceFailure> {
        if self.cleaned {
            return Ok(());
        }
        let entries = fs::read_dir(&self.root).map_err(|_| ServiceFailure::CleanupFailed)?;
        for entry in entries {
            let entry = entry.map_err(|_| ServiceFailure::CleanupFailed)?;
            if entry.path() != self.file
                || !regular(
                    &fs::symlink_metadata(entry.path())
                        .map_err(|_| ServiceFailure::CleanupFailed)?,
                )
            {
                return Err(ServiceFailure::CleanupFailed);
            }
            fs::remove_file(entry.path()).map_err(|_| ServiceFailure::CleanupFailed)?;
        }
        self.guard.take();
        let metadata =
            fs::symlink_metadata(&self.root).map_err(|_| ServiceFailure::CleanupFailed)?;
        if !metadata.is_dir() || metadata.file_attributes() & 0x400 != 0 {
            return Err(ServiceFailure::CleanupFailed);
        }
        fs::remove_dir(&self.root).map_err(|_| ServiceFailure::CleanupFailed)?;
        self.cleaned = true;
        Ok(())
    }
}
impl Drop for Stage {
    fn drop(&mut self) {
        let _ = self.cleanup();
    }
}

pub(crate) fn run(
    input: &Path,
    id: RequestIdentity,
    control: &Control,
    mark: Option<&Value>,
    expected: &crate::ExpectedSource,
) -> Result<Value, ServiceFailure> {
    let executable = std::env::current_exe().map_err(|_| ServiceFailure::ServiceUnavailable)?;
    run_from_executable(&executable, input, id, control, mark, expected)
}

// Private implementation shared with an explicitly opted-in native test only.
// Normal application callers cannot supply a package root or replace the
// independently compiled digest: run() always uses the OS current executable.
fn run_from_executable(
    executable: &Path,
    input: &Path,
    id: RequestIdentity,
    control: &Control,
    mark: Option<&Value>,
    expected: &crate::ExpectedSource,
) -> Result<Value, ServiceFailure> {
    let _input = pin_input(input)?;
    // Add inputs stay bounded at32MiB; generated signed images may grow to
    //64MiB and must remain inspectable through the same operation path.
    let source_bound = if mark.is_some() { 32 } else { 64 } * 1024 * 1024;
    let source = bytes(input, source_bound)?;
    let source_sha = hash(&source);
    // Selected-preview identity is checked before even creating a stage or
    // launching the helper, not merely after output publication.
    if !expected.valid() || expected.sha256 != source_sha || expected.size != source.len() as u64 {
        return Err(ServiceFailure::SourceChanged);
    }
    let output = output_for(input)?;
    let mut stage = if mark.is_some() {
        if fs::symlink_metadata(&output).is_ok() {
            return Err(ServiceFailure::InputUnavailable);
        }
        Some(Stage::new(&output, id)?)
    } else {
        None
    };
    let result = (|| {
        let raw = inspection_protocol::encode_product_request(
            id,
            input,
            match (mark, stage.as_ref()) {
                (Some(mark), Some(stage)) => Some((&output, stage.file.as_path(), mark)),
                _ => None,
            },
        )?;
        let package = resolve_compiled_package(executable)?;
        let report = crate::inspection_supervisor::invoke(
            &package.configuration(),
            &raw,
            id,
            control,
            Duration::from_secs(30),
            Duration::from_secs(5),
        );
        #[cfg(all(test, debug_assertions, shirushi_dev_limited_inspection_canary))]
        println!(
            "PRODUCT_NATIVE_CONTROLLER_CLEANUP: reaped={} jobActiveProcesses={:?} jobTotalProcesses={:?} exitCode={:?}",
            report.reaped, report.job_active_processes, report.job_total_processes, report.exit_code
        );
        if !report.reaped || report.job_active_processes != Some(0) {
            return Err(ServiceFailure::CleanupFailed);
        }
        if package.process_identity_failure().is_some() || !package.process_identity_verified() {
            return Err(ServiceFailure::ServiceUnavailable);
        }
        let value = match report.outcome {
            HelperOutcome::Success(v) => v,
            HelperOutcome::Failure(e) => return Err(e),
        };
        let fingerprint = if mark.is_some() {
            &value["source"]
        } else {
            &value["inspection"]["source"]
        };
        if fingerprint["size"].as_u64() != Some(source.len() as u64) {
            return Err(ServiceFailure::SourceChanged);
        }
        if value["source"]["sha256"]
            .as_str()
            .or_else(|| value["inspection"]["source"]["sha256"].as_str())
            != Some(source_sha.as_str())
            || hash(&bytes(input, source_bound)?) != source_sha
        {
            return Err(ServiceFailure::SourceChanged);
        }
        if let Some(stage) = stage.as_ref() {
            if value["output"]["path"].as_str() != stage.file.to_str()
                || value["output"]["finalPath"].as_str() != output.to_str()
                || value["personalMark"] != *mark.ok_or(ServiceFailure::ResultInvalid)?
            {
                return Err(ServiceFailure::ResultInvalid);
            }
            let _staged_guard = pin_input(&stage.file)?;
            let staged = bytes(&stage.file, 64 * 1024 * 1024)?;
            if value["output"]["size"].as_u64() != Some(staged.len() as u64)
                || value["output"]["sha256"].as_str() != Some(hash(&staged).as_str())
            {
                return Err(ServiceFailure::ResultInvalid);
            }
            control.publish_if_current(id, || {
                fs::hard_link(&stage.file, &output).map_err(|_|ServiceFailure::InputUnavailable)?;
                Ok(json!({"operation":"add","result":"ADD_SUCCESS","developmentSigning":true,
                    "source":value["source"],"output":{"path":output,"size":staged.len(),"sha256":hash(&staged)},
                    "personalMark":value["personalMark"]}))
            })
        } else {
            Ok(value)
        }
    })();
    if let Some(stage) = &mut stage {
        stage.cleanup()?;
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    fn isolated_root() -> PathBuf {
        use std::sync::atomic::{AtomicU64, Ordering};
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let root = std::env::temp_dir().join(format!(
            "shirushi-product-controller-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        root
    }

    #[test]
    fn changed_selected_source_stops_before_helper_or_stage_creation() {
        let root = isolated_root();
        let source = root.join("art.png");
        let selected = b"selected bytes";
        fs::write(&source, b"changed bytes").unwrap();
        let id = RequestIdentity {
            request: 1,
            generation: 1,
        };
        let control = Control::new(id);
        let expected = crate::ExpectedSource {
            sha256: hash(selected),
            size: selected.len() as u64,
        };
        assert_eq!(
            run(&source, id, &control, Some(&json!({})), &expected),
            Err(ServiceFailure::SourceChanged)
        );
        assert!(!control.has_started());
        assert_eq!(fs::read_dir(&root).unwrap().count(), 1);
        assert!(!output_for(&source).unwrap().exists());
        fs::remove_file(&source).unwrap();
        fs::remove_dir(&root).unwrap();
    }

    #[test]
    fn publication_collision_preserves_existing_output() {
        let root = isolated_root();
        let staged = root.join("staged.png");
        let output = root.join("art_rights.png");
        fs::write(&staged, b"new staged bytes").unwrap();
        fs::write(&output, b"existing output").unwrap();
        assert!(fs::hard_link(&staged, &output).is_err());
        assert_eq!(fs::read(&output).unwrap(), b"existing output");
        assert_eq!(fs::read(&staged).unwrap(), b"new staged bytes");
        fs::remove_file(&staged).unwrap();
        fs::remove_file(&output).unwrap();
        fs::remove_dir(&root).unwrap();
    }

    #[test]
    fn output_convention_is_separate_and_preserves_extension() {
        for source in ["C:/work/art.png", "C:/work/art.JPEG"] {
            let source = Path::new(source);
            let output = output_for(source).unwrap();
            assert_ne!(source, output);
            assert_eq!(source.parent(), output.parent());
            assert!(output
                .file_stem()
                .unwrap()
                .to_str()
                .unwrap()
                .ends_with("_rights"));
            assert_eq!(source.extension(), output.extension());
        }
    }

    // This test fixture surface is absent unless CI explicitly opts in when
    // compiling lib tests. No runtime environment lookup or normal-build
    // executable-root override is introduced by the shared implementation.
    #[cfg(all(debug_assertions, shirushi_dev_limited_inspection_canary))]
    #[test]
    fn native_controller_png_jpeg_add_inspect_roundtrip_and_fail_closed_publication() {
        use std::io::Write;
        const TRUSTED_MANIFEST: &[u8] = include_bytes!(env!("SHIRUSHI_CANARY_TRUSTED_MANIFEST"));
        const BUILT_HELPER: &str = env!("SHIRUSHI_CANARY_BUILT_HELPER");
        const HELPER_SHA: &str = env!("SHIRUSHI_CANARY_HELPER_SHA256");
        const DIGEST: &str = env!("SHIRUSHI_DEV_INSPECTION_MANIFEST_SHA256");
        const PNG: &[u8] = include_bytes!("../../tests/fixtures/inspection/plain_no_shirushi.png");
        const JPEG: &[u8] = include_bytes!("../../tests/fixtures/inspection/plain_no_shirushi.jpg");

        // Frozen executable evidence is not an image input. Do not call the
        // product image pin/format guard when reading this test-only helper.
        fn read_frozen_helper(path: &Path) -> Vec<u8> {
            const CAP: u64 = 268435456;
            assert!(path.is_absolute());
            for ancestor in path.ancestors() {
                let metadata = fs::symlink_metadata(ancestor).unwrap();
                assert!(!metadata.file_type().is_symlink());
                assert_eq!(metadata.file_attributes() & 0x400, 0);
            }
            let mut file = fs::OpenOptions::new()
                .read(true)
                .share_mode(1)
                .custom_flags(0x00200000)
                .open(path)
                .unwrap();
            let metadata = file.metadata().unwrap();
            assert!(regular(&metadata) && metadata.len() <= CAP);
            let mut raw = Vec::new();
            (&mut file).take(CAP + 1).read_to_end(&mut raw).unwrap();
            assert_eq!(raw.len() as u64, metadata.len());
            assert!(raw.len() as u64 <= CAP);
            raw
        }

        struct OwnedTree {
            root: PathBuf,
            files: Vec<PathBuf>,
        }
        impl OwnedTree {
            fn create(&mut self, name: &str, raw: &[u8]) -> PathBuf {
                let path = self.root.join(name);
                fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&path)
                    .unwrap()
                    .write_all(raw)
                    .unwrap();
                self.files.push(path.clone());
                path
            }
        }
        impl Drop for OwnedTree {
            fn drop(&mut self) {
                // Only known files in this exclusively created private test
                // tree are removed. Unexpected leftover stage dirs fail this
                // bounded cleanup rather than being recursively erased.
                for path in self.files.iter().rev() {
                    fs::remove_file(path).expect("owned native test file cleanup failed");
                }
                fs::remove_dir(&self.root).expect("owned native test tree/stage cleanup failed");
            }
        }

        assert_eq!(hash(TRUSTED_MANIFEST), DIGEST);
        let helper = read_frozen_helper(Path::new(BUILT_HELPER));
        assert_eq!(hash(&helper), HELPER_SHA);
        let manifest: Value = serde_json::from_slice(TRUSTED_MANIFEST).unwrap();
        assert_eq!(manifest["relativePath"], "shirushi-inspection-helper.exe");
        assert_eq!(manifest["sha256"], HELPER_SHA);
        assert_eq!(manifest["size"].as_u64(), Some(helper.len() as u64));
        let mut tree = OwnedTree {
            root: isolated_root(),
            files: Vec::new(),
        };
        let helper_copy = tree.create("shirushi-inspection-helper.exe", &helper);
        tree.create("inspection-helper.manifest.json", TRUSTED_MANIFEST);
        // This regular file is an explicit test-only root locator, not a GUI
        // executable and never launched. The frozen real helper is launched.
        let executable = tree.create("shirushi-desktop.exe", b"explicit test-only root locator");
        let mark = json!({"version":1,"mode":"typed","typed":"Native 作者",
            "handwritten":{"coordinateSpace":{"width":480,"height":220},"strokes":[]}});

        for (index, (extension, raw)) in [("png", PNG), ("jpg", JPEG)].into_iter().enumerate() {
            let input = tree.create(&format!("native source {index}.{extension}"), raw);
            let expected = crate::ExpectedSource {
                sha256: hash(raw),
                size: raw.len() as u64,
            };
            let id = RequestIdentity {
                request: 100 + index as u64 * 10,
                generation: 1,
            };
            let unmarked_id = RequestIdentity {
                request: id.request + 4,
                generation: 1,
            };
            let unmarked_control = Control::new(unmarked_id);
            let unmarked = run_from_executable(
                &executable,
                &input,
                unmarked_id,
                &unmarked_control,
                None,
                &expected,
            )
            .expect("native controller unmarked inspection failed");
            assert!(unmarked_control.has_started());
            assert_eq!(unmarked["result"], "LIMITED_INSPECTION");
            assert_eq!(unmarked["completeness"], "INCOMPLETE");
            assert_eq!(unmarked["inspection"]["c2pa"]["presence"], "ABSENT");
            assert_eq!(unmarked["inspection"]["c2pa"]["signature"], "ABSENT");
            assert_eq!(
                unmarked["inspection"]["cawg"],
                json!({"state":"INSPECTED","presence":"ABSENT","aiTrainingUse":"UNKNOWN","aiInferenceUse":"UNKNOWN"})
            );
            assert!(unmarked["inspection"]["personalMark"].is_null());
            assert_eq!(unmarked["inspection"]["fullVerificationPerformed"], false);
            assert_eq!(unmarked["inspection"]["successMotionEligible"], false);
            assert_eq!(fs::read(&input).unwrap(), raw);
            let add_control = Control::new(id);
            let added = run_from_executable(
                &executable,
                &input,
                id,
                &add_control,
                Some(&mark),
                &expected,
            )
            .expect("native controller Add failed");
            assert!(add_control.has_started());
            assert_eq!(added["result"], "ADD_SUCCESS");
            assert_eq!(added["operation"], "add");
            assert_eq!(added["developmentSigning"], true);
            assert_eq!(added["personalMark"], mark);
            assert_eq!(added["source"]["sha256"], expected.sha256);
            let output = output_for(&input).unwrap();
            assert_ne!(input, output);
            assert_eq!(added["output"]["path"].as_str(), output.to_str());
            assert!(regular(&fs::symlink_metadata(&output).unwrap()));
            tree.files.push(output.clone());
            let signed = bytes(&output, 64 * 1024 * 1024).unwrap();
            let output_hash = hash(&signed);
            assert_eq!(added["output"]["sha256"], output_hash);
            assert_eq!(added["output"]["size"].as_u64(), Some(signed.len() as u64));
            assert_eq!(fs::read(&input).unwrap(), raw);
            assert_eq!(hash(&fs::read(&input).unwrap()), expected.sha256);
            assert!(!fs::read_dir(&tree.root).unwrap().any(|e| {
                e.unwrap()
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".shirushi-add-")
            }));

            let inspect_id = RequestIdentity {
                request: id.request + 1,
                generation: 1,
            };
            let inspect_control = Control::new(inspect_id);
            let inspected = run_from_executable(
                &executable,
                &output,
                inspect_id,
                &inspect_control,
                None,
                &crate::ExpectedSource {
                    sha256: output_hash.clone(),
                    size: signed.len() as u64,
                },
            )
            .expect("native controller limited inspection failed");
            assert!(inspect_control.has_started());
            assert_eq!(inspected["result"], "LIMITED_INSPECTION");
            assert_eq!(inspected["completeness"], "INCOMPLETE");
            assert_eq!(
                inspected["checks"],
                json!({"c2pa":"INSPECTED","cawg":"INSPECTED","trustmark":"NOT_CHECKED"})
            );
            assert_eq!(inspected["inspection"]["personalMark"], mark);
            assert_eq!(
                inspected["inspection"]["cawg"],
                json!({"state":"INSPECTED","presence":"PRESENT","aiTrainingUse":"NOT_WANTED","aiInferenceUse":"NOT_WANTED"})
            );
            assert_eq!(inspected["inspection"]["c2pa"]["signature"], "PREVIEW");
            assert_eq!(inspected["inspection"]["c2pa"]["trustValidated"], false);
            assert_eq!(inspected["inspection"]["fullVerificationPerformed"], false);
            assert_eq!(inspected["inspection"]["successMotionEligible"], false);

            let collision_id = RequestIdentity {
                request: id.request + 2,
                generation: 1,
            };
            let collision_control = Control::new(collision_id);
            assert_eq!(
                run_from_executable(
                    &executable,
                    &input,
                    collision_id,
                    &collision_control,
                    Some(&mark),
                    &expected,
                ),
                Err(ServiceFailure::InputUnavailable)
            );
            assert!(!collision_control.has_started());
            assert_eq!(fs::read(&output).unwrap(), signed);
            let changed_id = RequestIdentity {
                request: id.request + 3,
                generation: 1,
            };
            let changed_control = Control::new(changed_id);
            assert_eq!(
                run_from_executable(
                    &executable,
                    &input,
                    changed_id,
                    &changed_control,
                    Some(&mark),
                    &crate::ExpectedSource {
                        sha256: hash(b"changed selected SHA"),
                        size: raw.len() as u64
                    },
                ),
                Err(ServiceFailure::SourceChanged)
            );
            assert!(!changed_control.has_started());
            assert_eq!(fs::read(&input).unwrap(), raw);
            assert_eq!(fs::read(&output).unwrap(), signed);
            println!("PRODUCT_NATIVE_CONTROLLER_ROUNDTRIP: format={extension} Add=ADD_SUCCESS inspection=LIMITED_INSPECTION completeness=INCOMPLETE collision=REJECTED selectedSHA=REJECTED sourcePreserved=true stageRemoved=true");
        }
        assert_eq!(hash(&read_frozen_helper(&helper_copy)), HELPER_SHA);
        assert_eq!(
            hash(&read_frozen_helper(Path::new(BUILT_HELPER))),
            HELPER_SHA
        );
    }
}
