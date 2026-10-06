//! Single-image Explorer startup handoff. No registration or helper execution.
use crate::protocol::BridgeError;
use serde_json::{json, Value};
use std::{
    ffi::OsString,
    sync::{Arc, Mutex},
};

#[derive(Clone, Debug, PartialEq, Eq)]
struct Request {
    operation: &'static str,
    path: String,
}

fn error(code: &'static str) -> BridgeError {
    BridgeError::new(code, "Explorer image request unavailable")
}

fn parse(args: impl IntoIterator<Item = OsString>) -> Result<Option<Request>, BridgeError> {
    // Stop at the first extra argument rather than accepting multi-selection.
    let args: Vec<_> = args.into_iter().take(5).collect();
    if args.is_empty() {
        return Ok(None);
    }
    if args.len() != 4 || args[0] != "--shirushi-explorer" || args[2] != "--" {
        return Err(error("EXPLORER_ARGUMENTS_INVALID"));
    }
    let operation = match args[1].to_str() {
        Some("add") => "add",
        Some("limited_inspect") => "limited_inspect",
        _ => return Err(error("EXPLORER_ARGUMENTS_INVALID")),
    };
    let path = args[3]
        .to_str()
        .filter(|path| valid_path(path))
        .ok_or_else(|| error("EXPLORER_PATH_INVALID"))?;
    Ok(Some(Request {
        operation,
        path: path.to_owned(),
    }))
}

fn valid_path(path: &str) -> bool {
    let bytes = path.as_bytes();
    if bytes.len() < 7
        || bytes.len() > 4096
        || !bytes[0].is_ascii_alphabetic()
        || bytes[1] != b':'
        || !matches!(bytes[2], b'\\' | b'/')
        || path
            .chars()
            .any(|c| c.is_control() || matches!(c, '"' | '<' | '>' | '|' | '?' | '*'))
    {
        return false;
    }
    let parts: Vec<_> = path[3..].split(['\\', '/']).take(33).collect();
    if parts.len() > 30
        || parts.iter().any(|part| {
            part.is_empty()
                || matches!(*part, "." | "..")
                || part.contains(':')
                || part.ends_with(['.', ' '])
                || reserved_name(part)
        })
    {
        return false;
    }
    parts.last().is_some_and(|leaf| {
        leaf.rsplit_once('.').is_some_and(|(_, extension)| {
            ["png", "jpg", "jpeg"]
                .iter()
                .any(|x| extension.eq_ignore_ascii_case(x))
        })
    })
}

fn reserved_name(part: &str) -> bool {
    let stem = part
        .split('.')
        .next()
        .unwrap_or("")
        .trim_end_matches(' ')
        .to_ascii_uppercase();
    matches!(
        stem.as_str(),
        "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$"
    ) || ["COM", "LPT"].iter().any(|prefix| {
        stem.strip_prefix(*prefix).is_some_and(|tail| {
            matches!(
                tail,
                "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³"
            )
        })
    })
}

struct Pending {
    request: Option<Result<Request, BridgeError>>,
    // Retain the Explorer-only duplicate claim for this process lifetime.
    _claim: Option<LaunchClaim>,
}

#[derive(Clone)]
pub(crate) struct ExplorerEntry(Arc<Mutex<Pending>>);

impl ExplorerEntry {
    pub(crate) fn from_args(args: impl IntoIterator<Item = OsString>) -> Self {
        let request = match parse(args) {
            Ok(request) => request.map(Ok),
            Err(error) => Some(Err(error)),
        };
        Self(Arc::new(Mutex::new(Pending {
            request,
            _claim: None,
        })))
    }

    pub(crate) fn take(&self) -> Result<Option<Value>, BridgeError> {
        self.take_with(resolve)
    }

    fn take_with(
        &self,
        resolve: impl FnOnce(Request) -> Result<(Value, Option<LaunchClaim>), BridgeError>,
    ) -> Result<Option<Value>, BridgeError> {
        // Consume before any image I/O; failure, cancellation of IPC, or a second
        // concurrent frontend call cannot replay this startup request.
        let request = self
            .0
            .lock()
            .map_err(|_| error("EXPLORER_STATE_UNAVAILABLE"))?
            .request
            .take();
        let Some(request) = request else {
            return Ok(None);
        };
        let request = request?;
        let operation = request.operation;
        let (image, claim) = resolve(request)?;
        self.0
            .lock()
            .map_err(|_| error("EXPLORER_STATE_UNAVAILABLE"))?
            ._claim = claim;
        Ok(Some(json!({"operation":operation,"image":image})))
    }
}

