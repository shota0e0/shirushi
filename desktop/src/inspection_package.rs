//! DEVELOPMENT CANARY ONLY: trusted Rust test injection, not Production discovery.
//! Exact-package integrity is not publisher authenticity or kernel image-section proof.
use crate::inspection_supervisor::FixedExecutable;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{
    ffi::{c_void, OsString},
    fs::{File, OpenOptions},
    io::Read,
    mem::size_of,
    os::windows::{
        ffi::OsStringExt,
        fs::{MetadataExt, OpenOptionsExt},
        io::AsRawHandle,
    },
    path::{Component, Path, PathBuf, Prefix},
    process::Child,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};

pub const HELPER_BASENAME: &str = "shirushi-inspection-helper.exe";
pub const MANIFEST_BASENAME: &str = "inspection-helper.manifest.json";
pub const MAX_MANIFEST_BYTES: u64 = 4096;
const MAX_HELPER_BYTES: u64 = 256 * 1024 * 1024;
const REPARSE: u32 = 0x400;

/// Host-local category. Never part of helper protocol v1, inner1 or outer2.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PackageFailure {
    PackageIntegrityFailure,
    ProcessIdentityUnverified,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Manifest {
    schema_version: u64,
    relative_path: String,
    size: u64,
    sha256: String,
    protocol_version: u64,
}
fn canonical_hash(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub fn parse_manifest(raw: &[u8]) -> Result<Manifest, PackageFailure> {
    let bad = PackageFailure::PackageIntegrityFailure;
    if raw.is_empty() || raw.len() as u64 > MAX_MANIFEST_BYTES {
        return Err(bad);
    }
    // Struct deserialization rejects duplicate declared keys as well as unknown keys;
    // from_slice additionally rejects trailing JSON/data. No normalization for hashing.
    let m: Manifest = serde_json::from_slice(raw).map_err(|_| bad)?;
    if m.schema_version != 1
        || m.protocol_version != 1
        || m.relative_path != HELPER_BASENAME
        || m.size == 0
        || m.size > MAX_HELPER_BYTES
        || !canonical_hash(&m.sha256)
    {
        return Err(bad);
    }
    Ok(m)
}

#[repr(C)]
#[derive(Default, PartialEq, Eq)]
struct FileIdentity {
    volume: u64,
    id: [u8; 16],
}
#[link(name = "kernel32")]
extern "system" {
    fn GetFileInformationByHandleEx(
        h: *mut c_void,
        class: i32,
        data: *mut c_void,
        size: u32,
    ) -> i32;
    fn GetHandleInformation(h: *mut c_void, flags: *mut u32) -> i32;
    fn QueryFullProcessImageNameW(
        h: *mut c_void,
        flags: u32,
        name: *mut u16,
        size: *mut u32,
    ) -> i32;
    fn GetDriveTypeW(root: *const u16) -> u32;
}
fn identity(file: &File) -> Result<FileIdentity, PackageFailure> {
    let mut id = FileIdentity::default();
    // SAFETY: FILE_ID_INFO (class18) is volume u64 followed by FILE_ID_128;
    // buffer and handle remain live for this synchronous call.
    if unsafe {
        GetFileInformationByHandleEx(
            file.as_raw_handle(),
            18,
            &mut id as *mut _ as *mut c_void,
            size_of::<FileIdentity>() as u32,
        )
    } == 0
    {
        return Err(PackageFailure::ProcessIdentityUnverified);
    }
    Ok(id)
}
fn noninheritable(file: &File) -> bool {
    let mut flags = 0;
    unsafe { GetHandleInformation(file.as_raw_handle(), &mut flags) != 0 && flags & 1 == 0 }
}
fn regular(file: &File) -> bool {
    file.metadata()
        .map(|m| m.is_file() && m.file_attributes() & REPARSE == 0)
        .unwrap_or(false)
}
fn open_file(path: &Path) -> Result<File, PackageFailure> {
    let bad = PackageFailure::PackageIntegrityFailure;
    // OPEN_REPARSE_POINT: inspect the leaf itself, never follow an untrusted leaf link.
    // SHARE_READ only: verified files cannot ordinarily be written/deleted/renamed.
    let f = OpenOptions::new()
        .read(true)
        .share_mode(1)
        .custom_flags(0x00200000)
        .open(path)
        .map_err(|_| bad)?;
    if !regular(&f) || !noninheritable(&f) {
        return Err(bad);
    }
    Ok(f)
}
fn pin_root(root: &Path) -> Result<Vec<File>, PackageFailure> {
    let bad = PackageFailure::PackageIntegrityFailure;
    if !root.is_absolute()
        || root.components().count() > 32
        || !matches!(root.components().next(), Some(Component::Prefix(p)) if matches!(p.kind(), Prefix::Disk(_)))
    {
        return Err(bad);
    }
    use std::os::windows::ffi::OsStrExt;
    let mut drive = PathBuf::new();
    drive.push(root.components().next().ok_or(bad)?.as_os_str());
    drive.push("\\");
    let terminated: Vec<u16> = drive.as_os_str().encode_wide().chain(Some(0)).collect();
    // Canary is confined to a local fixed drive, not a mapped network/UNC device.
    if unsafe { GetDriveTypeW(terminated.as_ptr()) } != 3 {
        return Err(bad);
    }
    let mut path = PathBuf::new();
    let mut pinned = Vec::new();
    for part in root.components() {
        match part {
            Component::Prefix(_) => {
                path.push(part.as_os_str());
                continue;
            }
            Component::RootDir => path.push(part.as_os_str()),
            Component::Normal(name) => {
                if name.encode_wide().any(|c| c == b':' as u16 || c == 0) {
                    return Err(bad);
                }
                path.push(name);
            }
            _ => return Err(bad),
        }
        // Directory handles deny deletion/rename, not normal child-file creation.
        // Pin ancestors before opening descendants; no ACL changes or namespace claims.
        let f = OpenOptions::new()
            .read(true)
            .share_mode(3)
            .custom_flags(0x02200000)
            .open(&path)
            .map_err(|_| bad)?;
        let m = f.metadata().map_err(|_| bad)?;
        if !m.is_dir() || m.file_attributes() & REPARSE != 0 || !noninheritable(&f) {
            return Err(bad);
        }
        pinned.push(f);
    }
    Ok(pinned)
}
fn hash_file(file: &File, size: u64) -> Result<String, PackageFailure> {
    let bad = PackageFailure::PackageIntegrityFailure;
    let mut reader = file;
    let mut hash = Sha256::new();
    let mut read = 0u64;
    let mut buf = [0u8; 8192];
    loop {
        let n = reader.read(&mut buf).map_err(|_| bad)?;
        if n == 0 {
            break;
        }
        read += n as u64;
        if read > size {
            return Err(bad);
        }
        hash.update(&buf[..n]);
    }
    if read != size {
        return Err(bad);
    }
    Ok(format!("{:x}", hash.finalize()))
}

pub(crate) struct VerifiedHelper {
    // All guards outlive spawn, pre-resume comparison, execution and cleanup.
    _root_guards: Vec<File>,
    _manifest_guard: File,
    helper: File,
    path: PathBuf,
    id: FileIdentity,
    checked: AtomicBool,
    failed: AtomicBool,
}
impl VerifiedHelper {
    pub(crate) fn begin(&self) {
        self.checked.store(false, Ordering::SeqCst);
        self.failed.store(false, Ordering::SeqCst);
    }
    pub(crate) fn verify_child(&self, child: &Child) -> bool {
        let check = || -> Result<(), PackageFailure> {
            let mut name = vec![0u16; 32768];
            let mut len = name.len() as u32;
            // Owned suspended child only. No PID lookup or arbitrary process observation.
            if unsafe {
                QueryFullProcessImageNameW(child.as_raw_handle(), 0, name.as_mut_ptr(), &mut len)
            } == 0
                || len == 0
                || len as usize >= name.len()
            {
                return Err(PackageFailure::ProcessIdentityUnverified);
            }
            let path = PathBuf::from(OsString::from_wide(&name[..len as usize]));
            if !path.is_absolute() {
                return Err(PackageFailure::ProcessIdentityUnverified);
            }
            let image = open_file(&path)?;
            if identity(&image)? != self.id || identity(&self.helper)? != self.id {
                return Err(PackageFailure::ProcessIdentityUnverified);
            }
            Ok(())
        };
        let ok = check().is_ok();
        self.checked.store(ok, Ordering::SeqCst);
        self.failed.store(!ok, Ordering::SeqCst);
        ok
    }
}

/// No release-build constructor, env override, frontend path, or discovery.
/// The expected digest is a trusted test/build input OUTSIDE this package tree.
pub struct CanaryPackage(Arc<VerifiedHelper>);
impl std::fmt::Debug for CanaryPackage {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("CanaryPackage(REDACTED)")
    }
}
impl CanaryPackage {
    pub fn preflight(root: &Path, expected_manifest_sha256: &str) -> Result<Self, PackageFailure> {
        let bad = PackageFailure::PackageIntegrityFailure;
        if !canonical_hash(expected_manifest_sha256) {
            return Err(bad);
        }
        let guards = pin_root(root)?;
        let manifest = open_file(&root.join(MANIFEST_BASENAME))?;
        let len = manifest.metadata().map_err(|_| bad)?.len();
        if len == 0 || len > MAX_MANIFEST_BYTES {
            return Err(bad);
        }
        let mut raw = Vec::new();
        (&manifest)
            .take(MAX_MANIFEST_BYTES + 1)
            .read_to_end(&mut raw)
            .map_err(|_| bad)?;
        if format!("{:x}", Sha256::digest(&raw)) != expected_manifest_sha256 {
            return Err(bad);
        }
        let m = parse_manifest(&raw)?;
        let path = root.join(HELPER_BASENAME);
        let helper = open_file(&path)?;
        if helper.metadata().map_err(|_| bad)?.len() != m.size
            || hash_file(&helper, m.size)? != m.sha256
        {
            return Err(bad);
        }
        let id = identity(&helper).map_err(|_| bad)?;
        Ok(Self(Arc::new(VerifiedHelper {
            _root_guards: guards,
            _manifest_guard: manifest,
            helper,
            path,
            id,
            checked: AtomicBool::new(false),
            failed: AtomicBool::new(false),
        })))
    }
    pub fn configuration(&self) -> FixedExecutable {
        FixedExecutable::verified_canary(self.0.path.clone(), self.0.clone())
    }
    pub fn process_identity_verified(&self) -> bool {
        self.0.checked.load(Ordering::SeqCst)
    }
    pub fn process_identity_failure(&self) -> Option<PackageFailure> {
        self.0
            .failed
            .load(Ordering::SeqCst)
            .then_some(PackageFailure::ProcessIdentityUnverified)
    }
    /// Trusted Rust negative-test injection ONLY; deliberately mismatches two
    /// independently verified package files. Absent from all release builds.
    #[doc(hidden)]
    pub fn mismatched_identity_configuration_for_canary(&self, other: &Self) -> FixedExecutable {
        FixedExecutable::verified_canary(self.0.path.clone(), other.0.clone())
    }
}
