//! DEVELOPMENT CANARY ONLY: trusted Rust test injection, not Production discovery.
//! Exact-package integrity is not publisher authenticity or kernel image-section proof.
#![allow(unexpected_cfgs)] // Explicit CI cfg; no Cargo feature/dependency surface.
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
    #[cfg(shirushi_release_helper_native_inventory)]
    native_inventory: std::sync::Mutex<Option<Arc<native_inventory::Observer>>>,
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
        #[cfg(shirushi_release_helper_native_inventory)]
        if ok {
            let observation = self
                .native_inventory
                .lock()
                .unwrap_or_else(|e| e.into_inner());
            if let Some(observer) = observation.as_ref() {
                // Observation failure never changes the identity/supervisor outcome.
                observer.seed(child, self.path.clone());
            }
        }
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
            #[cfg(shirushi_release_helper_native_inventory)]
            native_inventory: std::sync::Mutex::new(None),
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
    /// Explicit CI-only evidence. The guard must be finished after inspect returns;
    /// dropping it also stops and joins the worker. No runtime environment opt-in.
    #[cfg(shirushi_release_helper_native_inventory)]
    #[doc(hidden)]
    pub fn observe_native_modules_for_canary(
        &self,
        control: crate::inspection_supervisor::Control,
    ) -> Result<native_inventory::NativeInventoryCanary, &'static str> {
        let mut slot = self
            .0
            .native_inventory
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if slot.is_some() || control.has_started() {
            return Err("observer_already_armed_or_started");
        }
        let observer = Arc::new(native_inventory::Observer::new(control));
        *slot = Some(observer.clone());
        Ok(native_inventory::NativeInventoryCanary(observer))
    }
    /// Trusted Rust negative-test injection ONLY; deliberately mismatches two
    /// independently verified package files. Absent from all release builds.
    #[doc(hidden)]
    pub fn mismatched_identity_configuration_for_canary(&self, other: &Self) -> FixedExecutable {
        FixedExecutable::verified_canary(self.0.path.clone(), other.0.clone())
    }
}