fn resolve(request: Request) -> Result<(Value, Option<LaunchClaim>), BridgeError> {
    #[cfg(all(windows, debug_assertions))]
    {
        let path = std::path::Path::new(&request.path);
        // Existing local fixed-drive, ancestor and source reparse guards remain
        // held across canonicalization, duplicate claim, and preview readback.
        let _pin = crate::limited_inspection::pin_input(path)
            .map_err(|_| error("EXPLORER_IMAGE_UNAVAILABLE"))?;
        let canonical = path
            .canonicalize()
            .map_err(|_| error("EXPLORER_IMAGE_UNAVAILABLE"))?;
        let claim = LaunchClaim::acquire(request.operation, &canonical)?;
        let image = crate::product_image::read(&request.path)?;
        let png = path
            .extension()
            .and_then(|v| v.to_str())
            .is_some_and(|v| v.eq_ignore_ascii_case("png"));
        let expected = if png {
            "data:image/png;base64,"
        } else {
            "data:image/jpeg;base64,"
        };
        if !image
            .get("url")
            .and_then(Value::as_str)
            .is_some_and(|url| url.starts_with(expected))
        {
            return Err(error("EXPLORER_IMAGE_FORMAT_INVALID"));
        }
        Ok((image, Some(claim)))
    }
    #[cfg(not(all(windows, debug_assertions)))]
    {
        let _ = request;
        Err(error("DEV_CANARY_ONLY"))
    }
}

#[cfg(windows)]
struct LaunchClaim(usize);
#[cfg(not(windows))]
struct LaunchClaim;

#[cfg(windows)]
impl Drop for LaunchClaim {
    fn drop(&mut self) {
        unsafe { windows_sys::Win32::Foundation::CloseHandle(self.0 as _) };
    }
}

