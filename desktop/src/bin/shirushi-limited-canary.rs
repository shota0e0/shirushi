//! DEVELOPMENT-ONLY bounded F3A Limited C2PA/CAWG canary.

#[cfg(not(debug_assertions))]
compile_error!("shirushi-limited-canary is a development-only debug executable");

#[cfg(not(windows))]
fn main() {
    eprintln!("RUNNER_UNSUPPORTED: Windows only");
    std::process::exit(12);
}

#[cfg(windows)]
mod runner {
    use serde::{Deserialize, Serialize};
    use sha2::{Digest, Sha256};
    use std::collections::{BTreeMap, BTreeSet};
    use std::ffi::{OsStr, OsString};
    use std::fs::{self, File, OpenOptions};
    use std::io::{self, Read, Write};
    use std::os::windows::{ffi::OsStrExt, fs::MetadataExt, io::AsRawHandle, process::CommandExt};
    use std::path::{Path, PathBuf};
    use std::process::{Child, Command, ExitStatus, Stdio};
    use std::thread;
    use std::time::{Duration, Instant};
    use windows_sys::Win32::Foundation::{
        CloseHandle, GetLastError, ERROR_ALREADY_EXISTS, HANDLE, INVALID_HANDLE_VALUE,
        WAIT_OBJECT_0, WAIT_TIMEOUT,
    };
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
    };
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectBasicAccountingInformation,
        JobObjectExtendedLimitInformation, QueryInformationJobObject, SetInformationJobObject,
        TerminateJobObject, JOBOBJECT_BASIC_ACCOUNTING_INFORMATION,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows_sys::Win32::System::Threading::{
        CreateMutexW, OpenThread, ResumeThread, WaitForSingleObject, THREAD_SUSPEND_RESUME,
    };

    const CREATE_SUSPENDED: u32 = 0x0000_0004;
    const RUNNER: &str = "shirushi-limited-canary.exe";
    const MANIFEST: &str = "manifest.json";
    const SUMS: &str = "SHA256SUMS";
    const RUNTIME_ROOT: &str = ".venv-py312";
    const VENV_PYTHON: &str = ".venv-py312/Scripts/python.exe";
    const SEAL: &str = ".venv-py312/.shirushi-f3a-seal.json";
    const FIXTURE_MANIFEST: &str = "fixtures/manifest.json";
    const RUNNER_CONTRACT_VERSION: u64 = 1;
    // Outer wire v2 is independent of the unchanged runner/seal and inner v1 contracts.
    const OUTER_CONTRACT_VERSION: u64 = 2;
    const C2PATOOL_SHA256: &str =
        "90cbcebe30250f8e8c53416d32ed86065dc04a23be86e4a2337f5cd1badfa0b7";
    const SCRIPT: &str = "scripts/inspect_limited_fixture.py";
    const FIXTURE: &str = "fixtures/valid_shirushi.png";
    const EXPECTED_FIXTURE_SHA256: &str =
        "558c4044228761f91ad1ee1a4637bdd868c65f0e9954e7de928a1262e3076316";
    const EXPECTED_FIXTURE_SIZE: u64 = 319_495;
    const EXPECTED_IDENTITY: &str = "3.12.10";
    const MAX_CONTROL_BYTES: u64 = 16 * 1024 * 1024;
    const MAX_MANIFEST_FILES: usize = 100_000;
    const MAX_STDOUT_BYTES: usize = 64 * 1024;
    const MAX_STDERR_BYTES: usize = 16 * 1024;
    const IDENTITY_TIMEOUT: Duration = Duration::from_secs(20);
    const PREPARE_TIMEOUT: Duration = Duration::from_secs(120);
    const INSPECT_TIMEOUT: Duration = Duration::from_secs(60);
    const JOB_DRAIN_TIMEOUT: Duration = Duration::from_secs(5);
    const IDENTITY_CODE: &str = concat!(
        "import json,platform,struct,sys;",
        "print(json.dumps({",
        "'version':'.'.join(map(str,sys.version_info[:3])),",
        "'bits':struct.calcsize('P')*8,",
        "'implementation':platform.python_implementation(),",
        "'prefix':sys.prefix,",
        "'basePrefix':sys.base_prefix,",
        "'executable':sys.executable,",
        "'baseExecutable':getattr(sys,'_base_executable',sys.executable)",
        "},separators=(',',':')))"
    );

    const REQUIRED_PAYLOAD: &[&str] = &[
        RUNNER,
        SCRIPT,
        "src/inspection_metadata.py",
        "runtime-lock.json",
        "tools/c2patool.exe",
        FIXTURE,
        FIXTURE_MANIFEST,
        "config/verifier-settings.json",
    ];

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    struct Failure {
        code: &'static str,
        reason: &'static str,
    }
    impl Failure {
        const fn new(code: &'static str, reason: &'static str) -> Self {
            Self { code, reason }
        }
    }
    type Result<T> = std::result::Result<T, Failure>;
    fn fail<T>(code: &'static str, reason: &'static str) -> Result<T> {
        Err(Failure::new(code, reason))
    }
    fn policy_or(code: &'static str, reason: &'static str, error: &io::Error) -> Failure {
        if error.raw_os_error() == Some(1260) {
            Failure::new(
                "LOCAL_POLICY_BLOCKED",
                "Windows explicitly rejected a fixed child executable",
            )
        } else {
            Failure::new(code, reason)
        }
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    enum Action {
        Prepare,
        InspectLimitedFixture,
    }
    fn parse_action(args: &[OsString]) -> Result<Action> {
        match args {
            [action, flag, python]
                if action == OsStr::new("prepare")
                    && flag == OsStr::new("--python")
                    && Path::new(python).is_absolute()
                    && name_is(Path::new(python), "python.exe") =>
            {
                Ok(Action::Prepare)
            }
            [action] if action == OsStr::new("inspect-limited-fixture") => {
                Ok(Action::InspectLimitedFixture)
            }
            _ => fail(
                "INVALID_ARGUMENTS",
                "use prepare --python ABSOLUTE_PYTHON_EXE or inspect-limited-fixture",
            ),
        }
    }

    #[derive(Debug, Deserialize)]
    #[serde(deny_unknown_fields, rename_all = "camelCase")]
    struct PackageManifest {
        schema_version: u64,
        artifact_type: String,
        files: Vec<FileRecord>,
    }
    #[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
    #[serde(deny_unknown_fields)]
    struct FileRecord {
        path: String,
        size: u64,
        sha256: String,
    }
    #[derive(Debug, Deserialize, Serialize, PartialEq, Eq)]
    #[serde(deny_unknown_fields, rename_all = "camelCase")]
    struct RuntimeSeal {
        seal_format_version: u64,
        runner_contract_version: u64,
        package_manifest_sha256: String,
        runtime_lock_sha256: String,
        fixture_manifest_sha256: String,
        identity: SealedIdentity,
        runtime_artifacts: Vec<FileRecord>,
        wheel_artifacts: Vec<FileRecord>,
    }
    #[derive(Debug, Deserialize, Serialize, PartialEq, Eq)]
    #[serde(deny_unknown_fields, rename_all = "camelCase")]
    struct SealedIdentity {
        version: String,
        bits: u64,
        implementation: String,
        python_executable_sha256: String,
        base_python_executable_sha256: String,
    }
    #[derive(Debug, Deserialize)]
    #[serde(deny_unknown_fields, rename_all = "camelCase")]
    struct PythonIdentity {
        version: String,
        bits: u64,
        implementation: String,
        prefix: String,
        base_prefix: String,
        executable: String,
        base_executable: String,
    }

    fn name_is(path: &Path, expected: &str) -> bool {
        path.file_name()
            .and_then(OsStr::to_str)
            .is_some_and(|name| name.eq_ignore_ascii_case(expected))
    }
    fn is_lower_sha256(value: &str) -> bool {
        value.len() == 64
            && value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    }
    fn reserved_windows_name(part: &str) -> bool {
        let stem = part.split('.').next().unwrap_or("").to_ascii_uppercase();
        matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
            || (stem.len() == 4
                && (stem.starts_with("COM") || stem.starts_with("LPT"))
                && stem.as_bytes()[3].is_ascii_digit()
                && stem.as_bytes()[3] != b'0')
    }
    fn safe_relative(value: &str) -> Result<PathBuf> {
        if value.is_empty()
            || !value.is_ascii()
            || value.contains('\\')
            || value.contains(':')
            || value.starts_with('/')
            || value.split('/').any(|part| {
                part.is_empty()
                    || part == "."
                    || part == ".."
                    || part.ends_with('.')
                    || part.ends_with(' ')
                    || part.bytes().any(|byte| byte < 0x20 || byte == 0x7f)
                    || reserved_windows_name(part)
            })
        {
            return fail("PACKAGE_INVALID", "unsafe package-relative path");
        }
        Ok(value.split('/').collect())
    }
    fn relative_string(root: &Path, path: &Path) -> Result<String> {
        let relative = path
            .strip_prefix(root)
            .map_err(|_| Failure::new("PACKAGE_INVALID", "package entry escapes root"))?
            .to_str()
            .ok_or(Failure::new(
                "PACKAGE_INVALID",
                "package entry name is not Unicode",
            ))?
            .replace('\\', "/");
        safe_relative(&relative)?;
        Ok(relative)
    }
    fn is_reparse(path: &Path) -> Result<bool> {
        let metadata = fs::symlink_metadata(path)
            .map_err(|_| Failure::new("PACKAGE_INVALID", "package component unavailable"))?;
        Ok(metadata.file_attributes() & 0x400 != 0)
    }
    fn reject_existing_chain(path: &Path, code: &'static str) -> Result<()> {
        for component in path.ancestors() {
            if component.as_os_str().is_empty() || !component.exists() {
                continue;
            }
            let metadata = fs::symlink_metadata(component)
                .map_err(|_| Failure::new(code, "path component unavailable"))?;
            if metadata.file_attributes() & 0x400 != 0 {
                return fail(code, "linked or reparse-point path component");
            }
        }
        Ok(())
    }
    fn digest(path: &Path, code: &'static str) -> Result<(u64, String)> {
        let metadata = fs::symlink_metadata(path)
            .map_err(|_| Failure::new(code, "file metadata unavailable"))?;
        if !metadata.is_file() || metadata.file_attributes() & 0x400 != 0 {
            return fail(code, "expected regular nonlinked file");
        }
        let mut file = File::open(path).map_err(|_| Failure::new(code, "file unreadable"))?;
        let mut sha = Sha256::new();
        let mut size = 0_u64;
        let mut buffer = [0_u8; 65_536];
        loop {
            let read = file
                .read(&mut buffer)
                .map_err(|_| Failure::new(code, "file unreadable"))?;
            if read == 0 {
                break;
            }
            size = size
                .checked_add(read as u64)
                .ok_or(Failure::new(code, "file size overflow"))?;
            sha.update(&buffer[..read]);
        }
        Ok((size, format!("{:x}", sha.finalize())))
    }
    fn bounded_read(path: &Path, limit: u64, code: &'static str) -> Result<Vec<u8>> {
        let metadata = fs::symlink_metadata(path)
            .map_err(|_| Failure::new(code, "control file unavailable"))?;
        if !metadata.is_file() || metadata.file_attributes() & 0x400 != 0 || metadata.len() > limit
        {
            return fail(code, "control file is invalid or exceeds its bound");
        }
        let mut bytes = Vec::new();
        File::open(path)
            .and_then(|file| file.take(limit + 1).read_to_end(&mut bytes))
            .map_err(|_| Failure::new(code, "control file unreadable"))?;
        if bytes.len() as u64 > limit {
            return fail(code, "control file grew beyond its bound");
        }
        Ok(bytes)
    }

    fn collect_tree(
        root: &Path,
        directory: &Path,
        skip_runtime: bool,
        files: &mut BTreeSet<String>,
        folded: &mut BTreeSet<String>,
    ) -> Result<()> {
        let entries = fs::read_dir(directory)
            .map_err(|_| Failure::new("PACKAGE_INVALID", "package tree unreadable"))?;
        for item in entries {
            let item =
                item.map_err(|_| Failure::new("PACKAGE_INVALID", "package tree unreadable"))?;
            let path = item.path();
            let relative = relative_string(root, &path)?;
            let metadata = fs::symlink_metadata(&path)
                .map_err(|_| Failure::new("PACKAGE_INVALID", "package entry unavailable"))?;
            if metadata.file_attributes() & 0x400 != 0 {
                return fail("PACKAGE_INVALID", "linked package entry");
            }
            if skip_runtime && relative.eq_ignore_ascii_case(RUNTIME_ROOT) {
                if !metadata.is_dir() {
                    return fail("PACKAGE_INVALID", "mutable runtime root is not a directory");
                }
                continue;
            }
            let folded_name = relative.to_ascii_lowercase();
            if !folded.insert(folded_name) {
                return fail("PACKAGE_INVALID", "case-colliding package entry");
            }
            if metadata.is_dir() {
                collect_tree(root, &path, skip_runtime, files, folded)?;
            } else if metadata.is_file() {
                files.insert(relative);
            } else {
                return fail("PACKAGE_INVALID", "non-regular package entry");
            }
        }
        Ok(())
    }

    fn parse_manifest(raw: &[u8]) -> Result<PackageManifest> {
        let manifest: PackageManifest = serde_json::from_slice(raw)
            .map_err(|_| Failure::new("PACKAGE_INVALID", "manifest JSON/schema invalid"))?;
        if manifest.schema_version != 1
            || manifest.artifact_type != "F3A_LIMITED_DEVELOPMENT_CANARY"
            || manifest.files.is_empty()
            || manifest.files.len() > MAX_MANIFEST_FILES
        {
            return fail(
                "PACKAGE_INVALID",
                "manifest version, type, or file count invalid",
            );
        }
        Ok(manifest)
    }
    fn assert_immutable(root: &Path) -> Result<()> {
        reject_existing_chain(root, "PACKAGE_INVALID")?;
        for required in REQUIRED_PAYLOAD.iter().chain([MANIFEST, SUMS].iter()) {
            let path = root.join(safe_relative(required)?);
            if !path.exists() {
                return fail("PACKAGE_INVALID", "required package component missing");
            }
            reject_existing_chain(&path, "PACKAGE_INVALID")?;
        }
        let runtime = root.join(RUNTIME_ROOT);
        if runtime.exists() {
            reject_existing_chain(&runtime, "PACKAGE_INVALID")?;
        }
        let raw = bounded_read(&root.join(MANIFEST), MAX_CONTROL_BYTES, "PACKAGE_INVALID")?;
        let manifest = parse_manifest(&raw)?;
        let mut declared = BTreeMap::<String, FileRecord>::new();
        let mut folded = BTreeSet::new();
        for record in manifest.files {
            if !is_lower_sha256(&record.sha256)
                || record.path == MANIFEST
                || record.path == SUMS
                || record.path.eq_ignore_ascii_case(RUNTIME_ROOT)
                || record
                    .path
                    .to_ascii_lowercase()
                    .starts_with(&format!("{}/", RUNTIME_ROOT.to_ascii_lowercase()))
                || !folded.insert(record.path.to_ascii_lowercase())
            {
                return fail(
                    "PACKAGE_INVALID",
                    "manifest path/hash is invalid or duplicated",
                );
            }
            let path = root.join(safe_relative(&record.path)?);
            reject_existing_chain(&path, "PACKAGE_INVALID")?;
            let (size, sha256) = digest(&path, "PACKAGE_INVALID")?;
            if size != record.size || sha256 != record.sha256 {
                return fail("PACKAGE_INVALID", "manifest file size or hash mismatch");
            }
            if declared.insert(record.path.clone(), record).is_some() {
                return fail("PACKAGE_INVALID", "duplicate manifest path");
            }
        }
        for required in REQUIRED_PAYLOAD {
            if !declared.contains_key(*required) {
                return fail("PACKAGE_INVALID", "required payload absent from manifest");
            }
        }
        if !declared
            .keys()
            .any(|path| path.starts_with("runtime/site-packages/"))
        {
            return fail("PACKAGE_INVALID", "runtime site-packages payload missing");
        }
        let mut actual = BTreeSet::new();
        let mut actual_folded = BTreeSet::new();
        collect_tree(root, root, true, &mut actual, &mut actual_folded)?;
        actual.remove(MANIFEST);
        actual.remove(SUMS);
        if actual != declared.keys().cloned().collect() {
            return fail(
                "PACKAGE_INVALID",
                "unexpected or missing immutable package payload",
            );
        }
        let sums_raw = bounded_read(&root.join(SUMS), MAX_CONTROL_BYTES, "PACKAGE_INVALID")?;
        let sums_text = std::str::from_utf8(&sums_raw)
            .map_err(|_| Failure::new("PACKAGE_INVALID", "SHA256SUMS is not UTF-8"))?;
        let mut sums = BTreeMap::<String, String>::new();
        let mut sums_folded = BTreeSet::new();
        for line in sums_text.lines() {
            let (sha256, name) = line
                .split_once("  ")
                .ok_or(Failure::new("PACKAGE_INVALID", "SHA256SUMS line malformed"))?;
            if !is_lower_sha256(sha256)
                || name == SUMS
                || !sums_folded.insert(name.to_ascii_lowercase())
            {
                return fail("PACKAGE_INVALID", "SHA256SUMS entry invalid or duplicated");
            }
            let path = root.join(safe_relative(name)?);
            reject_existing_chain(&path, "PACKAGE_INVALID")?;
            let (_, actual_sha256) = digest(&path, "PACKAGE_INVALID")?;
            if actual_sha256 != sha256 {
                return fail("PACKAGE_INVALID", "SHA256SUMS hash mismatch");
            }
            sums.insert(name.to_owned(), sha256.to_owned());
        }
        let expected_sums: BTreeSet<String> = declared
            .keys()
            .cloned()
            .chain(std::iter::once(MANIFEST.to_owned()))
            .collect();
        if sums.keys().cloned().collect::<BTreeSet<_>>() != expected_sums {
            return fail("PACKAGE_INVALID", "SHA256SUMS coverage mismatch");
        }
        let fixture = declared
            .get(FIXTURE)
            .ok_or(Failure::new("PACKAGE_INVALID", "fixed fixture absent"))?;
        if fixture.size != EXPECTED_FIXTURE_SIZE || fixture.sha256 != EXPECTED_FIXTURE_SHA256 {
            return fail("PACKAGE_INVALID", "fixed fixture identity mismatch");
        }
        if declared
            .get("tools/c2patool.exe")
            .map_or(true, |file| file.sha256 != C2PATOOL_SHA256)
        {
            return fail("PACKAGE_INVALID", "pinned c2patool digest differs");
        }
        Ok(())
    }

    struct Handle(HANDLE);
    impl Drop for Handle {
        fn drop(&mut self) {
            if !self.0.is_null() && self.0 != INVALID_HANDLE_VALUE {
                unsafe { CloseHandle(self.0) };
            }
        }
    }
    fn acquire_run_lock() -> Result<Handle> {
        let name: Vec<u16> = OsStr::new("Local\\Shirushi-F3A-Limited-Development-Canary")
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        let raw = unsafe { CreateMutexW(std::ptr::null(), 1, name.as_ptr()) };
        if raw.is_null() {
            return fail("RUNNER_BUSY", "bounded canary lock is unavailable");
        }
        let existed = unsafe { GetLastError() } == ERROR_ALREADY_EXISTS;
        let handle = Handle(raw);
        if existed {
            return fail("RUNNER_BUSY", "another bounded canary runner owns the lock");
        }
        Ok(handle)
    }
    fn new_job() -> Result<Handle> {
        let raw = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if raw.is_null() {
            return fail(
                "JOB_CONTAINMENT_FAILED",
                "process containment job unavailable",
            );
        }
        let job = Handle(raw);
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let configured = unsafe {
            SetInformationJobObject(
                job.0,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const std::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if configured == 0 {
            return fail(
                "JOB_CONTAINMENT_FAILED",
                "process containment job configuration failed",
            );
        }
        Ok(job)
    }
    fn active_processes(job: HANDLE) -> Result<u32> {
        let mut accounting = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
        let queried = unsafe {
            QueryInformationJobObject(
                job,
                JobObjectBasicAccountingInformation,
                &mut accounting as *mut _ as *mut std::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
                std::ptr::null_mut(),
            )
        };
        if queried == 0 {
            return fail("JOB_CONTAINMENT_FAILED", "job process count unavailable");
        }
        Ok(accounting.ActiveProcesses)
    }
    fn wait_job_zero(job: HANDLE, deadline: Duration) -> Result<()> {
        let start = Instant::now();
        loop {
            if active_processes(job)? == 0 {
                return Ok(());
            }
            if start.elapsed() >= deadline {
                return fail(
                    "ORPHAN_PROCESS_DETECTED",
                    "contained child process remained active",
                );
            }
            thread::sleep(Duration::from_millis(20));
        }
    }
    fn terminate_job_and_verify(job: HANDLE) -> Result<()> {
        unsafe { TerminateJobObject(job, 1) };
        wait_job_zero(job, JOB_DRAIN_TIMEOUT)
    }
    fn resume_suspended(child: &Child) -> Result<()> {
        let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
        if snapshot == INVALID_HANDLE_VALUE {
            return fail(
                "JOB_CONTAINMENT_FAILED",
                "suspended thread snapshot unavailable",
            );
        }
        let _snapshot = Handle(snapshot);
        let mut row = THREADENTRY32 {
            dwSize: std::mem::size_of::<THREADENTRY32>() as u32,
            ..Default::default()
        };
        let mut present = unsafe { Thread32First(snapshot, &mut row) } != 0;
        while present {
            if row.th32OwnerProcessID == child.id() {
                let raw = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, row.th32ThreadID) };
                if raw.is_null() {
                    return fail(
                        "JOB_CONTAINMENT_FAILED",
                        "suspended child thread unavailable",
                    );
                }
                let _thread = Handle(raw);
                if unsafe { ResumeThread(raw) } == u32::MAX {
                    return fail("JOB_CONTAINMENT_FAILED", "suspended child could not resume");
                }
                return Ok(());
            }
            present = unsafe { Thread32Next(snapshot, &mut row) } != 0;
        }
        fail("JOB_CONTAINMENT_FAILED", "suspended child thread not found")
    }

    #[derive(Debug)]
    struct Capture {
        bytes: Vec<u8>,
        overflow: bool,
    }
    fn capture<R: Read + Send + 'static>(
        mut reader: R,
        limit: usize,
    ) -> thread::JoinHandle<io::Result<Capture>> {
        thread::spawn(move || {
            let mut bytes = Vec::new();
            let mut overflow = false;
            let mut buffer = [0_u8; 8192];
            loop {
                let count = reader.read(&mut buffer)?;
                if count == 0 {
                    break;
                }
                if bytes.len() < limit {
                    let retained = (limit - bytes.len()).min(count);
                    bytes.extend_from_slice(&buffer[..retained]);
                    if retained != count {
                        overflow = true;
                    }
                } else {
                    overflow = true;
                }
            }
            Ok(Capture { bytes, overflow })
        })
    }
    struct ProcessOutput {
        status: ExitStatus,
        stdout: Vec<u8>,
        stderr: Vec<u8>,
    }
    fn milliseconds(duration: Duration) -> u32 {
        duration.as_millis().min(u128::from(u32::MAX)) as u32
    }
    fn join_capture(handle: thread::JoinHandle<io::Result<Capture>>) -> Result<Capture> {
        handle
            .join()
            .map_err(|_| Failure::new("CHILD_IO_FAILED", "child output reader failed"))?
            .map_err(|_| Failure::new("CHILD_IO_FAILED", "child output stream failed"))
    }
    fn reap_failed_child(
        child: &mut Child,
        job: HANDLE,
        stdout_reader: thread::JoinHandle<io::Result<Capture>>,
        stderr_reader: thread::JoinHandle<io::Result<Capture>>,
    ) -> Result<()> {
        // A failed zero-process proof wins immediately; do not enter an unbounded wait/join
        // while a child may still own these pipes. Job drop remains kill-on-close.
        terminate_job_and_verify(job)?;
        let reaped = child.wait().map_err(|_| {
            Failure::new(
                "JOB_CONTAINMENT_FAILED",
                "terminated child exit unavailable",
            )
        });
        let stdout = join_capture(stdout_reader);
        let stderr = join_capture(stderr_reader);
        reaped?;
        stdout.map_err(|_| {
            Failure::new(
                "JOB_CONTAINMENT_FAILED",
                "terminated stdout was not drained",
            )
        })?;
        stderr.map_err(|_| {
            Failure::new(
                "JOB_CONTAINMENT_FAILED",
                "terminated stderr was not drained",
            )
        })?;
        if active_processes(job)? != 0 {
            return fail(
                "ORPHAN_PROCESS_DETECTED",
                "job was not empty after failure cleanup",
            );
        }
        Ok(())
    }
    fn run_contained(
        command: &mut Command,
        timeout: Duration,
        spawn_code: &'static str,
        timeout_code: &'static str,
    ) -> Result<ProcessOutput> {
        let job = new_job()?;
        command
            .creation_flags(CREATE_SUSPENDED)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = command.spawn().map_err(|error| {
            policy_or(spawn_code, "fixed child process could not start", &error)
        })?;
        if unsafe { AssignProcessToJobObject(job.0, child.as_raw_handle() as HANDLE) } == 0 {
            let _ = child.kill();
            let _ = child.wait();
            return fail(
                "JOB_CONTAINMENT_FAILED",
                "child could not be assigned to containment",
            );
        }
        let stdout = child
            .stdout
            .take()
            .ok_or(Failure::new("CHILD_IO_FAILED", "child stdout unavailable"))?;
        let stderr = child
            .stderr
            .take()
            .ok_or(Failure::new("CHILD_IO_FAILED", "child stderr unavailable"))?;
        let stdout_reader = capture(stdout, MAX_STDOUT_BYTES);
        let stderr_reader = capture(stderr, MAX_STDERR_BYTES);
        if let Err(error) = resume_suspended(&child) {
            let cleanup = reap_failed_child(&mut child, job.0, stdout_reader, stderr_reader);
            return after_cleanup(Err(error), cleanup);
        }
        let wait =
            unsafe { WaitForSingleObject(child.as_raw_handle() as HANDLE, milliseconds(timeout)) };
        if wait == WAIT_TIMEOUT {
            let cleanup = reap_failed_child(&mut child, job.0, stdout_reader, stderr_reader);
            return after_cleanup(
                fail(timeout_code, "contained child exceeded its deadline"),
                cleanup,
            );
        }
        if wait != WAIT_OBJECT_0 {
            let cleanup = reap_failed_child(&mut child, job.0, stdout_reader, stderr_reader);
            return after_cleanup(
                fail("JOB_CONTAINMENT_FAILED", "child wait state unavailable"),
                cleanup,
            );
        }
        let status = child
            .wait()
            .map_err(|_| Failure::new("JOB_CONTAINMENT_FAILED", "child exit unavailable"))?;
        if let Err(_drain_error) = wait_job_zero(job.0, JOB_DRAIN_TIMEOUT) {
            reap_failed_child(&mut child, job.0, stdout_reader, stderr_reader)?;
            return fail(
                "ORPHAN_PROCESS_DETECTED",
                "contained child process remained active",
            );
        }
        let stdout = join_capture(stdout_reader)?;
        let stderr = join_capture(stderr_reader)?;
        if stdout.overflow || stderr.overflow {
            terminate_job_and_verify(job.0)?;
            return fail(
                "OUTPUT_LIMIT_EXCEEDED",
                "contained child output exceeded its bound",
            );
        }
        if active_processes(job.0)? != 0 {
            terminate_job_and_verify(job.0)?;
            return fail(
                "ORPHAN_PROCESS_DETECTED",
                "job was not empty before result handling",
            );
        }
        Ok(ProcessOutput {
            status,
            stdout: stdout.bytes,
            stderr: stderr.bytes,
        })
    }

    fn package_root() -> Result<PathBuf> {
        let executable = std::env::current_exe()
            .map_err(|_| Failure::new("RUNNER_INTERNAL", "runner location unavailable"))?;
        reject_existing_chain(&executable, "PACKAGE_INVALID")?;
        if !name_is(&executable, RUNNER) {
            return fail(
                "PACKAGE_INVALID",
                "runner filename differs from package contract",
            );
        }
        let executable = fs::canonicalize(executable)
            .map_err(|_| Failure::new("PACKAGE_INVALID", "runner location invalid"))?;
        executable
            .parent()
            .map(Path::to_path_buf)
            .ok_or(Failure::new("PACKAGE_INVALID", "package root unavailable"))
    }

    struct Workspace {
        path: PathBuf,
    }
    impl Workspace {
        fn create(package: &Path) -> Result<Self> {
            let temporary_root = std::env::temp_dir();
            reject_existing_chain(&temporary_root, "CLEANUP_FAILED")?;
            let temporary_root = fs::canonicalize(temporary_root)
                .map_err(|_| Failure::new("CLEANUP_FAILED", "temporary root unavailable"))?;
            if temporary_root.starts_with(package) {
                return fail("CLEANUP_FAILED", "temporary root is inside package");
            }
            // mkdir is the exclusive allocator; no time or machine value enters the seal.
            for index in 0..1024_u32 {
                let path =
                    temporary_root.join(format!("shirushi-f3a-{}-{index}", std::process::id()));
                match fs::create_dir(&path) {
                    Ok(()) => {
                        reject_existing_chain(&path, "CLEANUP_FAILED")?;
                        return Ok(Self { path });
                    }
                    Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                    Err(_) => return fail("CLEANUP_FAILED", "temporary workspace creation failed"),
                }
            }
            fail("CLEANUP_FAILED", "temporary workspace allocation exhausted")
        }
        fn cleanup(self) -> Result<()> {
            reject_existing_chain(&self.path, "CLEANUP_FAILED")?;
            // Check every descendant before recursive removal; never traverse a reparse point.
            let mut files = BTreeSet::new();
            let mut folded = BTreeSet::new();
            collect_tree(&self.path, &self.path, false, &mut files, &mut folded).map_err(|_| {
                Failure::new("CLEANUP_FAILED", "temporary workspace containment invalid")
            })?;
            fs::remove_dir_all(&self.path).map_err(|_| {
                Failure::new("CLEANUP_FAILED", "temporary workspace removal failed")
            })?;
            if self.path.exists() {
                return fail(
                    "CLEANUP_FAILED",
                    "temporary workspace remained after cleanup",
                );
            }
            Ok(())
        }
    }

    fn fixed_command(executable: &Path, workspace: &Path) -> Result<Command> {
        if !executable.is_absolute() || !workspace.is_absolute() {
            return fail("RUNNER_INTERNAL", "fixed process path is not absolute");
        }
        reject_existing_chain(executable, "RUNTIME_UNAVAILABLE")?;
        let mut command = Command::new(executable);
        // An allowlist prevents Python, TLS, shell, and tool overrides inherited from callers.
        command.env_clear().current_dir(workspace);
        for key in ["SystemRoot", "WINDIR"] {
            if let Some(value) = std::env::var_os(key) {
                command.env(key, value);
            }
        }
        command
            .env("TMP", workspace)
            .env("TEMP", workspace)
            .env("LOCALAPPDATA", workspace)
            .env("APPDATA", workspace)
            .env("USERPROFILE", workspace)
            .env("HOME", workspace)
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .env("PYTHONNOUSERSITE", "1");
        Ok(command)
    }

    fn same_file(left: &Path, right: &Path) -> bool {
        match (fs::canonicalize(left), fs::canonicalize(right)) {
            (Ok(left), Ok(right)) => left == right,
            _ => false,
        }
    }
    fn valid_identity(identity: &PythonIdentity) -> bool {
        identity.version == EXPECTED_IDENTITY
            && identity.bits == 64
            && identity.implementation == "CPython"
    }
    fn python_identity(executable: &Path, workspace: &Path) -> Result<PythonIdentity> {
        let mut command = fixed_command(executable, workspace)?;
        command.args(["-I", "-B", "-S", "-c", IDENTITY_CODE]);
        let output = run_contained(
            &mut command,
            IDENTITY_TIMEOUT,
            "RUNTIME_UNAVAILABLE",
            "RUNTIME_UNAVAILABLE",
        )?;
        if !output.status.success() || !output.stderr.is_empty() {
            return fail("RUNTIME_UNAVAILABLE", "Python identity probe failed");
        }
        let identity: PythonIdentity = serde_json::from_slice(&output.stdout)
            .map_err(|_| Failure::new("RUNTIME_UNAVAILABLE", "Python identity schema invalid"))?;
        if !valid_identity(&identity) || !same_file(executable, Path::new(&identity.executable)) {
            return fail(
                "RUNTIME_UNAVAILABLE",
                "Python exact version or architecture differs",
            );
        }
        Ok(identity)
    }

    fn validate_venv_layout(root: &Path) -> Result<()> {
        let venv = root.join(RUNTIME_ROOT);
        reject_existing_chain(&venv, "SEAL_INVALID")?;
        let configuration = bounded_read(&venv.join("pyvenv.cfg"), 16 * 1024, "SEAL_INVALID")?;
        let configuration = std::str::from_utf8(&configuration)
            .map_err(|_| Failure::new("SEAL_INVALID", "venv configuration invalid"))?;
        let mut system_sites = configuration
            .lines()
            .filter_map(|line| line.split_once('='))
            .filter(|(key, _)| {
                key.trim()
                    .eq_ignore_ascii_case("include-system-site-packages")
            });
        if !system_sites
            .next()
            .is_some_and(|(_, value)| value.trim().eq_ignore_ascii_case("false"))
            || system_sites.next().is_some()
        {
            return fail("SEAL_INVALID", "venv permits inherited site packages");
        }
        let mut files = BTreeSet::new();
        let mut folded = BTreeSet::new();
        collect_tree(&venv, &venv, false, &mut files, &mut folded)
            .map_err(|_| Failure::new("SEAL_INVALID", "venv tree containment invalid"))?;
        // Fresh --without-pip venv only. No .pth, sitecustomize, or installer mutation.
        const ALLOWED: &[&str] = &[
            "pyvenv.cfg",
            ".shirushi-f3a-seal.json",
            "Scripts/python.exe",
            "Scripts/pythonw.exe",
            "Scripts/activate",
            "Scripts/activate.bat",
            "Scripts/Activate.ps1",
            "Scripts/deactivate.bat",
        ];
        if files.iter().any(|file| !ALLOWED.contains(&file.as_str())) {
            return fail("SEAL_INVALID", "unexpected file in fresh no-pip venv");
        }
        Ok(())
    }

    fn configured_base_python(root: &Path) -> Result<PathBuf> {
        let raw = bounded_read(
            &root.join(RUNTIME_ROOT).join("pyvenv.cfg"),
            16 * 1024,
            "SEAL_INVALID",
        )?;
        let text = std::str::from_utf8(&raw)
            .map_err(|_| Failure::new("SEAL_INVALID", "venv configuration invalid"))?;
        let mut fields = BTreeMap::new();
        for line in text.lines().filter(|line| !line.trim().is_empty()) {
            let (key, value) = line.split_once('=').ok_or(Failure::new(
                "SEAL_INVALID",
                "venv configuration entry invalid",
            ))?;
            let key = key.trim();
            if ![
                "home",
                "include-system-site-packages",
                "version",
                "executable",
                "command",
            ]
            .contains(&key)
                || fields.insert(key, value.trim()).is_some()
            {
                return fail(
                    "SEAL_INVALID",
                    "venv configuration duplicate or unknown entry",
                );
            }
        }
        if fields.get("version") != Some(&EXPECTED_IDENTITY) {
            return fail("SEAL_INVALID", "venv configuration Python version differs");
        }
        let home = Path::new(
            fields
                .get("home")
                .copied()
                .ok_or(Failure::new("SEAL_INVALID", "venv base home absent"))?,
        );
        if !home.is_absolute() {
            return fail("SEAL_INVALID", "venv base home is not absolute");
        }
        let executable = home.join("python.exe");
        reject_existing_chain(&executable, "SEAL_INVALID")?;
        let recorded = Path::new(
            fields
                .get("executable")
                .copied()
                .ok_or(Failure::new("SEAL_INVALID", "venv base executable absent"))?,
        );
        if !recorded.is_absolute() || !same_file(recorded, &executable) {
            return fail(
                "SEAL_INVALID",
                "venv base executable differs from base home",
            );
        }
        Ok(executable)
    }

    fn wheel_records(root: &Path) -> Result<Vec<FileRecord>> {
        let raw = bounded_read(
            &root.join("runtime-lock.json"),
            MAX_CONTROL_BYTES,
            "PACKAGE_INVALID",
        )?;
        let lock: serde_json::Value = serde_json::from_slice(&raw)
            .map_err(|_| Failure::new("PACKAGE_INVALID", "runtime lock JSON invalid"))?;
        if lock
            .get("schemaVersion")
            .and_then(serde_json::Value::as_u64)
            != Some(1)
            || lock.get("python").and_then(serde_json::Value::as_str) != Some(EXPECTED_IDENTITY)
            || lock
                .get("implementation")
                .and_then(serde_json::Value::as_str)
                != Some("CPython")
            || lock.get("bits").and_then(serde_json::Value::as_u64) != Some(64)
        {
            return fail("PACKAGE_INVALID", "runtime lock Python identity invalid");
        }
        let dependencies = lock
            .get("dependencies")
            .and_then(serde_json::Value::as_array)
            .ok_or(Failure::new(
                "PACKAGE_INVALID",
                "runtime dependency declarations absent",
            ))?;
        let mut records = Vec::new();
        let mut names = BTreeSet::new();
        for dependency in dependencies {
            let name = dependency
                .get("name")
                .and_then(serde_json::Value::as_str)
                .ok_or(Failure::new(
                    "PACKAGE_INVALID",
                    "runtime dependency name invalid",
                ))?;
            if !["cffi", "cryptography", "pillow", "pycparser"].contains(&name)
                || !names.insert(name)
            {
                return fail("PACKAGE_INVALID", "runtime dependency closure differs");
            }
            let filename = dependency
                .get("filename")
                .and_then(serde_json::Value::as_str)
                .ok_or(Failure::new(
                    "PACKAGE_INVALID",
                    "runtime wheel filename invalid",
                ))?;
            if safe_relative(filename)?.components().count() != 1 || !filename.ends_with(".whl") {
                return fail("PACKAGE_INVALID", "runtime wheel path invalid");
            }
            let sha256 = dependency
                .get("sha256")
                .and_then(serde_json::Value::as_str)
                .filter(|value| is_lower_sha256(value))
                .ok_or(Failure::new(
                    "PACKAGE_INVALID",
                    "runtime wheel digest invalid",
                ))?;
            let size = dependency
                .get("size")
                .and_then(serde_json::Value::as_u64)
                .filter(|size| *size > 0)
                .ok_or(Failure::new(
                    "PACKAGE_INVALID",
                    "runtime wheel size invalid",
                ))?;
            records.push(FileRecord {
                path: filename.to_owned(),
                size,
                sha256: sha256.to_owned(),
            });
        }
        if names.len() != 4 {
            return fail("PACKAGE_INVALID", "minimal dependency closure incomplete");
        }
        records.sort_by(|left, right| left.path.cmp(&right.path));
        Ok(records)
    }

    fn compute_seal(root: &Path, workspace: &Path) -> Result<RuntimeSeal> {
        assert_immutable(root)?;
        validate_venv_layout(root)?;
        let configured_base = configured_base_python(root)?;
        let executable = root.join(VENV_PYTHON);
        let identity = python_identity(&executable, workspace)?;
        // CPython 3.12 -S deliberately leaves sys.prefix at its base prefix.
        // Package-local executable, fresh tree and cfg are checked independently.
        if !same_file(
            Path::new(&identity.prefix),
            Path::new(&identity.base_prefix),
        ) || !Path::new(&identity.base_executable).is_absolute()
        {
            return fail(
                "SEAL_INVALID",
                "Python identity is not package-local fresh venv",
            );
        }
        reject_existing_chain(Path::new(&identity.base_executable), "SEAL_INVALID")?;
        if !same_file(&configured_base, Path::new(&identity.base_executable)) {
            return fail("SEAL_INVALID", "Python probe differs from configured base");
        }
        let base_identity = python_identity(Path::new(&identity.base_executable), workspace)?;
        if !same_file(
            Path::new(&base_identity.prefix),
            Path::new(&base_identity.base_prefix),
        ) || !same_file(
            Path::new(&identity.base_prefix),
            Path::new(&base_identity.prefix),
        ) {
            return fail("SEAL_INVALID", "venv base Python identity differs");
        }
        let manifest = parse_manifest(&bounded_read(
            &root.join(MANIFEST),
            MAX_CONTROL_BYTES,
            "PACKAGE_INVALID",
        )?)?;
        let mut artifacts: Vec<FileRecord> = manifest
            .files
            .into_iter()
            .filter(|file| {
                file.path.starts_with("runtime/")
                    || file.path.starts_with("src/")
                    || file.path.starts_with("scripts/")
                    || file.path.starts_with("tools/")
                    || file.path.starts_with("config/")
                    || file.path == RUNNER
            })
            .collect();
        // Do not hash pyvenv.cfg/activation scripts: their absolute paths are intentionally not sealed.
        for relative in [VENV_PYTHON, ".venv-py312/Scripts/pythonw.exe"] {
            let (size, sha256) = digest(&root.join(relative), "SEAL_INVALID")?;
            artifacts.push(FileRecord {
                path: relative.to_owned(),
                size,
                sha256,
            });
        }
        artifacts.sort_by(|left, right| left.path.cmp(&right.path));
        Ok(RuntimeSeal {
            seal_format_version: 1,
            runner_contract_version: RUNNER_CONTRACT_VERSION,
            package_manifest_sha256: digest(&root.join(MANIFEST), "PACKAGE_INVALID")?.1,
            runtime_lock_sha256: digest(&root.join("runtime-lock.json"), "PACKAGE_INVALID")?.1,
            fixture_manifest_sha256: digest(&root.join(FIXTURE_MANIFEST), "PACKAGE_INVALID")?.1,
            identity: SealedIdentity {
                version: identity.version,
                bits: identity.bits,
                implementation: identity.implementation,
                python_executable_sha256: digest(&executable, "SEAL_INVALID")?.1,
                base_python_executable_sha256: digest(
                    Path::new(&identity.base_executable),
                    "SEAL_INVALID",
                )?
                .1,
            },
            runtime_artifacts: artifacts,
            wheel_artifacts: wheel_records(root)?,
        })
    }

    fn validate_seal(root: &Path, workspace: &Path) -> Result<()> {
        let raw = bounded_read(&root.join(SEAL), MAX_CONTROL_BYTES, "SEAL_INVALID")?;
        let recorded: RuntimeSeal = serde_json::from_slice(&raw)
            .map_err(|_| Failure::new("SEAL_INVALID", "seal JSON/schema invalid"))?;
        validate_bound_files(root, &recorded)?;
        if recorded != compute_seal(root, workspace)? {
            return fail(
                "SEAL_INVALID",
                "runtime seal mismatch; fresh extraction required",
            );
        }
        Ok(())
    }

    fn validate_bound_files(root: &Path, seal: &RuntimeSeal) -> Result<()> {
        assert_immutable(root)?;
        validate_venv_layout(root)?;
        if seal.seal_format_version != 1
            || seal.runner_contract_version != RUNNER_CONTRACT_VERSION
            || seal.identity.version != EXPECTED_IDENTITY
            || seal.identity.bits != 64
            || seal.identity.implementation != "CPython"
            || seal.package_manifest_sha256 != digest(&root.join(MANIFEST), "SEAL_INVALID")?.1
            || seal.runtime_lock_sha256
                != digest(&root.join("runtime-lock.json"), "SEAL_INVALID")?.1
            || seal.fixture_manifest_sha256
                != digest(&root.join(FIXTURE_MANIFEST), "SEAL_INVALID")?.1
            || seal.wheel_artifacts != wheel_records(root)?
        {
            return fail("SEAL_INVALID", "sealed package binding changed");
        }
        // Never execute a changed launcher/base binary merely to discover a seal mismatch.
        if seal.identity.python_executable_sha256
            != digest(&root.join(VENV_PYTHON), "SEAL_INVALID")?.1
            || seal.identity.base_python_executable_sha256
                != digest(&configured_base_python(root)?, "SEAL_INVALID")?.1
        {
            return fail("SEAL_INVALID", "sealed Python binary changed");
        }
        for record in &seal.runtime_artifacts {
            let path = root.join(safe_relative(&record.path)?);
            reject_existing_chain(&path, "SEAL_INVALID")?;
            let (size, sha256) = digest(&path, "SEAL_INVALID")?;
            if size != record.size || sha256 != record.sha256 {
                return fail("SEAL_INVALID", "sealed runtime artifact changed");
            }
        }
        Ok(())
    }

    fn prepare(root: &Path, python: &OsStr, workspace: &Path) -> Result<Option<RuntimeSeal>> {
        let python = Path::new(python);
        if !python.is_absolute() || !name_is(python, "python.exe") {
            return fail(
                "INVALID_ARGUMENTS",
                "prepare requires an absolute Python executable",
            );
        }
        if root.join(RUNTIME_ROOT).exists() {
            // Read-only reuse; every bound value must match. Never repair partial state.
            validate_seal(root, workspace)?;
            let recorded: RuntimeSeal = serde_json::from_slice(&bounded_read(
                &root.join(SEAL),
                MAX_CONTROL_BYTES,
                "SEAL_INVALID",
            )?)
            .map_err(|_| Failure::new("SEAL_INVALID", "seal schema invalid"))?;
            if recorded.identity.base_python_executable_sha256 != digest(python, "SEAL_INVALID")?.1
            {
                return fail("SEAL_INVALID", "requested Python differs from sealed base");
            }
            return Ok(None);
        }
        // Only fresh preparation is authorized to probe an unsealed requested base.
        validate_c2patool(root, workspace)?;
        let identity = python_identity(python, workspace)?;
        if !same_file(
            Path::new(&identity.prefix),
            Path::new(&identity.base_prefix),
        ) || python
            .parent()
            .and_then(Path::parent)
            .is_some_and(|parent| parent.join("pyvenv.cfg").exists())
        {
            return fail(
                "PREPARE_FAILED",
                "prepare requires base CPython, not another venv",
            );
        }
        // create_dir exclusively reserves this previously absent package-local runtime.
        fs::create_dir(root.join(RUNTIME_ROOT))
            .map_err(|_| Failure::new("PREPARE_FAILED", "fresh venv reservation failed"))?;
        reject_existing_chain(&root.join(RUNTIME_ROOT), "PREPARE_FAILED")?;
        let mut command = fixed_command(python, workspace)?;
        command
            .args(["-I", "-B", "-S", "-m", "venv", "--without-pip"])
            .arg(root.join(RUNTIME_ROOT));
        let output = run_contained(
            &mut command,
            PREPARE_TIMEOUT,
            "PREPARE_FAILED",
            "PREPARE_FAILED",
        )?;
        if !output.status.success() {
            return fail("PREPARE_FAILED", "fresh no-pip venv creation failed");
        }
        let seal = compute_seal(root, workspace)?;
        if seal.identity.base_python_executable_sha256 != digest(python, "SEAL_INVALID")?.1 {
            return fail("SEAL_INVALID", "created venv uses a different base Python");
        }
        Ok(Some(seal))
    }

    fn persist_seal(root: &Path, seal: &RuntimeSeal) -> Result<()> {
        validate_bound_files(root, seal)?;
        let raw = serde_json::to_vec_pretty(&seal)
            .map_err(|_| Failure::new("RUNNER_INTERNAL", "seal serialization failed"))?;
        let mut output = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(root.join(SEAL))
            .map_err(|_| Failure::new("SEAL_INVALID", "seal cannot be created exclusively"))?;
        output
            .write_all(&raw)
            .and_then(|()| output.sync_all())
            .map_err(|_| Failure::new("SEAL_INVALID", "seal persistence failed"))?;
        let stored = bounded_read(&root.join(SEAL), MAX_CONTROL_BYTES, "SEAL_INVALID")?;
        if stored != raw {
            return fail("SEAL_INVALID", "persisted seal differs");
        }
        Ok(())
    }

    #[derive(Debug, Deserialize, Serialize)]
    #[serde(deny_unknown_fields, rename_all = "camelCase")]
    struct Checks {
        c2pa: String,
        cawg: String,
        trustmark: String,
    }
    #[derive(Debug, Deserialize, Serialize)]
    #[serde(deny_unknown_fields, rename_all = "camelCase")]
    struct CompletedEnvelope {
        contract_version: u64,
        operation: String,
        result: String,
        completeness: String,
        checks: Checks,
        inspection: InnerInspection,
    }
    #[derive(Debug, Deserialize, Serialize)]
    #[serde(deny_unknown_fields, rename_all = "camelCase")]
    struct InnerInspection {
        contract: String,
        contract_version: u64,
        overall: String,
        reason_code: String,
        trustmark: String,
        full_verification_performed: bool,
        success_motion_eligible: bool,
        source: Source,
        c2pa: C2pa,
        cawg: Cawg,
    }
    #[derive(Debug, Deserialize, Serialize)]
    #[serde(deny_unknown_fields)]
    struct Source {
        sha256: String,
        size: u64,
        format: String,
    }
    #[derive(Debug, Deserialize, Serialize)]
    #[serde(deny_unknown_fields, rename_all = "camelCase")]
    struct C2pa {
        state: String,
        presence: String,
        parse: bool,
        assertion_digests_valid: bool,
        asset_binding_valid: bool,
        signature: String,
        trust_validated: bool,
    }
    #[derive(Debug, Deserialize, Serialize)]
    #[serde(deny_unknown_fields, rename_all = "camelCase")]
    struct Cawg {
        state: String,
        presence: String,
        ai_training_use: String,
        ai_inference_use: String,
    }
    #[derive(Debug, Deserialize)]
    #[serde(deny_unknown_fields, rename_all = "camelCase")]
    struct FailedEnvelope {
        contract_version: u64,
        operation: String,
        result: String,
        error: ErrorCode,
    }
    #[derive(Debug, Deserialize)]
    #[serde(deny_unknown_fields)]
    struct ErrorCode {
        code: String,
    }

    fn validate_completed(raw: &[u8]) -> Result<CompletedEnvelope> {
        let envelope: CompletedEnvelope = serde_json::from_slice(raw)
            .map_err(|_| Failure::new("RESULT_INVALID", "inspection JSON/schema invalid"))?;
        let inner = &envelope.inspection;
        let c2pa = &inner.c2pa;
        let cawg = &inner.cawg;
        if envelope.contract_version != OUTER_CONTRACT_VERSION
            || envelope.operation != "limited_c2pa_cawg_inspection"
            || envelope.result != "LIMITED_INSPECTION"
            || envelope.completeness != "INCOMPLETE"
            || envelope.checks.c2pa != "INSPECTED"
            || envelope.checks.cawg != "INSPECTED"
            || envelope.checks.trustmark != "NOT_CHECKED"
            || inner.contract != "shirushi-limited-inspection"
            || inner.contract_version != 1
            || inner.overall != "LIMITED_INSPECTION"
            || inner.reason_code != "LIMITED_SCOPE"
            || inner.trustmark != "NOT_CHECKED"
            || inner.full_verification_performed
            || inner.success_motion_eligible
            || inner.source.sha256 != EXPECTED_FIXTURE_SHA256
            || inner.source.size != EXPECTED_FIXTURE_SIZE
            || inner.source.format != "PNG"
            || c2pa.state != "INSPECTED"
            || c2pa.presence != "PRESENT"
            || !c2pa.parse
            || !c2pa.assertion_digests_valid
            || !c2pa.asset_binding_valid
            || c2pa.signature != "PREVIEW"
            || c2pa.trust_validated
            || cawg.state != "INSPECTED"
            || cawg.presence != "PRESENT"
            || cawg.ai_training_use != "NOT_WANTED"
            || cawg.ai_inference_use != "NOT_WANTED"
        {
            return fail("RESULT_INVALID", "fixed fixture limited semantics differ");
        }
        Ok(envelope)
    }
    fn validate_failed(raw: &[u8]) -> Result<Failure> {
        let envelope: FailedEnvelope = serde_json::from_slice(raw)
            .map_err(|_| Failure::new("RESULT_INVALID", "failure JSON/schema invalid"))?;
        const CODES: &[&str] = &[
            "INVALID_ARGUMENTS",
            "ENVIRONMENT_MISMATCH",
            "RUNTIME_UNAVAILABLE",
            "FIXTURE_CHANGED",
            "FIXTURE_INVALID",
            "C2PATOOL_MISSING",
            "C2PATOOL_INTEGRITY_FAILED",
            "C2PATOOL_START_FAILED",
            "C2PATOOL_OUTPUT_LIMIT",
            "C2PATOOL_TIMEOUT",
            "C2PATOOL_VERSION_MISMATCH",
            "SETTINGS_INVALID",
            "LIMITED_RESULT_INVALID",
            "RESULT_SERIALIZATION_FAILED",
            "INTERNAL_ENTRY_FAILURE",
        ];
        if envelope.contract_version != OUTER_CONTRACT_VERSION
            || envelope.operation != "limited_c2pa_cawg_inspection"
            || envelope.result != "INSPECTION_FAILED"
            || !CODES.contains(&envelope.error.code.as_str())
        {
            return fail("RESULT_INVALID", "failure envelope contract differs");
        }
        let code = CODES
            .iter()
            .copied()
            .find(|code| *code == envelope.error.code)
            .ok_or(Failure::new("RESULT_INVALID", "failure code invalid"))?;
        Ok(Failure::new(code, "bounded fixture inspection failed"))
    }

    fn validate_child_result(code: Option<i32>, raw: &[u8]) -> Result<CompletedEnvelope> {
        if code != Some(0) {
            let failure = validate_failed(raw)?;
            if code != Some(exit_code(failure)) {
                return fail("RESULT_INVALID", "failure envelope and child exit differ");
            }
            return Err(failure);
        }
        validate_completed(raw)
    }

    fn validate_c2patool(root: &Path, workspace: &Path) -> Result<()> {
        let tool = root.join("tools/c2patool.exe");
        if digest(&tool, "RUNTIME_UNAVAILABLE")?.1 != C2PATOOL_SHA256 {
            return fail("RUNTIME_UNAVAILABLE", "pinned c2patool binary differs");
        }
        let mut version = fixed_command(&tool, workspace)?;
        version.arg("--version");
        let output = run_contained(
            &mut version,
            IDENTITY_TIMEOUT,
            "RUNTIME_UNAVAILABLE",
            "RUNTIME_UNAVAILABLE",
        )?;
        if !output.status.success()
            || !output.stderr.is_empty()
            || std::str::from_utf8(&output.stdout).ok().map(str::trim) != Some("c2patool 0.26.60")
        {
            return fail("RUNTIME_UNAVAILABLE", "pinned c2patool version differs");
        }
        Ok(())
    }

    fn inspect(root: &Path, workspace: &Path) -> Result<Vec<u8>> {
        validate_seal(root, workspace)?;
        validate_c2patool(root, workspace)?;
        let mut command = fixed_command(&root.join(VENV_PYTHON), workspace)?;
        command.args(["-I", "-B", "-S"]).arg(root.join(SCRIPT));
        let output = run_contained(
            &mut command,
            INSPECT_TIMEOUT,
            "RUNTIME_UNAVAILABLE",
            "INSPECTION_FAILED",
        )?;
        let envelope = validate_child_result(output.status.code(), &output.stdout)?;
        // Detect child mutation before trusting any formally successful output.
        validate_seal(root, workspace)?;
        serde_json::to_vec(&envelope)
            .map_err(|_| Failure::new("RUNNER_INTERNAL", "result serialization failed"))
    }

    fn exit_code(error: Failure) -> i32 {
        match error.code {
            "INVALID_ARGUMENTS" => 2,
            "PACKAGE_INVALID"
            | "FIXTURE_CHANGED"
            | "SETTINGS_INVALID"
            | "C2PATOOL_INTEGRITY_FAILED" => 10,
            "PREPARE_FAILED" | "SEAL_INVALID" => 11,
            "RUNTIME_UNAVAILABLE"
            | "LOCAL_POLICY_BLOCKED"
            | "ENVIRONMENT_MISMATCH"
            | "C2PATOOL_MISSING"
            | "C2PATOOL_VERSION_MISMATCH" => 12,
            "INSPECTION_FAILED"
            | "FIXTURE_INVALID"
            | "C2PATOOL_START_FAILED"
            | "C2PATOOL_OUTPUT_LIMIT"
            | "C2PATOOL_TIMEOUT" => 13,
            "RESULT_INVALID" | "OUTPUT_LIMIT_EXCEEDED" | "LIMITED_RESULT_INVALID" => 14,
            "CLEANUP_FAILED" | "JOB_CONTAINMENT_FAILED" | "ORPHAN_PROCESS_DETECTED" => 15,
            _ => 16,
        }
    }
    fn failure_json(error: Failure) -> String {
        serde_json::json!({"contractVersion":OUTER_CONTRACT_VERSION,"operation":"limited_c2pa_cawg_inspection",
            "result":"INSPECTION_FAILED","error":{"code":error.code}})
        .to_string()
    }
    fn after_cleanup<T>(outcome: Result<T>, cleanup: Result<()>) -> Result<T> {
        cleanup?;
        outcome
    }
    fn dispatch(args: &[OsString]) -> Result<Option<Vec<u8>>> {
        let action = parse_action(args)?;
        let _lock = acquire_run_lock()?;
        let root = package_root()?;
        assert_immutable(&root)?;
        let workspace = Workspace::create(&root)?;
        let mut pending_seal = None;
        let outcome = match action {
            Action::Prepare => prepare(&root, &args[2], &workspace.path).map(|seal| {
                pending_seal = seal;
                None
            }),
            Action::InspectLimitedFixture => inspect(&root, &workspace.path).map(Some),
        };
        // Cleanup supersedes every inspection result, including previous errors.
        let outcome = after_cleanup(outcome, workspace.cleanup())?;
        if let Some(seal) = pending_seal {
            persist_seal(&root, &seal)?;
        }
        Ok(outcome)
    }
    pub fn main() {
        let args: Vec<OsString> = std::env::args_os().skip(1).collect();
        match dispatch(&args) {
            Ok(Some(output)) => {
                let mut stdout = io::stdout().lock();
                if stdout
                    .write_all(&output)
                    .and_then(|()| stdout.write_all(b"\n"))
                    .is_err()
                {
                    eprintln!("RUNNER_INTERNAL: result output failed");
                    std::process::exit(16);
                }
            }
            Ok(None) => eprintln!("PREPARE_OK: package-local runtime seal validated"),
            Err(error) => {
                let raw = failure_json(error);
                if io::stdout()
                    .lock()
                    .write_all(format!("{raw}\n").as_bytes())
                    .is_err()
                {
                    eprintln!("RUNNER_INTERNAL: failure output unavailable");
                    std::process::exit(16);
                }
                eprintln!("{}: {}", error.code, error.reason);
                std::process::exit(exit_code(error));
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        fn positive() -> serde_json::Value {
            serde_json::json!({"contractVersion":OUTER_CONTRACT_VERSION,"operation":"limited_c2pa_cawg_inspection",
                "result":"LIMITED_INSPECTION","completeness":"INCOMPLETE",
                "checks":{"c2pa":"INSPECTED","cawg":"INSPECTED","trustmark":"NOT_CHECKED"},
                "inspection":{"contract":"shirushi-limited-inspection","contractVersion":1,
                    "overall":"LIMITED_INSPECTION","reasonCode":"LIMITED_SCOPE","trustmark":"NOT_CHECKED",
                    "fullVerificationPerformed":false,"successMotionEligible":false,
                    "source":{"sha256":EXPECTED_FIXTURE_SHA256,"size":EXPECTED_FIXTURE_SIZE,"format":"PNG"},
                    "c2pa":{"state":"INSPECTED","presence":"PRESENT","parse":true,"assertionDigestsValid":true,
                        "assetBindingValid":true,"signature":"PREVIEW","trustValidated":false},
                    "cawg":{"state":"INSPECTED","presence":"PRESENT","aiTrainingUse":"NOT_WANTED","aiInferenceUse":"NOT_WANTED"}}})
        }
        #[test]
        fn cli_accepts_only_fixed_actions() {
            assert_eq!(
                parse_action(&["inspect-limited-fixture".into()]),
                Ok(Action::InspectLimitedFixture)
            );
            for args in [
                vec![],
                vec!["inspect-limited-fixture".into(), "private.png".into()],
                vec!["prepare".into()],
                vec!["prepare".into(), "--python".into(), "python.exe".into()],
                vec![
                    "prepare".into(),
                    "--python".into(),
                    "C:/Python/pythonw.exe".into(),
                ],
                vec!["shell".into()],
                vec![
                    "prepare".into(),
                    "--python".into(),
                    "python.exe".into(),
                    "--repair".into(),
                ],
            ] {
                assert!(parse_action(&args).is_err());
            }
        }
        #[test]
        fn cleanup_failure_and_orphan_failure_discard_every_result() {
            for code in [
                "CLEANUP_FAILED",
                "ORPHAN_PROCESS_DETECTED",
                "JOB_CONTAINMENT_FAILED",
            ] {
                let containment = Failure::new(code, "fixed");
                let success = Ok(positive().to_string().into_bytes());
                assert_eq!(after_cleanup(success, Err(containment)), Err(containment));
                let failed: Result<Vec<u8>> = Err(Failure::new("INSPECTION_FAILED", "fixed"));
                assert_eq!(after_cleanup(failed, Err(containment)), Err(containment));
                let resume_failed: Result<Vec<u8>> =
                    Err(Failure::new("JOB_CONTAINMENT_FAILED", "resume failed"));
                assert_eq!(
                    after_cleanup(resume_failed, Err(containment)),
                    Err(containment)
                );
            }
        }
        #[test]
        fn partial_reuse_refuses_before_requested_python_is_probed() {
            let executable = std::env::current_exe().unwrap();
            let workspace = Workspace::create(executable.parent().unwrap()).unwrap();
            let root = &workspace.path;
            fs::create_dir(root.join(RUNTIME_ROOT)).unwrap();
            // This regular file cannot execute. A premature identity probe would produce
            // RUNTIME_UNAVAILABLE (12), while partial reuse must refuse its seal (11).
            let requested = root.join("python.exe");
            File::create(&requested).unwrap();
            let missing = prepare(root, requested.as_os_str(), root).unwrap_err();
            assert_eq!(missing.code, "SEAL_INVALID");
            let mut seal = File::create(root.join(SEAL)).unwrap();
            seal.write_all(b"{}").unwrap();
            drop(seal);
            let malformed = prepare(root, requested.as_os_str(), root).unwrap_err();
            assert_eq!(malformed.code, "SEAL_INVALID");
            assert_eq!(fs::metadata(&requested).unwrap().len(), 0);
            assert_eq!(fs::read(root.join(SEAL)).unwrap(), b"{}");
            workspace.cleanup().unwrap();
        }
        #[test]
        fn identity_requires_exact_cpython_x64() {
            let raw = br#"{"version":"3.12.10","bits":64,"implementation":"CPython","prefix":"x","basePrefix":"x","executable":"x","baseExecutable":"x"}"#;
            let mut identity: PythonIdentity = serde_json::from_slice(raw).unwrap();
            assert!(valid_identity(&identity));
            identity.version = "3.12.11".into();
            assert!(!valid_identity(&identity));
            identity.version = EXPECTED_IDENTITY.into();
            identity.bits = 32;
            assert!(!valid_identity(&identity));
            identity.bits = 64;
            identity.implementation = "PyPy".into();
            assert!(!valid_identity(&identity));
        }
        #[test]
        fn complete_positive_schema_required() {
            let value = positive();
            assert!(validate_completed(&serde_json::to_vec(&value).unwrap()).is_ok());
            for pointer in [
                "/completeness",
                "/inspection/trustmark",
                "/inspection/c2pa/trustValidated",
                "/inspection/cawg/aiTrainingUse",
                "/inspection/source/sha256",
            ] {
                let mut changed = value.clone();
                *changed.pointer_mut(pointer).unwrap() = serde_json::json!("PASS");
                assert!(validate_completed(&serde_json::to_vec(&changed).unwrap()).is_err());
            }
            for pointer in [
                "/inspection/fullVerificationPerformed",
                "/inspection/successMotionEligible",
            ] {
                let mut changed = value.clone();
                *changed.pointer_mut(pointer).unwrap() = serde_json::json!(true);
                assert!(validate_completed(&serde_json::to_vec(&changed).unwrap()).is_err());
            }
            for pointer in [
                "/inspection/c2pa/parse",
                "/inspection/c2pa/assertionDigestsValid",
                "/inspection/c2pa/assetBindingValid",
            ] {
                let mut changed = value.clone();
                *changed.pointer_mut(pointer).unwrap() = serde_json::json!(false);
                assert!(validate_completed(&serde_json::to_vec(&changed).unwrap()).is_err());
            }
        }
        #[test]
        fn outer_v2_preserves_inner_v1_value_semantics() {
            let value = positive();
            let envelope = validate_completed(value.to_string().as_bytes()).unwrap();
            let serialized = serde_json::to_value(&envelope).unwrap();
            assert_eq!(serialized["contractVersion"], 2);
            assert_eq!(serialized["result"], "LIMITED_INSPECTION");
            assert_eq!(serialized["completeness"], "INCOMPLETE");
            assert_eq!(serialized["inspection"]["contractVersion"], 1);
            assert_eq!(serialized["inspection"], value["inspection"]);
            assert_eq!(RUNNER_CONTRACT_VERSION, 1);
        }
        #[test]
        fn old_outer_and_every_hybrid_are_rejected() {
            for version in [1, OUTER_CONTRACT_VERSION] {
                for result in ["INSPECTION_COMPLETED", "LIMITED_INSPECTION"] {
                    for completeness in ["LIMITED", "INCOMPLETE"] {
                        let mut value = positive();
                        value["contractVersion"] = version.into();
                        value["result"] = result.into();
                        value["completeness"] = completeness.into();
                        assert_eq!(
                            validate_completed(value.to_string().as_bytes()).is_ok(),
                            version == OUTER_CONTRACT_VERSION
                                && result == "LIMITED_INSPECTION"
                                && completeness == "INCOMPLETE"
                        );
                    }
                }
            }
            let mut inner_version = positive();
            inner_version["inspection"]["contractVersion"] = OUTER_CONTRACT_VERSION.into();
            assert!(validate_completed(inner_version.to_string().as_bytes()).is_err());
            let mut future = positive();
            future["contractVersion"] = 3.into();
            assert!(validate_completed(future.to_string().as_bytes()).is_err());
        }
        #[test]
        fn child_exit_must_match_success_or_fixed_failure() {
            let success = positive().to_string();
            assert!(validate_child_result(Some(0), success.as_bytes()).is_ok());
            for code in [Some(2), Some(10), Some(14), None] {
                assert_eq!(
                    validate_child_result(code, success.as_bytes())
                        .unwrap_err()
                        .code,
                    "RESULT_INVALID"
                );
            }
            let failed = failure_json(Failure::new("FIXTURE_CHANGED", "fixed"));
            assert_eq!(
                validate_child_result(Some(10), failed.as_bytes())
                    .unwrap_err()
                    .code,
                "FIXTURE_CHANGED"
            );
            for code in [Some(0), Some(11), None] {
                assert_eq!(
                    validate_child_result(code, failed.as_bytes())
                        .unwrap_err()
                        .code,
                    "RESULT_INVALID"
                );
            }
        }
        #[test]
        fn json_rejects_duplicates_unknown_missing_and_trailing_objects() {
            let value = positive();
            let raw = value.to_string();
            for invalid in [
                raw.replacen(
                    "\"contractVersion\":2",
                    "\"contractVersion\":2,\"contractVersion\":2",
                    1,
                ),
                raw.replacen(
                    "\"contractVersion\":1",
                    "\"contractVersion\":1,\"contractVersion\":1",
                    1,
                ),
                format!("{raw}{raw}"),
                "{}".into(),
                "null".into(),
            ] {
                assert!(validate_completed(invalid.as_bytes()).is_err());
            }
            let mut unknown = value.clone();
            unknown["extra"] = true.into();
            assert!(validate_completed(unknown.to_string().as_bytes()).is_err());
            let mut unknown = value.clone();
            unknown["inspection"]["c2pa"]["extra"] = true.into();
            assert!(validate_completed(unknown.to_string().as_bytes()).is_err());
            let mut missing = value;
            missing["inspection"]["cawg"]
                .as_object_mut()
                .unwrap()
                .remove("presence");
            assert!(validate_completed(missing.to_string().as_bytes()).is_err());
        }
        #[test]
        fn negative_or_full_success_inner_never_completes_fixed_fixture() {
            for (overall, reason) in [
                ("INCOMPLETE", "C2PA_ABSENT"),
                ("INCOMPLETE", "CAWG_ABSENT"),
                ("INCOMPLETE", "C2PA_MALFORMED"),
                ("PASS", "LIMITED_SCOPE"),
                ("COMPLETE", "LIMITED_SCOPE"),
            ] {
                let mut changed = positive();
                changed["inspection"]["overall"] = overall.into();
                changed["inspection"]["reasonCode"] = reason.into();
                assert!(validate_completed(changed.to_string().as_bytes()).is_err());
            }
        }
        #[test]
        fn failure_never_accepts_partial_result_or_path_code() {
            let valid = failure_json(Failure::new("FIXTURE_CHANGED", "fixed"));
            assert!(validate_failed(valid.as_bytes()).is_ok());
            let mut old: serde_json::Value = serde_json::from_str(&valid).unwrap();
            assert_eq!(old["contractVersion"], OUTER_CONTRACT_VERSION);
            assert!(old.get("inspection").is_none());
            old["contractVersion"] = 1.into();
            assert!(validate_failed(old.to_string().as_bytes()).is_err());
            old["contractVersion"] = 3.into();
            assert!(validate_failed(old.to_string().as_bytes()).is_err());
            assert!(validate_failed(
                valid
                    .replacen(
                        "\"contractVersion\":2",
                        "\"contractVersion\":2,\"contractVersion\":2",
                        1
                    )
                    .as_bytes()
            )
            .is_err());
            assert!(validate_failed(
                valid
                    .replacen(
                        "\"code\":\"FIXTURE_CHANGED\"",
                        "\"code\":\"FIXTURE_CHANGED\",\"code\":\"FIXTURE_CHANGED\"",
                        1
                    )
                    .as_bytes()
            )
            .is_err());
            let mut invalid: serde_json::Value = serde_json::from_str(&valid).unwrap();
            invalid["inspection"] = positive()["inspection"].clone();
            assert!(validate_failed(invalid.to_string().as_bytes()).is_err());
            assert!(validate_failed(
                failure_json(Failure::new("C:/private/user", "fixed")).as_bytes()
            )
            .is_err());
        }
        #[test]
        fn seal_is_path_free_and_every_digest_is_bound() {
            let seal = RuntimeSeal {
                seal_format_version: 1,
                runner_contract_version: 1,
                package_manifest_sha256: "a".repeat(64),
                runtime_lock_sha256: "b".repeat(64),
                fixture_manifest_sha256: "c".repeat(64),
                identity: SealedIdentity {
                    version: EXPECTED_IDENTITY.into(),
                    bits: 64,
                    implementation: "CPython".into(),
                    python_executable_sha256: "d".repeat(64),
                    base_python_executable_sha256: "e".repeat(64),
                },
                runtime_artifacts: vec![FileRecord {
                    path: "runtime/site-packages/module.py".into(),
                    size: 1,
                    sha256: "f".repeat(64),
                }],
                wheel_artifacts: vec![FileRecord {
                    path: "approved.whl".into(),
                    size: 1,
                    sha256: "0".repeat(64),
                }],
            };
            let raw = serde_json::to_string(&seal).unwrap();
            assert!(!raw.contains("prefix") && !raw.contains("timestamp") && !raw.contains("C:"));
            let mut changed: RuntimeSeal = serde_json::from_str(&raw).unwrap();
            assert_eq!(seal, changed);
            changed.runtime_lock_sha256 = "0".repeat(64);
            assert_ne!(seal, changed);
            let mut changed: RuntimeSeal = serde_json::from_str(&raw).unwrap();
            changed.runtime_artifacts[0].sha256 = "0".repeat(64);
            assert_ne!(seal, changed);
            let mut changed: RuntimeSeal = serde_json::from_str(&raw).unwrap();
            changed.identity.python_executable_sha256 = "0".repeat(64);
            assert_ne!(seal, changed);
        }
        #[test]
        fn owner_exit_codes_and_containment_are_fixed() {
            for (code, expected) in [
                ("INVALID_ARGUMENTS", 2),
                ("PACKAGE_INVALID", 10),
                ("SEAL_INVALID", 11),
                ("RUNTIME_UNAVAILABLE", 12),
                ("INSPECTION_FAILED", 13),
                ("RESULT_INVALID", 14),
                ("CLEANUP_FAILED", 15),
                ("ORPHAN_PROCESS_DETECTED", 15),
                ("JOB_CONTAINMENT_FAILED", 15),
                ("RUNNER_INTERNAL", 16),
            ] {
                assert_eq!(exit_code(Failure::new(code, "fixed")), expected);
            }
            for path in [
                "../outside",
                "C:/private",
                "a\\b",
                "NUL.txt",
                "folder/COM1",
                "a/../b",
            ] {
                assert!(safe_relative(path).is_err());
            }
        }
    }
}

#[cfg(windows)]
fn main() {
    runner::main();
}
