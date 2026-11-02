use std::{fs::{self, OpenOptions}, io::{Read, Write}, net::IpAddr,
          os::windows::fs::{MetadataExt, OpenOptionsExt}, path::PathBuf, sync::Mutex};
use serde::{Deserialize, Serialize};

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ModelConfiguration {
    pub provider: String,
    pub model: String,
    pub endpoint: Option<String>,
    #[serde(default)]
    pub credential_reference: Option<String>,
    #[serde(default)]
    pub voice_enabled: bool,
    #[serde(default)]
    pub voice_endpoint: Option<String>,
}

impl Default for ModelConfiguration {
    fn default() -> Self {
        Self { provider: "mock".into(), model: "jarvis-dev".into(), endpoint: None, credential_reference: None, voice_enabled: false, voice_endpoint: None }
    }
}

pub fn data_directory() -> Result<PathBuf, String> {
    let shell = std::env::current_exe().map_err(|_| "Cannot locate desktop executable.")?;
    if std::env::args().any(|arg| arg == "--portable") {
        Ok(shell.parent().ok_or("Cannot locate desktop directory.")?.join("data"))
    } else {
        Ok(PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or("Local application data is unavailable.")?).join("USMANJARVIS"))
    }
}

fn validate(value: &ModelConfiguration) -> Result<(), String> {
    if value.voice_enabled {
        let endpoint = value.voice_endpoint.as_ref().ok_or("An explicit local voice endpoint is required.")?;
        let url = tauri::Url::parse(endpoint).map_err(|_| "Invalid local voice endpoint.")?;
        let local = url.host_str().and_then(|host| host.parse::<IpAddr>().ok()).map(|host| host.is_loopback()).unwrap_or(false);
        if endpoint.len() > 500 || !local || url.scheme() != "http" || !url.username().is_empty()
            || url.password().is_some() || url.query().is_some() || url.fragment().is_some() {
            return Err("Voice requires numeric HTTP loopback without credentials or query parameters.".into());
        }
    }
    if value.model.is_empty() || value.model.len() > 200
        || !value.model.bytes().all(|c| c.is_ascii_alphanumeric() || b":/._-".contains(&c)) {
        return Err("Choose a bounded model identifier without credentials.".into());
    }
    if let Some(reference) = &value.credential_reference {
        if !reference.starts_with("vault:") || reference.len() != 38
            || !reference[6..].bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) {
            return Err("Use a protected backend credential reference.".into());
        }
        if matches!(value.provider.as_str(), "mock" | "ollama") {
            return Err("Local providers do not accept cloud credentials.".into());
        }
    }
    match value.provider.as_str() {
        "mock" if value.endpoint.is_none() => Ok(()),
        "ollama" => {
            let endpoint = value.endpoint.as_ref().ok_or("An explicit local endpoint is required.")?;
            let url = tauri::Url::parse(endpoint).map_err(|_| "Invalid local model endpoint.")?;
            let local = url.host_str().and_then(|host| host.parse::<IpAddr>().ok())
                .map(|host| host.is_loopback()).unwrap_or(false);
            if endpoint.len() > 500 || !local || url.scheme() != "http" || !url.username().is_empty()
                || url.password().is_some() || url.query().is_some() || url.fragment().is_some() {
                return Err("Use a numeric HTTP loopback endpoint without credentials or query parameters.".into());
            }
            Ok(())
        }
        "gemini" | "anthropic" | "openai_compatible" => {
            let endpoint = value.endpoint.as_ref().ok_or("An explicit cloud endpoint is required.")?;
            let url = tauri::Url::parse(endpoint).map_err(|_| "Invalid cloud endpoint.")?;
            if endpoint.len() > 500 || url.scheme() != "https" || url.host_str().is_none()
                || !url.username().is_empty() || url.password().is_some() || url.query().is_some() || url.fragment().is_some() {
                return Err("Cloud endpoints require HTTPS without credentials or query parameters.".into());
            }
            let expected = match value.provider.as_str() {
                "gemini" => Some("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
                "anthropic" => Some("https://api.anthropic.com/v1/messages"),
                _ => None,
            };
            if expected.map(|target| endpoint.trim_end_matches('/') != target).unwrap_or(false) {
                return Err("Named providers require their exact official endpoint.".into());
            }
            Ok(())
        }
        _ => Err("Unsupported desktop model provider.".into()),
    }
}

static SETTINGS_LOCK: Mutex<()> = Mutex::new(());

pub fn setup_status() -> Result<serde_json::Value, String> {
    let directory = data_directory()?;
    let marker = directory.join("setup-v1.complete");
    let completed = match OpenOptions::new().read(true).custom_flags(0x00200000).share_mode(1).open(marker) {
        Ok(file) => {
            let metadata = file.metadata().map_err(|_| "Cannot inspect setup state.")?;
            if metadata.file_attributes() & 0x400 != 0 || metadata.len() > 64 { return Err("Invalid setup state.".into()); }
            let mut bytes = Vec::new();
            file.take(65).read_to_end(&mut bytes).map_err(|_| "Cannot read setup state.")?;
            if bytes != b"setup-v1\n" { return Err("Invalid setup state.".into()); }
            true
        },
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => false,
        Err(_) => return Err("Cannot read setup state.".into()),
    };
    let existing = directory.join("desktop-model.json").exists()
        || generations(&directory)?.iter().any(|entry| entry.1);
    Ok(serde_json::json!({"completed": completed, "existing_configuration": existing}))
}

