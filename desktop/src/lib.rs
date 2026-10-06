#[cfg(all(feature = "manual-canary", not(debug_assertions)))]
compile_error!("the manual-canary feature is DEVELOPMENT CANARY only and must not be built without debug assertions");

#[cfg(test)]
mod asset_stage;
mod explorer_entry;
mod host;
// Host-owned inspection boundary; development command reuses this supervisor.
pub mod inspection_protocol;
pub mod inspection_supervisor;
// Development/CI package identity Canary. No Production helper discovery.
#[cfg(all(windows, debug_assertions))]
pub mod inspection_package;
pub mod limited_inspection;
#[path = "../../tools/f3a-rust-sdk-parity/src/product_contract.rs"]
mod product_contract;
mod product_image;
#[cfg(all(windows, debug_assertions))]
mod product_operation;
mod protocol;

use host::BridgeHost;
use protocol::{BridgeError, GET_CAPABILITIES, LOAD_PERSONAL_MARK};
use serde_json::Value;
use std::sync::Arc;
use tauri::webview::{NewWindowResponse, WebviewWindowBuilder};
use tauri::Manager;

#[derive(Clone)]
struct BridgeRuntime(Arc<BridgeRuntimeInner>);

enum BridgeRuntimeInner {
    Ready(BridgeHost),
    Failed(BridgeError),
}

impl BridgeRuntime {
    fn start() -> Self {
        Self(Arc::new(match BridgeHost::start() {
            Ok(host) => BridgeRuntimeInner::Ready(host),
            Err(error) => BridgeRuntimeInner::Failed(error),
        }))
    }

    fn call(&self, method: &'static str) -> Result<Value, BridgeError> {
        match self.0.as_ref() {
            BridgeRuntimeInner::Ready(host) => {
                if let Some(error) = host.health_error() {
                    Err(error)
                } else {
                    host.request(method)
                }
            }
            BridgeRuntimeInner::Failed(error) => Err(error.clone()),
        }
    }

    fn shutdown(&self) {
        if let BridgeRuntimeInner::Ready(host) = self.0.as_ref() {
            host.shutdown();
        }
    }
}

#[tauri::command]
async fn bridge_get_capabilities(
    state: tauri::State<'_, BridgeRuntime>,
) -> Result<Value, BridgeError> {
    let runtime = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || runtime.call(GET_CAPABILITIES))
        .await
        .map_err(|_| {
            BridgeError::new("BRIDGE_TASK_FAILED", "native bridge task did not complete")
        })?
}

#[tauri::command]
async fn bridge_load_personal_mark(
    state: tauri::State<'_, BridgeRuntime>,
) -> Result<Value, BridgeError> {
    let runtime = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || runtime.call(LOAD_PERSONAL_MARK))
        .await
        .map_err(|_| {
            BridgeError::new("BRIDGE_TASK_FAILED", "native bridge task did not complete")
        })?
}

#[tauri::command]
async fn bridge_inspect_limited(
    state: tauri::State<'_, limited_inspection::LimitedInspectionRuntime>,
    request: limited_inspection::InspectionRequest,
) -> Result<Value, BridgeError> {
    // Reserve before spawning; a dropped IPC future does not release the gate
    // while its blocking worker is still running or cleaning up the child.
    let invocation = state.reserve(request)?;
    tauri::async_runtime::spawn_blocking(move || invocation.run())
        .await
        .map_err(|_| BridgeError::new("INSPECTION_TASK_FAILED", "inspection task did not complete"))
}

#[tauri::command]
async fn bridge_select_image() -> Result<Option<Value>, BridgeError> {
    tauri::async_runtime::spawn_blocking(product_image::choose)
        .await
        .map_err(|_| BridgeError::new("IMAGE_SELECTION_FAILED", "image picker did not complete"))?
}

#[tauri::command]
async fn bridge_read_image(input_path: String) -> Result<Value, BridgeError> {
    tauri::async_runtime::spawn_blocking(move || product_image::read(&input_path))
        .await
        .map_err(|_| BridgeError::new("IMAGE_READ_FAILED", "image preview did not complete"))?
}

#[tauri::command]
async fn bridge_take_explorer_request(
    state: tauri::State<'_, explorer_entry::ExplorerEntry>,
) -> Result<Option<Value>, BridgeError> {
    let entry = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || entry.take())
        .await
        .map_err(|_| {
            BridgeError::new("EXPLORER_TASK_FAILED", "Explorer handoff did not complete")
        })?
}