// Compiled only in the debug Windows package module AND this explicit CI cfg.
// No production discovery, arbitrary PID, process enumeration, or OpenProcess.
#[cfg(shirushi_release_helper_native_inventory)]
pub mod native_inventory {
    use super::*;
    use crate::inspection_supervisor::Control;
    use serde::Serialize;
    use std::{
        collections::BTreeSet,
        os::windows::io::{FromRawHandle, IntoRawHandle, OwnedHandle},
        sync::Mutex,
        thread::{self, JoinHandle},
        time::{Duration, Instant},
    };
    const MAX_MODULES: usize = 256;
    const MAX_WINDOW: Duration = Duration::from_secs(30);
    type Worker = JoinHandle<Result<NativeModuleInventory, &'static str>>;
    #[link(name = "kernel32")]
    extern "system" {
        fn GetCurrentProcess() -> *mut c_void;
        fn DuplicateHandle(
            source_process: *mut c_void,
            source: *mut c_void,
            target_process: *mut c_void,
            target: *mut *mut c_void,
            access: u32,
            inherit: i32,
            options: u32,
        ) -> i32;
        fn CloseHandle(handle: *mut c_void) -> i32;
        fn WaitForSingleObject(handle: *mut c_void, milliseconds: u32) -> u32;
        fn GetLastError() -> u32;
        fn GetSystemDirectoryW(buffer: *mut u16, size: u32) -> u32;
        fn K32EnumProcessModulesEx(
            process: *mut c_void,
            modules: *mut *mut c_void,
            bytes: u32,
            needed: *mut u32,
            filter: u32,
        ) -> i32;
        fn K32GetModuleFileNameExW(
            process: *mut c_void,
            module: *mut c_void,
            name: *mut u16,
            size: u32,
        ) -> u32;
    }
    #[derive(Serialize, PartialEq, Eq, PartialOrd, Ord)]
    pub struct NativeModule {
        pub basename: String,
        pub category: &'static str,
    }
    #[derive(Serialize)]
    pub struct NativeModuleInventory {
        pub sampled_post_resume: bool,
        pub successful_samples: u32,
        pub failed_samples: u32,
        pub partial_copy_samples: u32,
        pub incomplete_samples: u32,
        pub errors: BTreeSet<u32>,
        pub observation_duration_ms: u64,
        pub handle_closed: bool,
        pub modules: BTreeSet<NativeModule>,
    }
    pub(crate) struct Observer {
        control: Control,
        stop: Arc<AtomicBool>,
        worker: Mutex<Option<Result<Worker, &'static str>>>,
    }
    impl Observer {
        pub(super) fn new(control: Control) -> Self {
            Self {
                control,
                stop: Arc::new(AtomicBool::new(false)),
                worker: Mutex::new(None),
            }
        }
        pub(super) fn seed(&self, child: &Child, helper: PathBuf) {
            let mut worker = self.worker.lock().unwrap_or_else(|e| e.into_inner());
            if worker.is_some() {
                return;
            }
            let start = || -> Result<Worker, &'static str> {
                let mut raw = std::ptr::null_mut();
                // Same owned process object; reduced query/read/synchronize rights,
                // noninheritable duplicate. No PID resolution or handle logging.
                let current = unsafe { GetCurrentProcess() };
                if unsafe {
                    DuplicateHandle(
                        current,
                        child.as_raw_handle(),
                        current,
                        &mut raw,
                        0x00100410,
                        0,
                        0,
                    )
                } == 0
                {
                    return Err("owned_handle_duplicate_failed");
                }
                // SAFETY: successful duplicate transfers one live unique handle.
                let handle = unsafe { OwnedHandle::from_raw_handle(raw) };
                let mut flags = 0;
                if unsafe { GetHandleInformation(handle.as_raw_handle(), &mut flags) } == 0
                    || flags & 1 != 0
                {
                    return Err("owned_handle_not_noninheritable");
                }
                let control = self.control.clone();
                let stop = self.stop.clone();
                thread::Builder::new()
                    .name("canary-native-inventory".into())
                    .spawn(move || {
                        let result = observe(&handle, &helper, &control, &stop);
                        // OwnedHandle still closes automatically on unwind. On the
                        // normal route record explicit close success before returning.
                        let closed = unsafe { CloseHandle(handle.into_raw_handle()) } != 0;
                        if !closed {
                            return Err("owned_handle_close_failed");
                        }
                        result.map(|mut evidence| {
                            evidence.handle_closed = true;
                            evidence
                        })
                    })
                    .map_err(|_| "observer_thread_spawn_failed")
            };
            *worker = Some(start());
        }
        fn finish(&self) -> Result<NativeModuleInventory, &'static str> {
            self.stop.store(true, Ordering::SeqCst);
            let worker = self.worker.lock().unwrap_or_else(|e| e.into_inner()).take();
            worker
                .ok_or("observer_never_seeded")??
                .join()
                .map_err(|_| "observer_thread_panicked")?
        }
    }
    pub struct NativeInventoryCanary(Arc<Observer>);
    impl NativeInventoryCanary {
        pub fn finish(self) -> Result<NativeModuleInventory, &'static str> {
            self.0.finish()
        }
    }
    impl Drop for NativeInventoryCanary {
        fn drop(&mut self) {
            let _ = self.0.finish();
        }
    }
    fn system_directory() -> Option<PathBuf> {
        let mut buffer = vec![0u16; 32768];
        let len = unsafe { GetSystemDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) } as usize;
        (len > 0 && len < buffer.len()).then(|| PathBuf::from(OsString::from_wide(&buffer[..len])))
    }
    fn same_path(left: &Path, right: &Path) -> bool {
        match (left.to_str(), right.to_str()) {
            (Some(a), Some(b)) => a.eq_ignore_ascii_case(b),
            _ => false,
        }
    }
    fn module_record(path: &Path, helper: &Path, system: Option<&Path>) -> Option<NativeModule> {
        let basename = path.file_name()?.to_str()?.to_ascii_lowercase();
        // Log-safe basename only. Unexpected names make that sample incomplete.
        if basename.is_empty()
            || basename.len() > 128
            || !basename
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
        {
            return None;
        }
        let category = if same_path(path, helper) {
            "OWN_HELPER"
        } else if ["vcruntime", "msvcp", "concrt"]
            .iter()
            .any(|p| basename.starts_with(p))
        {
            // Location is not evidence that a VC runtime is OS-provided.
            "VC_RUNTIME"
        } else if system
            .map(|s| path.parent().map(|p| same_path(p, s)).unwrap_or(false))
            .unwrap_or(false)
        {
            "WINDOWS_SYSTEM"
        } else if system.is_some() {
            "OTHER_NON_SYSTEM"
        } else {
            "UNKNOWN"
        };
        Some(NativeModule { basename, category })
    }
    fn observe(
        handle: &OwnedHandle,
        helper: &Path,
        control: &Control,
        stop: &AtomicBool,
    ) -> Result<NativeModuleInventory, &'static str> {
        let started = Instant::now();
        let system = system_directory();
        let mut evidence = NativeModuleInventory {
            sampled_post_resume: false,
            successful_samples: 0,
            failed_samples: 0,
            partial_copy_samples: 0,
            incomplete_samples: 0,
            errors: BTreeSet::new(),
            observation_duration_ms: 0,
            handle_closed: false,
            modules: BTreeSet::new(),
        };
        let mut modules = vec![std::ptr::null_mut(); MAX_MODULES];
        let mut name = vec![0u16; 32768];
        while !stop.load(Ordering::SeqCst) && started.elapsed() < MAX_WINDOW {
            // The existing marker is set only AFTER successful ResumeThread. No
            // suspended/preloader sample can count as runtime evidence.
            if !control.has_started() {
                thread::sleep(Duration::from_millis(1));
                continue;
            }
            match unsafe { WaitForSingleObject(handle.as_raw_handle(), 0) } {
                0 => break, // owned process already exited
                258 => {}   // WAIT_TIMEOUT: still live, sample without waiting
                _ => return Err("owned_process_wait_failed"),
            }
            let mut needed = 0;
            let capacity = (modules.len() * size_of::<*mut c_void>()) as u32;
            let enumerated = unsafe {
                K32EnumProcessModulesEx(
                    handle.as_raw_handle(),
                    modules.as_mut_ptr(),
                    capacity,
                    &mut needed,
                    3,
                )
            } != 0;
            if !enumerated {
                let error = unsafe { GetLastError() };
                evidence.failed_samples += 1;
                evidence.partial_copy_samples += u32::from(error == 299);
                // Bound distinct error output too; numeric categories only.
                if evidence.errors.len() < 16 {
                    evidence.errors.insert(error);
                }
            } else if needed == 0
                || needed > capacity
                || needed as usize % size_of::<*mut c_void>() != 0
            {
                evidence.failed_samples += 1;
                evidence.incomplete_samples += 1;
            } else {
                let count = needed as usize / size_of::<*mut c_void>();
                let mut sample = BTreeSet::new();
                let mut complete = true;
                for module in &modules[..count] {
                    if stop.load(Ordering::SeqCst) || started.elapsed() >= MAX_WINDOW {
                        complete = false;
                        break;
                    }
                    let len = unsafe {
                        K32GetModuleFileNameExW(
                            handle.as_raw_handle(),
                            *module,
                            name.as_mut_ptr(),
                            name.len() as u32,
                        )
                    } as usize;
                    if len == 0 {
                        let error = unsafe { GetLastError() };
                        evidence.partial_copy_samples += u32::from(error == 299);
                        if evidence.errors.len() < 16 {
                            evidence.errors.insert(error);
                        }
                        complete = false;
                        break;
                    }
                    if len >= name.len() - 1 {
                        complete = false;
                        break;
                    }
                    let path = PathBuf::from(OsString::from_wide(&name[..len]));
                    if let Some(record) = module_record(&path, helper, system.as_deref()) {
                        sample.insert(record);
                    } else {
                        complete = false;
                        break;
                    }
                }
                // A process may exit/load/unload during PSAPI reads. Count only
                // complete live observations including this exact helper path.
                if complete
                    && sample.iter().any(|m| m.category == "OWN_HELPER")
                    && unsafe { WaitForSingleObject(handle.as_raw_handle(), 0) } == 258
                {
                    if evidence.modules.union(&sample).count() > MAX_MODULES {
                        return Err("module_union_cap_exceeded");
                    }
                    evidence.modules.extend(sample);
                    evidence.successful_samples += 1;
                    evidence.sampled_post_resume = true;
                } else {
                    evidence.failed_samples += 1;
                    evidence.incomplete_samples += 1;
                }
            }
            thread::sleep(Duration::from_millis(2));
        }
        evidence.observation_duration_ms =
            started.elapsed().as_millis().min(u64::MAX as u128) as u64;
        // Missing runtime observations are rejected by the test; retain counts
        // here so a fast-exit/loader-transient gap is visible in the evidence.
        Ok(evidence)
    }
}
