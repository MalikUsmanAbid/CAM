#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod owned_backend;
mod model_configuration;
mod startup_recovery;

use std::{io::{BufRead, BufReader, Read, Write},
          sync::{mpsc, Mutex}, time::Duration};
use serde::{Deserialize, Serialize};
use tauri::{Manager, State};
use owned_backend::OwnedBackend;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Session {
    version: u8,
    event: String,
    endpoint: String,
    token: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    request_id: Option<String>,
}

struct BackendState {
    process: Mutex<Option<OwnedBackend>>,
    session: Mutex<Session>,
    responses: Mutex<mpsc::Receiver<Result<Session, &'static str>>>,
}

#[tauri::command]
fn desktop_session(window: tauri::WebviewWindow, state: State<'_, BackendState>) -> Result<Session, String> {
    ensure_dashboard(&window)?;
    Ok(state.session.lock().map_err(|_| "Desktop session is unavailable.")?.clone())
}

#[tauri::command]
fn renew_desktop_session(window: tauri::WebviewWindow, state: State<'_, BackendState>) -> Result<Session, String> {
    ensure_dashboard(&window)?;
    let request_id = format!("{:032x}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_err(|_| "Cannot issue renewal correlation ID.")?.as_nanos());
    let mut process = state.process.lock().map_err(|_| "Backend supervisor is unavailable.")?;
    let process = process.as_mut().ok_or("Backend is stopped.")?;
    let request = serde_json::json!({"version": 1, "action": "renew_session", "request_id": request_id});
    process.input.write_all(format!("{}\n", request).as_bytes()).and_then(|_| process.input.flush())
        .map_err(|_| "Cannot request private session renewal.")?;
    let receiver = state.responses.lock().map_err(|_| "Private response channel is unavailable.")?;
    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    loop {
        let remaining = deadline.checked_duration_since(std::time::Instant::now()).ok_or("Private renewal deadline exceeded.")?;
        let renewed = receiver.recv_timeout(remaining).map_err(|_| "Private renewal response was not received.")??;
        if renewed.request_id.as_deref() != Some(request_id.as_str()) { continue }
        let mut current = state.session.lock().map_err(|_| "Desktop session is unavailable.")?;
        if renewed.version != 1 || renewed.event != "session_renewed" || renewed.endpoint != current.endpoint
            || renewed.token.len() < 32 || renewed.token.len() > 128
            || !renewed.token.bytes().all(|value| value.is_ascii_alphanumeric() || value == b'-' || value == b'_') {
            return Err("Private renewal contract rejected.".into());
        }
        *current = renewed.clone();
        return Ok(renewed);
    }
}

fn ensure_dashboard(window: &tauri::WebviewWindow) -> Result<(), String> {
    let url = window.url().map_err(|_| "Cannot verify dashboard location.")?;
    if window.label() != "main" || !matches!(url.as_str(), "http://tauri.localhost/" | "tauri://localhost/") {
        return Err("Desktop bootstrap is restricted to the packaged main dashboard.".into());
    }
    Ok(())
}

#[tauri::command]
fn open_oauth_url(window: tauri::WebviewWindow, app: tauri::AppHandle, state: State<'_, BackendState>, url: String) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    ensure_dashboard(&window)?;
    if url.len() > 6000 { return Err("Authorization URL exceeds its bound.".into()); }
    let parsed = tauri::Url::parse(&url).map_err(|_| "Invalid authorization URL.")?;
    let provider = matches!((parsed.host_str(), parsed.path()),
        (Some("accounts.google.com"), "/o/oauth2/v2/auth") |
        (Some("login.microsoftonline.com"), "/common/oauth2/v2.0/authorize"));
    if !provider || parsed.scheme() != "https" || !parsed.username().is_empty()
        || parsed.password().is_some() || parsed.fragment().is_some() || parsed.port_or_known_default() != Some(443) {
        return Err("Only supported provider authorization pages may open.".into());
    }
    let mut query = std::collections::BTreeMap::new();
    for (key, value) in parsed.query_pairs() {
        if query.insert(key.into_owned(), value.into_owned()).is_some() {
            return Err("Duplicate authorization parameters are unsupported.".into());
        }
    }
    let expected = format!("{}/api/v1/oauth/callback", state.session.lock().map_err(|_| "Desktop session is unavailable.")?.endpoint);
    if query.get("redirect_uri") != Some(&expected)
        || query.get("response_type").map(String::as_str) != Some("code")
        || query.get("code_challenge_method").map(String::as_str) != Some("S256")
        || !query.get("code_challenge").map(|value| value.len() == 43 && value.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')).unwrap_or(false)
        || !query.get("state").map(|value| value.len() >= 32 && value.len() <= 128 && value.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')).unwrap_or(false) {
        return Err("Authorization URL must bind the active backend callback and PKCE state.".into());
    }
    app.opener().open_url(url, None::<&str>).map_err(|_| "Cannot open the provider in the system browser.".into())
}

#[tauri::command]
fn local_model_configuration(window: tauri::WebviewWindow) -> Result<model_configuration::ModelConfiguration, String> {
    ensure_dashboard(&window)?;
    model_configuration::load()
}

#[tauri::command]
fn save_local_model_configuration(window: tauri::WebviewWindow, configuration: model_configuration::ModelConfiguration) -> Result<(), String> {
    ensure_dashboard(&window)?;
    model_configuration::save(&configuration)
}

#[tauri::command]
fn desktop_setup_status(window: tauri::WebviewWindow) -> Result<serde_json::Value, String> {
    ensure_dashboard(&window)?;
    model_configuration::setup_status()
}

#[tauri::command]
fn open_model_key_page(window: tauri::WebviewWindow, app: tauri::AppHandle, provider: String) -> Result<(), String> {
    ensure_dashboard(&window)?;
    let url = match provider.as_str() {
        "gemini" => "https://aistudio.google.com/apikey",
        "anthropic" => "https://platform.claude.com/settings/keys",
        "nvidia" => "https://build.nvidia.com/settings",
        _ => return Err("Unsupported key provider.".into()),
    };
    app.opener().open_url(url, None::<&str>).map_err(|_| "Cannot open the provider key page.".into())
}

#[tauri::command]
fn finish_desktop_setup(window: tauri::WebviewWindow) -> Result<(), String> {
    ensure_dashboard(&window)?;
    model_configuration::finish_setup()
}

fn start_backend() -> Result<BackendState, String> {
    let shell = std::env::current_exe().map_err(|_| "Cannot locate desktop executable.")?;
    let directory = shell.parent().ok_or("Cannot locate desktop directory.")?;
    let executable = directory.join("jarvis-backend.exe");
    let data = model_configuration::data_directory()?;
    if std::env::args().any(|argument| argument == "--reset-settings") {
        model_configuration::save(&model_configuration::ModelConfiguration::default())?;
    }
    let model_configuration = model_configuration::load()?;
    let workspace = data.join("workspace");
    let configuration = serde_json::json!({"version": 1, "action": "configure",
        "data_directory": data, "workspace_directory": workspace,
        "provider": model_configuration.provider, "model": model_configuration.model,
        "endpoint": model_configuration.endpoint, "credential_reference": model_configuration.credential_reference, "voice_enabled": model_configuration.voice_enabled,
        "voice_endpoint": model_configuration.voice_endpoint});
    let (mut process, output) = OwnedBackend::launch(&executable)?;
    process.input.write_all(format!("{}\n", configuration).as_bytes())
        .map_err(|_| "Cannot configure private backend channel.")?;
    process.input.flush().map_err(|_| "Cannot flush private backend channel.")?;
    let (send, receive) = mpsc::sync_channel(2);
    std::thread::spawn(move || {
      let mut reader = BufReader::new(output);
      loop {
        let mut bytes = Vec::new();
        let result = Read::by_ref(&mut reader).take(8193).read_until(b'\n', &mut bytes)
            .map_err(|_| "Cannot read private backend channel.")
            .and_then(|_| {
                if bytes.len() > 8192 || !bytes.ends_with(b"\n") {
                    return Err("Invalid private backend response.");
                }
                serde_json::from_slice::<Session>(&bytes).map_err(|_| "Invalid backend session response.")
            });
        let failed = result.is_err();
        if send.send(result).is_err() || failed { break }
      }
    });
    let session = receive.recv_timeout(Duration::from_secs(60))
        .map_err(|_| "Backend startup deadline exceeded.")??;
    let port = session.endpoint.strip_prefix("http://127.0.0.1:")
        .and_then(|value| value.parse::<u16>().ok()).filter(|value| *value > 0);
    if session.version != 1 || session.event != "ready" || port.is_none()
        || session.token.len() < 32 || session.token.len() > 128
        || !session.token.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_') {
        return Err("Backend session contract rejected.".into());
    }
    Ok(BackendState { process: Mutex::new(Some(process)), session: Mutex::new(session), responses: Mutex::new(receive) })
}

fn main() {
    let application = tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .on_window_event(|window, event| {
            if window.label() == "main" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    // Tray Exit owns shutdown. Keep the event loop and scoped
                    // reminder polling alive when the main window is hidden.
                    if window.hide().is_ok() { api.prevent_close(); }
                }
            }
        })
        .setup(|app| {
            // Configuration errors are returned without printing private pipe data.
            let state = start_backend().map_err(std::io::Error::other)?;
            app.manage(state);
            // Create only after binding the WebView profile to this app's data
            // mode. Portable launches must not write the global WebView cache.
            let config = app.config().app.windows.first()
                .ok_or_else(|| std::io::Error::other("Main window configuration is missing."))?;
            let webview_data = model_configuration::data_directory()
                .map_err(std::io::Error::other)?.join("webview");
            tauri::WebviewWindowBuilder::from_config(app, config)?
                .data_directory(webview_data).build()?;
            let show = tauri::menu::MenuItem::with_id(app, "show", "Show CAM", true, None::<&str>)?;
            let quit = tauri::menu::MenuItem::with_id(app, "quit", "Exit CAM", true, None::<&str>)?;
            let menu = tauri::menu::Menu::with_items(app, &[&show, &quit])?;
            let mut pixels = vec![0u8; 16 * 16 * 4];
            for y in 0..16i32 {
                for x in 0..16i32 {
                    if (x - 8).pow(2) + (y - 8).pow(2) <= 49 {
                        let offset = (y as usize * 16 + x as usize) * 4;
                        pixels[offset..offset + 4].copy_from_slice(&[30, 210, 225, 255]);
                    }
                }
            }
            tauri::tray::TrayIconBuilder::new()
                .icon(tauri::image::Image::new_owned(pixels, 16, 16))
                .tooltip("CAM")
                .menu(&menu)
                .on_menu_event(|app, event| {
                    match event.id.as_ref() {
                        "show" => {
                            if let Some(window) = app.get_webview_window("main") {
                                let _ = window.show();
                                let _ = window.set_focus();
                            }
                        }
                        "quit" => app.exit(0),
                        _ => {}
                    }
                })
                .build(app)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![open_model_key_page, desktop_setup_status, finish_desktop_setup, desktop_session, renew_desktop_session, local_model_configuration, save_local_model_configuration, open_oauth_url])
        .build(tauri::generate_context!());
    let application = match application {
        Ok(application) => application,
        Err(_) => { startup_recovery::show_failure(); return; }
    };
    application.run(|app, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                if let Some(state) = app.try_state::<BackendState>() {
                    if let Ok(mut process) = state.process.lock() {
                        drop(process.take());
                    }
                }
            }
        });
}
