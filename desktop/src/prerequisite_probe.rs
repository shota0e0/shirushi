//! Read-only Preview installer preflight, not a Runtime identity or UI proof.
//! Uses the already-locked statically linked x64 MSVC WebView2 loader only.
use std::ffi::OsString;

const FLAG: &str = "--shirushi-prerequisite-check";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Failure {
    Arguments,
    UnsupportedPlatform,
    OverridePresent,
    OverrideUnproven,
    ApiUnavailable,
    VersionUnproven,
}

impl Failure {
    fn exit_code(self) -> i32 {
        match self {
            Self::Arguments => 2,
            Self::UnsupportedPlatform => 3,
            Self::OverridePresent => 4,
            Self::OverrideUnproven => 5,
            Self::ApiUnavailable => 6,
            Self::VersionUnproven => 7,
        }
    }
}

fn request(args: impl IntoIterator<Item = OsString>) -> Option<Result<(), Failure>> {
    let mut args = args.into_iter();
    if args.next()?.as_os_str() != FLAG {
        return None;
    }
    Some(if args.next().is_none() {
        Ok(())
    } else {
        Err(Failure::Arguments)
    })
}

fn stable_version(version: &str) -> bool {
    if version.is_empty() || version.len() > 43 {
        return false;
    }
    let mut count = 0;
    let mut positive = false;
    for part in version.split('.') {
        count += 1;
        if count > 4 || part.is_empty() || !part.bytes().all(|b| b.is_ascii_digit()) {
            return false;
        }
        let Ok(value) = part.parse::<u32>() else { return false; };
        if value.to_string() != part {
            return false;
        }
        positive |= value != 0;
    }
    count == 4 && positive
}

fn environment_clear(keys: impl IntoIterator<Item = OsString>) -> Result<(), Failure> {
    for (index, key) in keys.into_iter().enumerate() {
        if index >= 512 {
            return Err(Failure::OverrideUnproven);
        }
        // Deny all runtime overrides, including unknown/future WEBVIEW2_ keys;
        // do not inspect or publish values or the rest of the environment.
        if key.to_string_lossy().to_ascii_uppercase().starts_with("WEBVIEW2_") {
            return Err(Failure::OverridePresent);
        }
    }
    Ok(())
}

pub(crate) fn run_if_requested(args: impl IntoIterator<Item = OsString>) -> Option<i32> {
    let requested = request(args)?;
    Some(match requested.and_then(|_| probe()) {
        Ok(version) => {
            eprintln!("SHIRUSHI_PREREQUISITE_CHECK:STABLE_WEBVIEW2_API_AVAILABLE;version={version};scope=X64_API_AVAILABILITY_ONLY");
            0
        }
        Err(failure) => {
            eprintln!("SHIRUSHI_PREREQUISITE_CHECK:NOT_PROVEN;code={}", failure.exit_code());
            failure.exit_code()
        }
    })
}

#[cfg(all(windows, target_arch = "x86_64", target_env = "msvc"))]
fn policy_clear() -> Result<(), Failure> {
    use windows_sys::Win32::{
        Foundation::{ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND, ERROR_SUCCESS},
        System::Registry::{
            RegCloseKey, RegOpenKeyExW, HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE,
            KEY_READ, KEY_WOW64_32KEY, KEY_WOW64_64KEY,
        },
    };
    let path: Vec<u16> = "Software\\Policies\\Microsoft\\Edge\\WebView2\0".encode_utf16().collect();
    for hive in [HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER] {
        for view in [KEY_WOW64_64KEY, KEY_WOW64_32KEY] {
            let mut key = std::ptr::null_mut();
            let status = unsafe { RegOpenKeyExW(hive, path.as_ptr(), 0, KEY_READ | view, &mut key) };
            match status {
                ERROR_FILE_NOT_FOUND | ERROR_PATH_NOT_FOUND => (),
                ERROR_SUCCESS => {
                    let closed = unsafe { RegCloseKey(key) };
                    if closed != ERROR_SUCCESS { return Err(Failure::OverrideUnproven); }
                    // Conservative: any WebView2 policy root (even empty or
                    // unrelated policy) blocks attribution rather than assuming
                    // wildcard/AUMID/executable override precedence is absent.
                    return Err(Failure::OverridePresent);
                }
                _ => return Err(Failure::OverrideUnproven),
            }
        }
    }
    Ok(())
}

#[cfg(all(windows, target_arch = "x86_64", target_env = "msvc"))]
fn overrides_clear() -> Result<(), Failure> {
    environment_clear(std::env::vars_os().map(|(key, _)| key))?;
    policy_clear()
}

