//! Bounded development input adapter. Production path/OS isolation is not proven here.
//! The inspection core receives owned immutable bytes, never a locator to reopen.

use crate::service::ServiceFailure;
use sha2::{Digest, Sha256};
use std::{fmt, fs, io::Read, path::Path, sync::Arc};

/// Approved skeleton ceiling only. Production size/memory limits: BENCHMARK_REQUIRED.
pub const MAX_SNAPSHOT_BYTES: usize = super::MAX_INPUT_BYTES;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DetectedFormat {
    Png,
    Jpeg,
    Unsupported,
}

impl DetectedFormat {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Png => "PNG",
            Self::Jpeg => "JPEG",
            Self::Unsupported => "UNSUPPORTED",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SnapshotFingerprint {
    sha256: [u8; 32],
    size: usize,
    format: DetectedFormat,
}

impl SnapshotFingerprint {
    pub fn sha256_hex(&self) -> String {
        self.sha256
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect()
    }

    pub fn size(&self) -> usize {
        self.size
    }

    pub fn format(&self) -> DetectedFormat {
        self.format
    }
}

/// Fields are private; no caller can replace the bytes or manufacture their metadata.
#[derive(Clone)]
pub struct Snapshot {
    bytes: Arc<[u8]>,
    fingerprint: SnapshotFingerprint,
}

impl fmt::Debug for Snapshot {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Snapshot")
            .field("fingerprint", &self.fingerprint)
            .finish_non_exhaustive()
    }
}

impl Snapshot {
    pub fn from_bytes(bytes: &[u8]) -> Result<Self, ServiceFailure> {
        if bytes.len() > MAX_SNAPSHOT_BYTES {
            return Err(ServiceFailure::ResourceLimitExceeded);
        }
        Ok(Self::from_bounded_bytes(bytes.into()))
    }

    /// Reads no more than ceiling+1 bytes; the extra byte is an overflow probe.
    /// Read errors are deliberately not carried into diagnostics or outcomes.
    pub fn from_reader(reader: &mut impl Read) -> Result<Self, ServiceFailure> {
        let mut bytes = Vec::new();
        let mut chunk = [0u8; 8192];
        loop {
            let remaining = MAX_SNAPSHOT_BYTES + 1 - bytes.len();
            let count = remaining.min(chunk.len());
            let read = match reader.read(&mut chunk[..count]) {
                Ok(0) => break,
                Ok(read) => read,
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(_) => return Err(ServiceFailure::InputUnavailable),
            };
            if bytes.len() + read > MAX_SNAPSHOT_BYTES {
                return Err(ServiceFailure::ResourceLimitExceeded);
            }
            bytes
                .try_reserve(read)
                .map_err(|_| ServiceFailure::ResourceLimitExceeded)?;
            bytes.extend_from_slice(&chunk[..read]);
        }
        Ok(Self::from_bounded_bytes(bytes.into()))
    }

    fn from_bounded_bytes(bytes: Arc<[u8]>) -> Self {
        let format = if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
            DetectedFormat::Png
        } else if bytes.starts_with(b"\xff\xd8\xff") {
            DetectedFormat::Jpeg
        } else {
            DetectedFormat::Unsupported
        };
        let fingerprint = SnapshotFingerprint {
            sha256: Sha256::digest(&bytes).into(),
            size: bytes.len(),
            format,
        };
        Self { bytes, fingerprint }
    }

    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub fn fingerprint(&self) -> &SnapshotFingerprint {
        &self.fingerprint
    }
}

/// Acquisition-only metadata; Debug is redacted and no locator enters a result.
pub struct InputLocator<'a> {
    path: &'a Path,
}

impl fmt::Debug for InputLocator<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("InputLocator(REDACTED)")
    }
}

impl<'a> InputLocator<'a> {
    pub fn new(path: &'a Path) -> Result<Self, ServiceFailure> {
        if !path.is_absolute()
            || path
                .components()
                .any(|part| matches!(part, std::path::Component::ParentDir))
        {
            return Err(ServiceFailure::InputUnavailable);
        }
        #[cfg(windows)]
        {
            use std::path::{Component, Prefix};
            if !matches!(path.components().next(), Some(Component::Prefix(prefix))
                if matches!(prefix.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_)))
                || path.components().skip(1).any(|part| {
                    matches!(part, Component::Normal(name) if name.to_string_lossy().contains(':'))
                })
            {
                return Err(ServiceFailure::InputUnavailable);
            }
        }
        Ok(Self { path })
    }

    /// Bounded ordinary-file adapter for disposable local test inputs.
    /// This is not the future helper's complete reparse/network/TOCTOU policy.
    pub fn capture(&self) -> Result<Snapshot, ServiceFailure> {
        for ancestor in self.path.ancestors() {
            let metadata =
                fs::symlink_metadata(ancestor).map_err(|_| ServiceFailure::InputUnavailable)?;
            if metadata.file_type().is_symlink() {
                return Err(ServiceFailure::InputUnavailable);
            }
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                if metadata.file_attributes() & 0x400 != 0 {
                    return Err(ServiceFailure::InputUnavailable);
                }
            }
        }
        let mut options = fs::OpenOptions::new();
        options.read(true);
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            options.share_mode(1); // FILE_SHARE_READ: deny ordinary write/delete sharing.
        }
        let mut file = options
            .open(self.path)
            .map_err(|_| ServiceFailure::InputUnavailable)?;
        let metadata = file
            .metadata()
            .map_err(|_| ServiceFailure::InputUnavailable)?;
        if !metadata.is_file() {
            return Err(ServiceFailure::InputUnavailable);
        }
        if metadata.len() > MAX_SNAPSHOT_BYTES as u64 {
            return Err(ServiceFailure::ResourceLimitExceeded);
        }
        Snapshot::from_reader(&mut file)
    }
}