pub fn finish_setup() -> Result<(), String> {
    let _lock = SETTINGS_LOCK.lock().map_err(|_| "Setup writer is unavailable.")?;
    if setup_status()?["completed"] == true { return Ok(()); }
    let directory = data_directory()?;
    fs::create_dir_all(&directory).map_err(|_| "Cannot create setup state directory.")?;
    let mut file = OpenOptions::new().write(true).create_new(true).share_mode(1)
        .open(directory.join("setup-v1.complete")).map_err(|_| "Cannot save setup state.")?;
    file.write_all(b"setup-v1\n").and_then(|_| file.sync_all()).map_err(|_| "Cannot finish setup state.".into())
}

fn read_configuration(path: PathBuf) -> Result<ModelConfiguration, String> {
    let mut file = match OpenOptions::new().read(true).custom_flags(0x00200000).share_mode(1).open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(ModelConfiguration::default()),
        Err(_) => return Err("Cannot read local model settings.".into()),
    };
    let metadata = file.metadata().map_err(|_| "Cannot inspect local model settings.")?;
    if metadata.len() > 4096 || metadata.file_attributes() & 0x400 != 0 {
        return Err("Local model settings are linked or oversized.".into());
    }
    let mut bytes = Vec::new();
    Read::by_ref(&mut file).take(4097).read_to_end(&mut bytes).map_err(|_| "Cannot read bounded model settings.")?;
    let value: ModelConfiguration = serde_json::from_slice(&bytes).map_err(|_| "Invalid local model settings.")?;
    validate(&value)?;
    Ok(value)
}

pub fn save(value: &ModelConfiguration) -> Result<(), String> {
    let _lock = SETTINGS_LOCK.lock().map_err(|_| "Local settings writer is unavailable.")?;
    validate(value)?;
    let directory = data_directory()?;
    fs::create_dir_all(&directory).map_err(|_| "Cannot create local settings directory.")?;
    let generations = generations(&directory)?;
    let next = generations.iter().map(|value| value.0).max().unwrap_or(0).checked_add(1)
        .ok_or("Local settings generation limit exceeded.")?;
    let bytes = serde_json::to_vec(value).map_err(|_| "Cannot serialize model configuration.")?;
    // Append an immutable generation. No prior settings file is truncated,
    // renamed, replaced or deleted, including an incomplete prior save.
    let mut file = OpenOptions::new().write(true).create_new(true).share_mode(1)
        .open(directory.join(format!("desktop-model-{next:020}.json")))
        .map_err(|_| "Cannot create a fresh settings generation.")?;
    file.write_all(&bytes).and_then(|_| file.sync_all()).map_err(|_| "Cannot persist model settings.")?;
    drop(file);
    let mut marker = OpenOptions::new().write(true).create_new(true).share_mode(1)
        .open(directory.join(format!("desktop-model-{next:020}.commit")))
        .map_err(|_| "Cannot commit settings generation.")?;
    marker.write_all(b"committed-v1\n").and_then(|_| marker.sync_all()).map_err(|_| "Cannot finish settings commit.")?;
    Ok(())
}

fn generations(directory: &PathBuf) -> Result<Vec<(u64, bool)>, String> {
    let entries = match fs::read_dir(directory) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(_) => return Err("Cannot inspect local settings history.".into()),
    };
    let mut result = Vec::new();
    for (count, entry) in entries.enumerate() {
        if count >= 4096 { return Err("Local data directory exceeds bounded settings scan capacity.".into()); }
        let entry = entry.map_err(|_| "Cannot inspect settings entry.")?;
        let name = entry.file_name().to_string_lossy().into_owned();
        let Some(suffix) = name.strip_prefix("desktop-model-") else { continue };
        let number = suffix.strip_suffix(".json").or_else(|| suffix.strip_suffix(".commit"));
        let Some(number) = number else { continue };
        if number.len() != 20 || !number.bytes().all(|value| value.is_ascii_digit()) { continue }
        let Ok(number) = number.parse::<u64>() else { continue };
        let mut committed = false;
        if suffix.ends_with(".commit") {
            if let Ok(file) = OpenOptions::new().read(true).custom_flags(0x00200000).share_mode(1).open(entry.path()) {
                if let Ok(metadata) = file.metadata() {
                    if metadata.file_attributes() & 0x400 == 0 && metadata.len() <= 32 {
                        let mut marker = Vec::new();
                        if file.take(33).read_to_end(&mut marker).is_ok() && marker == b"committed-v1\n" {
                            committed = true;
                        }
                    }
                }
            }
        }
        result.push((number, committed));
    }
    Ok(result)
}

pub fn load() -> Result<ModelConfiguration, String> {
    let _lock = SETTINGS_LOCK.lock().map_err(|_| "Local settings reader is unavailable.")?;
    let directory = data_directory()?;
    let latest = generations(&directory)?.into_iter().filter(|value| value.1).map(|value| value.0).max();
    if let Some(generation) = latest {
        let path = directory.join(format!("desktop-model-{generation:020}.json"));
        if !path.exists() { return Err("Committed settings are missing. Use explicit --reset-settings recovery.".into()); }
        read_configuration(path)
    } else {
        // Backward-compatible read; future saves leave the legacy file intact.
        read_configuration(directory.join("desktop-model.json"))
    }
}