#[tauri::command]
async fn bridge_product_operation(
    state: tauri::State<'_, limited_inspection::LimitedInspectionRuntime>,
    request: ProductRequest,
) -> Result<Value, BridgeError> {
    let mark = match request.operation.as_str() {
        "add" => {
            let mark = request
                .personal_mark
                .ok_or_else(|| BridgeError::new("INVALID_PERSONAL_MARK", "mark required"))?;
            product_contract::validate_personal_mark(&mark)
                .map_err(|_| BridgeError::new("INVALID_PERSONAL_MARK", "invalid mark"))?;
            Some(mark)
        }
        "limited_inspect" if request.personal_mark.is_none() => None,
        _ => {
            return Err(BridgeError::new(
                "PRODUCT_REQUEST_INVALID",
                "unknown operation",
            ))
        }
    };
    if !request.expected_source.valid() {
        return Err(BridgeError::new(
            "PRODUCT_REQUEST_INVALID",
            "invalid source identity",
        ));
    }
    let invocation = state.reserve(limited_inspection::InspectionRequest {
        operation: limited_inspection::OPERATION.into(),
        input_path: request.input_path,
    })?;
    tauri::async_runtime::spawn_blocking(move || {
        invocation.run_product(mark, request.expected_source)
    })
    .await
    .map_err(|_| BridgeError::new("PRODUCT_TASK_FAILED", "product operation did not complete"))
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ProductRequest {
    operation: String,
    input_path: String,
    personal_mark: Option<Value>,
    expected_source: ExpectedSource,
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ExpectedSource {
    sha256: String,
    size: u64,
}
impl ExpectedSource {
    fn valid(&self) -> bool {
        self.size > 0
            && self.size <= 64 * 1024 * 1024
            && self.sha256.len() == 64
            && self
                .sha256
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    }
}

pub fn run() {
    let explorer = explorer_entry::ExplorerEntry::from_args(std::env::args_os().skip(1));
    let webview_data_directory = match host::prepare_process_environment() {
        Ok(path) => path,
        Err(error) => {
            eprintln!(
                "Shirushi DEVELOPMENT CANARY preparation failed: {}",
                error.code
            );
            #[cfg(feature = "manual-canary")]
            std::process::exit(2);
            #[cfg(not(feature = "manual-canary"))]
            return;
        }
    };
    let runtime = BridgeRuntime::start();
    tauri::Builder::default()
        .manage(runtime)
        .manage(explorer)
        .manage(limited_inspection::LimitedInspectionRuntime::default())
        .setup(move |app| {
            let window =
                WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::App("index.html".into()));
            #[cfg(feature = "manual-canary")]
            let window = window.data_directory(
                webview_data_directory
                    .clone()
                    .expect("manual canary WebView data directory was not prepared"),
            );
            #[cfg(not(feature = "manual-canary"))]
            let window = {
                let _ = &webview_data_directory;
                window
            };
            window
                .title("Shirushi")
                .inner_size(980.0, 760.0)
                .min_inner_size(720.0, 600.0)
                .on_navigation(is_local_app_url)
                .on_new_window(|_, _| NewWindowResponse::Deny)
                .build()?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            bridge_get_capabilities,
            bridge_load_personal_mark,
            bridge_inspect_limited,
            bridge_select_image,
            bridge_read_image,
            bridge_take_explorer_request,
            bridge_product_operation
        ])
        .build(tauri::generate_context!())
        .expect("failed to build Shirushi desktop canary")
        .run(|app, event| {
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                app.state::<BridgeRuntime>().shutdown();
                let result = app
                    .state::<limited_inspection::LimitedInspectionRuntime>()
                    .shutdown_and_wait(limited_inspection::SHUTDOWN_WAIT_BOUND);
                eprintln!(
                    "Shirushi development inspection shutdown: {}",
                    result.code()
                );
            }
        });
}

fn is_local_app_url(url: &tauri::Url) -> bool {
    let exact_authority =
        url.username().is_empty() && url.password().is_none() && url.port().is_none();
    exact_authority
        && ((url.scheme() == "tauri" && url.host_str() == Some("localhost"))
            || (url.scheme() == "http" && url.host_str() == Some("tauri.localhost")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn navigation_is_local_only() {
        assert!(is_local_app_url(
            &"http://tauri.localhost/index.html".parse().unwrap()
        ));
        assert!(is_local_app_url(
            &"tauri://localhost/index.html".parse().unwrap()
        ));
        assert!(!is_local_app_url(&"https://example.com/".parse().unwrap()));
        assert!(!is_local_app_url(
            &"https://tauri.localhost/index.html".parse().unwrap()
        ));
        assert!(!is_local_app_url(
            &"http://tauri.localhost:4444/index.html".parse().unwrap()
        ));
        assert!(!is_local_app_url(
            &"http://user@tauri.localhost/index.html".parse().unwrap()
        ));
        assert!(!is_local_app_url(
            &"http://tauri.localhost.example/index.html".parse().unwrap()
        ));
        assert!(!is_local_app_url(
            &"http://evil-tauri.localhost/index.html".parse().unwrap()
        ));
        assert!(!is_local_app_url(&"file:///C:/secret.txt".parse().unwrap()));
        assert!(!is_local_app_url(&"javascript:alert(1)".parse().unwrap()));
    }
}