#[cfg(all(windows, target_arch = "x86_64", target_env = "msvc"))]
fn probe() -> Result<String, Failure> {
    use webview2_com_sys::Microsoft::Web::WebView2::Win32::GetAvailableCoreWebView2BrowserVersionString;
    use windows_core::{PCWSTR, PWSTR};
    #[link(name = "ole32")]
    extern "system" { fn CoTaskMemFree(memory: *const std::ffi::c_void); }
    struct Allocation(PWSTR);
    impl Drop for Allocation {
        fn drop(&mut self) {
            if !self.0.is_null() { unsafe { CoTaskMemFree(self.0.0.cast()) }; }
        }
    }
    overrides_clear()?;
    // The SDK owns returned CoTaskMem allocation, including any non-null output
    // on an error path. RAII frees it on every exit after the API invocation.
    let mut allocation = Allocation(PWSTR::null());
    let api = unsafe { GetAvailableCoreWebView2BrowserVersionString(PCWSTR::null(), &mut allocation.0) };
    overrides_clear()?;
    api.map_err(|_| Failure::ApiUnavailable)?;
    if allocation.0.is_null() { return Err(Failure::VersionUnproven); }
    let mut units = Vec::new();
    // SDK version is NUL terminated; do not perform an unbounded string scan.
    for index in 0..44 {
        let unit = unsafe { *allocation.0.0.add(index) };
        if unit == 0 {
            let version = String::from_utf16(&units).map_err(|_| Failure::VersionUnproven)?;
            return if stable_version(&version) { Ok(version) } else { Err(Failure::VersionUnproven) };
        }
        units.push(unit);
    }
    Err(Failure::VersionUnproven)
}

#[cfg(not(all(windows, target_arch = "x86_64", target_env = "msvc")))]
fn probe() -> Result<String, Failure> { Err(Failure::UnsupportedPlatform) }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cli_is_exact_single_flag_and_does_not_consume_normal_or_explorer_startup() {
        assert_eq!(request(Vec::<OsString>::new()), None);
        assert_eq!(request([FLAG].map(OsString::from)), Some(Ok(())));
        assert_eq!(request([FLAG, "extra"].map(OsString::from)), Some(Err(Failure::Arguments)));
        // Malformed probe argv exits without calling the live API at all.
        assert_eq!(run_if_requested([FLAG, "extra"].map(OsString::from)), Some(2));
        assert_eq!(request(["--shirushi-explorer", "add", "--", "C:\\image.png"].map(OsString::from)), None);
        assert_eq!(request(["--shirushi-prerequisite-chec"].map(OsString::from)), None);
    }
    #[test]
    fn only_stable_positive_canonical_four_part_versions_are_accepted() {
        for value in ["154.0.4258.53", "1.0.0.0", "0.0.0.1"] { assert!(stable_version(value)); }
        for value in ["", "0.0.0.0", "154.0.4258.53 beta", "154.0.4258.53 dev", "154.0.4258.53 canary", "154.0.4258", "154.0.4258.53.1", "154.0.4258.-1", "0154.0.4258.53", "4294967296.0.0.0", " 154.0.4258.53", "154.0.4258.53\0"] { assert!(!stable_version(value), "accepted {value:?}"); }
    }
    #[test]
    fn all_webview_override_keys_fail_closed_without_reading_values() {
        assert_eq!(environment_clear(["PATH", "TEMP"].map(OsString::from)), Ok(()));
        for key in ["WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", "WEBVIEW2_CHANNEL_SEARCH_KIND", "WEBVIEW2_RELEASE_CHANNELS", "WEBVIEW2_RELEASE_CHANNEL_PREFERENCE", "WEBVIEW2_USER_DATA_FOLDER", "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "webview2_future_override"] {
            assert_eq!(environment_clear([OsString::from(key)]), Err(Failure::OverridePresent));
        }
        assert_eq!(environment_clear((0..513).map(|n| OsString::from(format!("NORMAL_{n}")))), Err(Failure::OverrideUnproven));
    }
    #[test]
    fn every_failure_code_is_nonzero_and_distinct() {
        let failures = [Failure::Arguments, Failure::UnsupportedPlatform, Failure::OverridePresent, Failure::OverrideUnproven, Failure::ApiUnavailable, Failure::VersionUnproven];
        let codes: std::collections::BTreeSet<_> = failures.into_iter().map(Failure::exit_code).collect();
        assert_eq!(codes.len(), failures.len());
        assert!(!codes.contains(&0));
    }
}