#[cfg(windows)]
impl LaunchClaim {
    fn acquire(operation: &str, canonical: &std::path::Path) -> Result<Self, BridgeError> {
        use sha2::{Digest, Sha256};
        use windows_sys::Win32::{
            Foundation::{CloseHandle, GetLastError, ERROR_ALREADY_EXISTS},
            Security::{
                GetLengthSid, GetTokenInformation, IsValidSid, TokenUser, TOKEN_QUERY, TOKEN_USER,
            },
            System::Threading::{CreateMutexW, GetCurrentProcess, OpenProcessToken},
        };
        let mut token = std::ptr::null_mut();
        if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0 {
            return Err(error("EXPLORER_DUPLICATE_GUARD_UNAVAILABLE"));
        }
        // Use aligned storage, bounded to token-user metadata only. Do not read
        // environment usernames or publish the SID/private image path.
        let mut storage = [0usize; 512];
        let mut length = 0;
        let ok = unsafe {
            GetTokenInformation(
                token,
                TokenUser,
                storage.as_mut_ptr().cast(),
                std::mem::size_of_val(&storage) as u32,
                &mut length,
            )
        };
        unsafe { CloseHandle(token) };
        if ok == 0
            || length as usize > std::mem::size_of_val(&storage)
            || (length as usize) < std::mem::size_of::<TOKEN_USER>()
        {
            return Err(error("EXPLORER_DUPLICATE_GUARD_UNAVAILABLE"));
        }
        let sid = unsafe { (&*storage.as_ptr().cast::<TOKEN_USER>()).User.Sid };
        let start = storage.as_ptr() as usize;
        let end = start + length as usize;
        let address = sid as usize;
        if address < start || address.checked_add(8).is_none_or(|n| n > end) {
            return Err(error("EXPLORER_DUPLICATE_GUARD_UNAVAILABLE"));
        }
        // SID header's sub-authority count bounds IsValidSid/GetLengthSid reads.
        let sid_length = 8 + 4 * unsafe { *sid.cast::<u8>().add(1) } as usize;
        if address.checked_add(sid_length).is_none_or(|n| n > end)
            || unsafe { IsValidSid(sid) } == 0
            || unsafe { GetLengthSid(sid) } as usize != sid_length
        {
            return Err(error("EXPLORER_DUPLICATE_GUARD_UNAVAILABLE"));
        }
        let mut digest = Sha256::new();
        digest.update(unsafe { std::slice::from_raw_parts(sid.cast::<u8>(), sid_length) });
        digest.update([0]);
        digest.update(operation.as_bytes());
        digest.update([0]);
        let canonical = canonical
            .to_str()
            .ok_or_else(|| error("EXPLORER_IMAGE_UNAVAILABLE"))?;
        digest.update(canonical.to_lowercase().as_bytes());
        let name = format!("Local\\Shirushi.Explorer.v1.{:x}", digest.finalize());
        let name: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
        // Existence, not mutex ownership, is the duplicate gate. The handle is
        // retained for the entire Explorer-launched process lifetime.
        let handle = unsafe { CreateMutexW(std::ptr::null(), 0, name.as_ptr()) };
        let last_error = unsafe { GetLastError() };
        if handle.is_null() {
            return Err(error("EXPLORER_DUPLICATE_GUARD_UNAVAILABLE"));
        }
        if last_error == ERROR_ALREADY_EXISTS {
            unsafe { CloseHandle(handle) };
            return Err(error("EXPLORER_DUPLICATE_REQUEST"));
        }
        Ok(Self(handle as usize))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn arguments(operation: &str, path: &str) -> Vec<OsString> {
        ["--shirushi-explorer", operation, "--", path]
            .map(OsString::from)
            .to_vec()
    }
    #[test]
    fn normal_startup_has_no_handoff() {
        assert_eq!(parse(Vec::new()).unwrap(), None);
        assert_eq!(
            ExplorerEntry::from_args(Vec::new())
                .take_with(|_| panic!("no request"))
                .unwrap(),
            None
        );
    }
    #[test]
    fn both_operations_support_png_and_jpeg_special_character_paths() {
        for operation in ["add", "limited_inspect"] {
            for extension in ["png", "jpg", "JPEG"] {
                let path = format!("C:\\画像 & family's (1)\\手書き.{extension}");
                let request = parse(arguments(operation, &path)).unwrap().unwrap();
                assert_eq!(request.operation, operation);
                assert_eq!(request.path, path);
            }
        }
    }
    #[test]
    fn malformed_and_extra_arguments_are_rejected() {
        for values in [
            vec!["x"],
            vec!["--shirushi-explorer", "verify", "--", "C:\\x.png"],
            vec!["--shirushi-explorer", "add", "C:\\x.png"],
            vec!["--shirushi-explorer", "add", "--", "C:\\x.png", "C:\\y.png"],
        ] {
            assert!(parse(values.into_iter().map(OsString::from)).is_err());
        }
    }
    #[test]
    fn unsafe_windows_path_forms_are_rejected() {
        for path in [
            "x.png",
            "C:x.png",
            "\\\\server\\share\\x.png",
            "\\\\?\\C:\\x.png",
            "\\\\.\\C:\\x.png",
            "C:\\x.png:stream",
            "C:\\..\\x.png",
            "C:\\.\\x.png",
            "C:\\\\x.png",
            "C:\\x.gif",
            "C:\\x.png ",
            "C:\\bad.\\x.png",
            "C:\\NUL.png",
            "C:\\COM¹\\x.jpg",
            "C:\\x\n.png",
            "C:\\x\0.png",
            "C:\\x\".png",
            "C:\\x?.png",
        ] {
            assert!(parse(arguments("add", path)).is_err(), "accepted {path:?}");
        }
    }
    #[test]
    fn one_shot_consumes_before_resolving_and_never_replays() {
        let state = ExplorerEntry::from_args(arguments("add", "C:\\x.png"));
        let value = state
            .take_with(|request| {
                assert_eq!(request.operation, "add");
                assert_eq!(state.take_with(|_| panic!("duplicate I/O")).unwrap(), None);
                Ok((json!({"reference":request.path}), None))
            })
            .unwrap()
            .unwrap();
        assert_eq!(value["operation"], "add");
        assert_eq!(state.take_with(|_| panic!("replay")).unwrap(), None);
    }
    #[test]
    fn malformed_and_read_failures_are_visible_once() {
        let invalid = ExplorerEntry::from_args([OsString::from("unknown")]);
        assert_eq!(
            invalid
                .take_with(|_| panic!("invalid read"))
                .unwrap_err()
                .code,
            "EXPLORER_ARGUMENTS_INVALID"
        );
        assert_eq!(invalid.take_with(|_| panic!("replay")).unwrap(), None);
        let state = ExplorerEntry::from_args(arguments("limited_inspect", "C:\\x.jpg"));
        assert_eq!(
            state
                .take_with(|_| Err(error("EXPLORER_IMAGE_UNAVAILABLE")))
                .unwrap_err()
                .code,
            "EXPLORER_IMAGE_UNAVAILABLE"
        );
        assert_eq!(state.take_with(|_| panic!("retry")).unwrap(), None);
    }
    #[test]
    fn concurrent_consumers_resolve_at_most_once() {
        use std::sync::{
            atomic::{AtomicUsize, Ordering},
            Barrier,
        };
        let state = ExplorerEntry::from_args(arguments("add", "C:\\x.png"));
        let start = Arc::new(Barrier::new(3));
        let calls = Arc::new(AtomicUsize::new(0));
        let workers: Vec<_> = (0..2)
            .map(|_| {
                let state = state.clone();
                let start = start.clone();
                let calls = calls.clone();
                std::thread::spawn(move || {
                    start.wait();
                    state
                        .take_with(|_| {
                            calls.fetch_add(1, Ordering::SeqCst);
                            Ok((json!({}), None))
                        })
                        .unwrap()
                        .is_some()
                })
            })
            .collect();
        start.wait();
        let delivered = workers
            .into_iter()
            .map(|w| usize::from(w.join().unwrap()))
            .sum::<usize>();
        assert_eq!(delivered, 1);
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }
    #[cfg(windows)]
    #[test]
    fn duplicate_claim_is_operation_specific_and_released_with_lifetime() {
        let path = std::path::PathBuf::from(format!(
            "C:\\shirushi-claim-test-{}.png",
            std::process::id()
        ));
        let first = LaunchClaim::acquire("add", &path).unwrap();
        assert!(
            matches!(LaunchClaim::acquire("add", &path), Err(e) if e.code == "EXPLORER_DUPLICATE_REQUEST")
        );
        let second = LaunchClaim::acquire("limited_inspect", &path).unwrap();
        drop(first);
        assert!(LaunchClaim::acquire("add", &path).is_ok());
        drop(second);
    }
    #[cfg(windows)]
    #[test]
    fn non_unicode_arguments_are_rejected() {
        use std::os::windows::ffi::OsStringExt;
        let mut args = arguments("add", "C:\\x.png");
        args[3] = OsString::from_wide(&[0xd800]);
        assert!(parse(args).is_err());
    }

    // These tests invoke the actual image handoff, not the injected resolver.
    // No Desktop, helper, shell, or other executable is launched.
    #[cfg(all(windows, debug_assertions))]
    mod native {
        use super::*;
        use base64::{engine::general_purpose::STANDARD, Engine};
        use sha2::{Digest, Sha256};
        use std::{
            fs,
            io::Write,
            os::windows::fs::MetadataExt,
            path::{Path, PathBuf},
            sync::atomic::{AtomicU64, Ordering},
            time::{SystemTime, UNIX_EPOCH},
        };

        const PNG: &[u8] = include_bytes!("../../tests/fixtures/inspection/plain_no_shirushi.png");
        const JPEG: &[u8] = include_bytes!("../../tests/fixtures/inspection/plain_no_shirushi.jpg");

        struct OwnedImages {
            root: PathBuf,
            files: Vec<PathBuf>,
        }

        impl OwnedImages {
            fn new() -> Self {
                static NEXT: AtomicU64 = AtomicU64::new(0);
                let nonce = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_nanos();
                let root = std::env::temp_dir().join(format!(
                    "shirushi-explorer-test-{}-{nonce}-{}",
                    std::process::id(),
                    NEXT.fetch_add(1, Ordering::SeqCst)
                ));
                // Exclusive ownership; never reuse or overwrite a test tree.
                fs::create_dir(&root).unwrap();
                Self {
                    root,
                    files: Vec::new(),
                }
            }

            fn create(&mut self, name: &str, raw: &[u8]) -> PathBuf {
                let path = self.root.join(name);
                assert_eq!(path.parent(), Some(self.root.as_path()));
                let mut file = fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&path)
                    .unwrap();
                self.files.push(path.clone());
                file.write_all(raw).unwrap();
                path
            }
        }

        impl Drop for OwnedImages {
            fn drop(&mut self) {
                // Remove only the exact files created above, never recurse or
                // follow a substituted link. Unexpected leftovers fail cleanup.
                for path in self.files.iter().rev() {
                    let metadata = fs::symlink_metadata(path).unwrap();
                    assert!(metadata.is_file());
                    assert_eq!(metadata.file_attributes() & 0x400, 0);
                    fs::remove_file(path).unwrap();
                }
                let metadata = fs::symlink_metadata(&self.root).unwrap();
                assert!(metadata.is_dir());
                assert_eq!(metadata.file_attributes() & 0x400, 0);
                fs::remove_dir(&self.root).unwrap();
            }
        }

        fn source_unchanged(path: &Path, original: &[u8]) {
            let actual = fs::read(path).unwrap();
            assert_eq!(actual, original);
            assert_eq!(Sha256::digest(&actual), Sha256::digest(original));
        }

        #[test]
        fn native_take_reads_png_jpeg_for_both_operations_once() {
            let mut images = OwnedImages::new();
            for (extension, original, mime) in
                [("png", PNG, "image/png"), ("jpg", JPEG, "image/jpeg")]
            {
                let path = images.create(
                    &format!("日本語 image & family's (1).{extension}"),
                    original,
                );
                let path_string = path.to_str().unwrap();
                for operation in ["add", "limited_inspect"] {
                    let entry = ExplorerEntry::from_args(arguments(operation, path_string));
                    let result = entry.take().unwrap().unwrap();
                    assert_eq!(result.as_object().unwrap().len(), 2);
                    assert_eq!(result["operation"], operation);
                    let image = &result["image"];
                    assert_eq!(image["reference"], path_string);
                    assert_eq!(image["name"], path.file_name().unwrap().to_str().unwrap());
                    assert_eq!(image["local"], true);
                    assert_eq!(image["size"].as_u64(), Some(original.len() as u64));
                    assert_eq!(image["sha256"], format!("{:x}", Sha256::digest(original)));
                    let prefix = format!("data:{mime};base64,");
                    let encoded = image["url"]
                        .as_str()
                        .unwrap()
                        .strip_prefix(prefix.as_str())
                        .unwrap();
                    assert_eq!(STANDARD.decode(encoded).unwrap(), original);
                    assert_eq!(entry.take().unwrap(), None);
                    source_unchanged(&path, original);
                }
            }
        }

        #[test]
        fn native_independent_entries_reject_concurrent_duplicate() {
            use std::sync::Barrier;
            let mut images = OwnedImages::new();
            let path = images.create("重複 image.png", PNG);
            let path_string = path.to_str().unwrap();
            for operation in ["add", "limited_inspect"] {
                let start = Arc::new(Barrier::new(3));
                let workers: Vec<_> = (0..2)
                    .map(|_| {
                        // Independent state models separate Explorer-launched
                        // Desktop instances; only the OS named claim is shared.
                        let entry = ExplorerEntry::from_args(arguments(operation, path_string));
                        let start = start.clone();
                        std::thread::spawn(move || {
                            start.wait();
                            let result = entry.take();
                            // Retain the winning instance through both join results.
                            (entry, result)
                        })
                    })
                    .collect();
                start.wait();
                let outcomes: Vec<_> = workers.into_iter().map(|w| w.join().unwrap()).collect();
                assert_eq!(
                    outcomes
                        .iter()
                        .filter(|(_, r)| matches!(r, Ok(Some(_))))
                        .count(),
                    1
                );
                assert_eq!(
                    outcomes
                        .iter()
                        .filter(
                            |(_, r)| matches!(r, Err(e) if e.code == "EXPLORER_DUPLICATE_REQUEST")
                        )
                        .count(),
                    1
                );
                for (entry, _) in &outcomes {
                    assert_eq!(entry.take().unwrap(), None);
                }
                source_unchanged(&path, PNG);
            }
        }

        #[test]
        fn native_take_rejects_malformed_unsupported_and_mismatched_images() {
            let mut images = OwnedImages::new();
            let malformed = images.create("壊れた image.png", b"not a PNG or JPEG");
            let unsupported = images.create("未対応 image.gif", PNG);
            let mismatched = images.create("種類不一致 image.jpg", PNG);
            for operation in ["add", "limited_inspect"] {
                for (path, expected) in [
                    (&malformed, "UNSUPPORTED_FORMAT"),
                    (&unsupported, "EXPLORER_PATH_INVALID"),
                    (&mismatched, "EXPLORER_IMAGE_FORMAT_INVALID"),
                ] {
                    let entry =
                        ExplorerEntry::from_args(arguments(operation, path.to_str().unwrap()));
                    assert_eq!(entry.take().unwrap_err().code, expected);
                    assert_eq!(entry.take().unwrap(), None);
                }
            }
            source_unchanged(&malformed, b"not a PNG or JPEG");
            source_unchanged(&unsupported, PNG);
            source_unchanged(&mismatched, PNG);
        }
    }
}
