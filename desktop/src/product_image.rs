//! Development local image picker/readback. No upload, writer or helper lookup.
use crate::protocol::BridgeError;
use serde_json::Value;

pub fn choose() -> Result<Option<Value>, BridgeError> {
    #[cfg(all(windows, any(debug_assertions, feature = "preview-release")))]
    {
        use windows_sys::Win32::UI::Controls::Dialogs::{
            CommDlgExtendedError, GetOpenFileNameW, OFN_EXPLORER, OFN_FILEMUSTEXIST,
            OFN_NOCHANGEDIR, OFN_PATHMUSTEXIST, OPENFILENAMEW,
        };
        let mut path = vec![0u16; 4096];
        let filter: Vec<u16> = "PNG / JPEG\0*.png;*.jpg;*.jpeg\0\0"
            .encode_utf16()
            .collect();
        let mut dialog: OPENFILENAMEW = unsafe { std::mem::zeroed() };
        dialog.lStructSize = std::mem::size_of::<OPENFILENAMEW>() as u32;
        dialog.lpstrFilter = filter.as_ptr();
        dialog.lpstrFile = path.as_mut_ptr();
        dialog.nMaxFile = path.len() as u32;
        dialog.Flags = OFN_EXPLORER | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR;
        if unsafe { GetOpenFileNameW(&mut dialog) } == 0 {
            return if unsafe { CommDlgExtendedError() } == 0 {
                Ok(None)
            } else {
                Err(error("IMAGE_SELECTION_FAILED"))
            };
        }
        let len = path
            .iter()
            .position(|c| *c == 0)
            .ok_or_else(|| error("IMAGE_SELECTION_FAILED"))?;
        let path = String::from_utf16(&path[..len]).map_err(|_| error("IMAGE_SELECTION_FAILED"))?;
        read_with_bound(&path, 32 * 1024 * 1024).map(Some)
    }
    #[cfg(not(all(windows, any(debug_assertions, feature = "preview-release"))))]
    {
        Err(error("DEV_CANARY_ONLY"))
    }
}

fn error(code: &'static str) -> BridgeError {
    BridgeError::new(code, "local development image unavailable")
}

pub fn read(input: &str) -> Result<Value, BridgeError> {
    read_with_bound(input, 64 * 1024 * 1024)
}

fn read_with_bound(input: &str, maximum: u64) -> Result<Value, BridgeError> {
    #[cfg(all(windows, any(debug_assertions, feature = "preview-release")))]
    {
        use base64::{engine::general_purpose::STANDARD, Engine};
        use sha2::{Digest, Sha256};
        use std::{io::Read, path::Path};
        if input.len() > 4096 || input.contains('\0') {
            return Err(error("INPUT_UNAVAILABLE"));
        }
        let path = Path::new(input);
        // Pin all local ancestors and source against ordinary write/delete;
        // same guard used by the existing helper dispatch.
        let _guard =
            crate::limited_inspection::pin_input(path).map_err(|_| error("INPUT_UNAVAILABLE"))?;
        let mut bytes = Vec::new();
        std::fs::File::open(path)
            .map_err(|_| error("INPUT_UNAVAILABLE"))?
            .take(maximum + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| error("INPUT_UNAVAILABLE"))?;
        if bytes.len() as u64 > maximum {
            return Err(error("RESOURCE_LIMIT_EXCEEDED"));
        }
        let mime = if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
            "image/png"
        } else if bytes.starts_with(b"\xff\xd8\xff") {
            "image/jpeg"
        } else {
            return Err(error("UNSUPPORTED_FORMAT"));
        };
        // Browser decode must also succeed before the record becomes selected.
        Ok(
            serde_json::json!({"url":format!("data:{mime};base64,{}", STANDARD.encode(&bytes)),
            "name":path.file_name().and_then(|n|n.to_str()).ok_or_else(||error("INPUT_UNAVAILABLE"))?,
            "reference":input,"local":true,"sha256":format!("{:x}",Sha256::digest(&bytes)),"size":bytes.len()}),
        )
    }
    #[cfg(not(all(windows, any(debug_assertions, feature = "preview-release"))))]
    {
        let _ = (input, maximum);
        Err(error("DEV_CANARY_ONLY"))
    }
}
