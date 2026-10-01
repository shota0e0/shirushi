#[cfg(all(feature = "manual-canary", not(debug_assertions)))]
compile_error!("the manual-canary feature is DEVELOPMENT CANARY only and must not be built without debug assertions");

#[cfg(test)]
mod asset_stage;
mod host;
// Parallel host-owned inspection boundary; not exposed as a Tauri command.
pub mod inspection_protocol;
pub mod inspection_supervisor;
// CI-only package identity Canary. No Production discovery or Tauri command.
#[cfg(all(windows, debug_assertions))]
pub mod inspection_package;
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
async fn bridge_get_capabilities(state: tauri::State<'_, BridgeRuntime>) -> Result<Value, BridgeError> {
    let runtime = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || runtime.call(GET_CAPABILITIES))
        .await
        .map_err(|_| BridgeError::new("BRIDGE_TASK_FAILED", "native bridge task did not complete"))?
}

#[tauri::command]
async fn bridge_load_personal_mark(state: tauri::State<'_, BridgeRuntime>) -> Result<Value, BridgeError> {
    let runtime = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || runtime.call(LOAD_PERSONAL_MARK))
        .await
        .map_err(|_| BridgeError::new("BRIDGE_TASK_FAILED", "native bridge task did not complete"))?
}

pub fn run() {
    let webview_data_directory = match host::prepare_process_environment() {
        Ok(path) => path,
        Err(error) => {
            eprintln!("Shirushi DEVELOPMENT CANARY preparation failed: {}", error.code);
            #[cfg(feature = "manual-canary")]
            std::process::exit(2);
            #[cfg(not(feature = "manual-canary"))]
            return;
        }
    };
    let runtime = BridgeRuntime::start();
    tauri::Builder::default()
        .manage(runtime)
        .setup(move |app| {
            let window = WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::App("index.html".into()));
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
            bridge_load_personal_mark
        ])
        .build(tauri::generate_context!())
        .expect("failed to build Shirushi desktop canary")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit) {
                app.state::<BridgeRuntime>().shutdown();
            }
        });
}

fn is_local_app_url(url: &tauri::Url) -> bool {
    let exact_authority = url.username().is_empty() && url.password().is_none() && url.port().is_none();
    exact_authority
        && ((url.scheme() == "tauri" && url.host_str() == Some("localhost"))
            || (url.scheme() == "http" && url.host_str() == Some("tauri.localhost")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn navigation_is_local_only() {
        assert!(is_local_app_url(&"http://tauri.localhost/index.html".parse().unwrap()));
        assert!(is_local_app_url(&"tauri://localhost/index.html".parse().unwrap()));
        assert!(!is_local_app_url(&"https://example.com/".parse().unwrap()));
        assert!(!is_local_app_url(&"https://tauri.localhost/index.html".parse().unwrap()));
        assert!(!is_local_app_url(&"http://tauri.localhost:4444/index.html".parse().unwrap()));
        assert!(!is_local_app_url(&"http://user@tauri.localhost/index.html".parse().unwrap()));
        assert!(!is_local_app_url(&"http://tauri.localhost.example/index.html".parse().unwrap()));
        assert!(!is_local_app_url(&"http://evil-tauri.localhost/index.html".parse().unwrap()));
        assert!(!is_local_app_url(&"file:///C:/secret.txt".parse().unwrap()));
        assert!(!is_local_app_url(&"javascript:alert(1)".parse().unwrap()));
    }
}
