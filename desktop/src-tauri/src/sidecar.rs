use std::{
    env,
    error::Error,
    fmt,
    io::{BufRead, BufReader, Read, Write},
    net::TcpStream,
    path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio},
    sync::{mpsc, Mutex},
    thread,
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};
use tauri::{http, AppHandle, Manager};
use tauri_plugin_dialog::DialogExt;

#[cfg(debug_assertions)]
use std::path::Path;

const PROTOCOL_VERSION: u32 = 1;
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(15);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(15);
const MAX_BATCH_ASSETS: usize = 1_000;
const MAX_SOURCE_PATH_BYTES: usize = 32 * 1024;
pub const MEDIA_PROTOCOL: &str = "memesort-media";
#[cfg(not(debug_assertions))]
const SIDECAR_BINARY_NAME: &str = "memesort-sidecar-x86_64-pc-windows-msvc.exe";

#[derive(Debug)]
pub struct SidecarError(String);

impl SidecarError {
    fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for SidecarError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl Error for SidecarError {}

#[derive(Debug, Deserialize)]
struct Handshake {
    protocol_version: u32,
    origin: String,
    bootstrap_url: String,
    library_root: String,
}

pub struct SidecarSession {
    child: Child,
    stdin: ChildStdin,
    #[allow(dead_code)]
    origin: String,
    #[allow(dead_code)]
    session_cookie: String,
}

pub struct SidecarState(Mutex<Option<SidecarSession>>);

/// A source folder selected through the native dialog for this desktop session.
/// The WebView never supplies this path to an import command.
pub struct ImportSelection(Mutex<Option<PathBuf>>);

impl ImportSelection {
    pub fn new() -> Self {
        Self(Mutex::new(None))
    }

    fn replace(&self, path: Option<PathBuf>) -> Result<Option<String>, SidecarError> {
        let selected_path = path.as_ref().map(|path| path.display().to_string());
        let mut selection = self
            .0
            .lock()
            .map_err(|_| SidecarError::new("MemeSort import selection is unavailable."))?;
        *selection = path;
        Ok(selected_path)
    }

    fn selected_path(&self) -> Result<String, SidecarError> {
        self.0
            .lock()
            .map_err(|_| SidecarError::new("MemeSort import selection is unavailable."))?
            .as_ref()
            .map(|path| path.display().to_string())
            .ok_or_else(|| SidecarError::new("Choose a source folder before importing."))
    }
}

impl SidecarState {
    pub fn new(session: SidecarSession) -> Self {
        Self(Mutex::new(Some(session)))
    }
}

impl SidecarSession {
    pub fn start(app: &AppHandle) -> Result<Self, SidecarError> {
        let launch = SidecarLaunch::for_current_build(app)?;
        let mut child = launch.spawn()?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| SidecarError::new("Sidecar stdin was not available."))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| SidecarError::new("Sidecar stdout was not available."))?;

        // Sidecar stdout is a single protocol message, never a log stream.
        let (sender, receiver) = mpsc::sync_channel(1);
        thread::spawn(move || {
            let mut line = String::new();
            let result = BufReader::new(stdout).read_line(&mut line).map(|_| line);
            let _ = sender.send(result);
        });

        if let Some(stderr) = child.stderr.take() {
            // Keep a noisy or failing sidecar from blocking on its stderr pipe.
            // It intentionally does not forward diagnostic text to the WebView.
            thread::spawn(move || {
                let _ = std::io::copy(&mut BufReader::new(stderr), &mut std::io::sink());
            });
        }

        let line = match receiver.recv_timeout(HANDSHAKE_TIMEOUT) {
            Ok(Ok(line)) => line,
            Ok(Err(error)) => {
                terminate_child(&mut child);
                return Err(SidecarError::new(format!(
                    "Could not read the sidecar handshake: {error}"
                )));
            }
            Err(_) => {
                terminate_child(&mut child);
                return Err(SidecarError::new(
                    "Timed out waiting for the sidecar handshake.",
                ));
            }
        };

        let handshake = match parse_handshake(&line) {
            Ok(handshake) => handshake,
            Err(error) => {
                terminate_child(&mut child);
                return Err(error);
            }
        };
        let session_cookie = match consume_bootstrap(&handshake) {
            Ok(cookie) => cookie,
            Err(error) => {
                terminate_child(&mut child);
                return Err(error);
            }
        };

        Ok(Self {
            child,
            stdin,
            origin: handshake.origin,
            session_cookie,
        })
    }

    pub fn shutdown(&mut self) -> Result<(), SidecarError> {
        self.stdin
            .write_all(b"{\"command\":\"shutdown\"}\n")
            .and_then(|_| self.stdin.flush())
            .map_err(|error| SidecarError::new(format!("Could not stop sidecar: {error}")))?;

        let deadline = Instant::now() + SHUTDOWN_TIMEOUT;
        while Instant::now() < deadline {
            if self
                .child
                .try_wait()
                .map_err(|error| SidecarError::new(format!("Could not wait for sidecar: {error}")))?
                .is_some()
            {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(50));
        }

        terminate_child(&mut self.child);
        Err(SidecarError::new(
            "Sidecar did not stop before the shutdown deadline.",
        ))
    }

    fn app_state(&self) -> Result<serde_json::Value, SidecarError> {
        authenticated_get_json(&self.origin, &self.session_cookie, ApiRoute::State)
    }

    fn assets(&self) -> Result<serde_json::Value, SidecarError> {
        authenticated_get_json(&self.origin, &self.session_cookie, ApiRoute::Assets)
    }

    fn asset_detail(&self, asset_id: &str) -> Result<serde_json::Value, SidecarError> {
        authenticated_get_json(
            &self.origin,
            &self.session_cookie,
            ApiRoute::AssetDetail(validate_asset_id(asset_id)?),
        )
    }

    fn delete_asset(&self, asset_id: &str) -> Result<serde_json::Value, SidecarError> {
        authenticated_post_json(
            &self.origin,
            &self.session_cookie,
            MutationRoute::DeleteAsset,
            &AssetIdPayload {
                asset_id: validate_asset_id(asset_id)?,
            },
        )
    }

    fn remove_source_record(
        &self,
        asset_id: &str,
        source_path: &str,
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_post_json(
            &self.origin,
            &self.session_cookie,
            MutationRoute::RemoveSourceRecord,
            &RemoveSourceRecordPayload {
                asset_id: validate_asset_id(asset_id)?,
                source_path: validate_source_path(source_path)?,
            },
        )
    }

    fn batch_asset_action(
        &self,
        action: BatchAssetAction,
        asset_ids: &[String],
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_post_json(
            &self.origin,
            &self.session_cookie,
            MutationRoute::BatchAssetAction,
            &BatchAssetActionPayload {
                action: action.as_api_value(),
                asset_ids: validate_asset_ids(asset_ids)?,
            },
        )
    }

    fn start_import(
        &self,
        source_folder: String,
        start_indexing: bool,
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_post_json(
            &self.origin,
            &self.session_cookie,
            MutationRoute::StartImport,
            &StartImportPayload {
                path: source_folder,
                start_indexing,
            },
        )
    }

    fn pause_import(&self) -> Result<serde_json::Value, SidecarError> {
        authenticated_post_json(
            &self.origin,
            &self.session_cookie,
            MutationRoute::PauseImport,
            &EmptyPayload {},
        )
    }

    fn resume_import(&self) -> Result<serde_json::Value, SidecarError> {
        authenticated_post_json(
            &self.origin,
            &self.session_cookie,
            MutationRoute::ResumeImport,
            &EmptyPayload {},
        )
    }

    fn media_response(
        &self,
        request: &http::Request<Vec<u8>>,
    ) -> Result<SidecarHttpResponse, SidecarError> {
        authenticated_get_media(&self.origin, &self.session_cookie, request)
    }
}

enum ApiRoute {
    State,
    Assets,
    AssetDetail(String),
}

#[derive(Clone, Copy)]
enum BatchAssetAction {
    Delete,
    RebuildActiveIndex,
}

impl BatchAssetAction {
    fn parse(value: &str) -> Result<Self, SidecarError> {
        match value {
            "delete" => Ok(Self::Delete),
            "rebuild-active-index" => Ok(Self::RebuildActiveIndex),
            _ => Err(SidecarError::new("Invalid MemeSort batch Asset action.")),
        }
    }

    fn as_api_value(self) -> &'static str {
        match self {
            Self::Delete => "delete",
            Self::RebuildActiveIndex => "rebuild-active-index",
        }
    }
}

#[derive(Clone, Copy)]
enum MutationRoute {
    RemoveSourceRecord,
    DeleteAsset,
    BatchAssetAction,
    StartImport,
    PauseImport,
    ResumeImport,
}

impl MutationRoute {
    fn path(self) -> &'static str {
        match self {
            Self::RemoveSourceRecord => "/api/remove-source-record",
            Self::DeleteAsset => "/api/delete-asset",
            Self::BatchAssetAction => "/api/assets/batch-action",
            Self::StartImport => "/api/import/start",
            Self::PauseImport => "/api/import/pause",
            Self::ResumeImport => "/api/import/resume",
        }
    }
}

#[derive(Serialize)]
struct AssetIdPayload {
    asset_id: String,
}

#[derive(Serialize)]
struct RemoveSourceRecordPayload {
    asset_id: String,
    source_path: String,
}

#[derive(Serialize)]
struct BatchAssetActionPayload {
    action: &'static str,
    asset_ids: Vec<String>,
}

#[derive(Serialize)]
struct StartImportPayload {
    path: String,
    start_indexing: bool,
}

#[derive(Serialize)]
struct EmptyPayload {}

#[derive(Serialize)]
pub struct FolderSelection {
    selected_path: Option<String>,
}

impl ApiRoute {
    fn path(&self) -> String {
        match self {
            Self::State => "/api/state".to_owned(),
            Self::Assets => "/api/assets".to_owned(),
            Self::AssetDetail(asset_id) => format!("/api/asset-detail?asset_id={asset_id}"),
        }
    }
}

fn authenticated_get_json(
    origin: &str,
    session_cookie: &str,
    route: ApiRoute,
) -> Result<serde_json::Value, SidecarError> {
    let port = loopback_port(origin)?;
    let mut stream = TcpStream::connect(("127.0.0.1", port))
        .map_err(|error| SidecarError::new(format!("Could not connect to sidecar: {error}")))?;
    stream
        .set_read_timeout(Some(Duration::from_secs(15)))
        .map_err(|error| {
            SidecarError::new(format!("Could not configure sidecar request: {error}"))
        })?;
    stream
        .write_all(
            format!(
                "GET {} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nCookie: {session_cookie}\r\nAccept: application/json\r\nConnection: close\r\n\r\n",
                route.path(),
            )
            .as_bytes(),
        )
        .map_err(|error| SidecarError::new(format!("Could not request sidecar state: {error}")))?;

    let mut response = Vec::new();
    stream
        .read_to_end(&mut response)
        .map_err(|error| SidecarError::new(format!("Could not read sidecar state: {error}")))?;
    parse_json_response(&response)
}

fn authenticated_get_media(
    origin: &str,
    session_cookie: &str,
    request: &http::Request<Vec<u8>>,
) -> Result<SidecarHttpResponse, SidecarError> {
    let path = managed_media_path(request)?;
    authenticated_get(origin, session_cookie, &path, "*/*")
}

fn authenticated_post_json(
    origin: &str,
    session_cookie: &str,
    route: MutationRoute,
    payload: &impl Serialize,
) -> Result<serde_json::Value, SidecarError> {
    let body = serde_json::to_vec(payload)
        .map_err(|error| SidecarError::new(format!("Could not encode sidecar request: {error}")))?;
    let port = loopback_port(origin)?;
    let mut stream = TcpStream::connect(("127.0.0.1", port))
        .map_err(|error| SidecarError::new(format!("Could not connect to sidecar: {error}")))?;
    stream
        .set_read_timeout(Some(Duration::from_secs(15)))
        .map_err(|error| {
            SidecarError::new(format!("Could not configure sidecar request: {error}"))
        })?;
    stream
        .write_all(
            format!(
                "POST {} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nCookie: {session_cookie}\r\nAccept: application/json\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                route.path(),
                body.len(),
            )
            .as_bytes(),
        )
        .and_then(|_| stream.write_all(&body))
        .map_err(|error| SidecarError::new(format!("Could not request sidecar: {error}")))?;

    let mut response = Vec::new();
    stream
        .read_to_end(&mut response)
        .map_err(|error| SidecarError::new(format!("Could not read sidecar response: {error}")))?;
    parse_json_response(&response)
}

fn authenticated_get(
    origin: &str,
    session_cookie: &str,
    path: &str,
    accept: &str,
) -> Result<SidecarHttpResponse, SidecarError> {
    let port = loopback_port(origin)?;
    let mut stream = TcpStream::connect(("127.0.0.1", port))
        .map_err(|error| SidecarError::new(format!("Could not connect to sidecar: {error}")))?;
    stream
        .set_read_timeout(Some(Duration::from_secs(15)))
        .map_err(|error| {
            SidecarError::new(format!("Could not configure sidecar request: {error}"))
        })?;
    stream
        .write_all(
            format!(
                "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nCookie: {session_cookie}\r\nAccept: {accept}\r\nConnection: close\r\n\r\n",
            )
            .as_bytes(),
        )
        .map_err(|error| SidecarError::new(format!("Could not request sidecar: {error}")))?;

    let mut response = Vec::new();
    stream
        .read_to_end(&mut response)
        .map_err(|error| SidecarError::new(format!("Could not read sidecar response: {error}")))?;
    parse_http_response(&response)
}

pub fn shutdown_managed_sidecar(app: &AppHandle) {
    let Some(state) = app.try_state::<SidecarState>() else {
        return;
    };
    let Ok(mut session) = state.0.lock() else {
        return;
    };
    let Some(mut session) = session.take() else {
        return;
    };
    if let Err(error) = session.shutdown() {
        eprintln!("MemeSort sidecar shutdown failed: {error}");
    }
}

#[tauri::command]
pub fn get_app_state(app: AppHandle) -> Result<serde_json::Value, String> {
    let state = app
        .try_state::<SidecarState>()
        .ok_or_else(|| "MemeSort sidecar is not available.".to_owned())?;
    let session = state
        .0
        .lock()
        .map_err(|_| "MemeSort sidecar state is unavailable.".to_owned())?;
    let session = session
        .as_ref()
        .ok_or_else(|| "MemeSort sidecar has already stopped.".to_owned())?;
    session.app_state().map_err(|error| error.to_string())
}

#[tauri::command]
pub fn get_assets(app: AppHandle) -> Result<serde_json::Value, String> {
    with_sidecar_session(&app, |session| session.assets())
}

#[tauri::command]
pub fn get_asset_detail(app: AppHandle, asset_id: String) -> Result<serde_json::Value, String> {
    with_sidecar_session(&app, |session| session.asset_detail(&asset_id))
}

#[tauri::command]
pub fn delete_asset(app: AppHandle, asset_id: String) -> Result<serde_json::Value, String> {
    with_sidecar_session(&app, |session| session.delete_asset(&asset_id))
}

#[tauri::command]
pub fn remove_source_record(
    app: AppHandle,
    asset_id: String,
    source_path: String,
) -> Result<serde_json::Value, String> {
    with_sidecar_session(&app, |session| {
        session.remove_source_record(&asset_id, &source_path)
    })
}

#[tauri::command]
pub fn batch_asset_action(
    app: AppHandle,
    action: String,
    asset_ids: Vec<String>,
) -> Result<serde_json::Value, String> {
    let action = BatchAssetAction::parse(&action).map_err(|error| error.to_string())?;
    with_sidecar_session(&app, |session| {
        session.batch_asset_action(action, &asset_ids)
    })
}

#[tauri::command]
pub fn choose_import_folder(app: AppHandle) -> Result<FolderSelection, String> {
    let path = app
        .dialog()
        .file()
        .set_title("Choose a folder to import into MemeSort")
        .blocking_pick_folder();
    let path = path
        .map(|path| {
            path.into_path()
                .map_err(|error| SidecarError::new(error.to_string()))
        })
        .transpose()
        .map_err(|error| error.to_string())?;
    let selection = app
        .try_state::<ImportSelection>()
        .ok_or_else(|| "MemeSort import selection is unavailable.".to_owned())?;
    let selected_path = selection.replace(path).map_err(|error| error.to_string())?;
    Ok(FolderSelection { selected_path })
}

#[tauri::command]
pub fn start_import(app: AppHandle) -> Result<serde_json::Value, String> {
    start_selected_import(&app, false)
}

#[tauri::command]
pub fn start_import_and_index(app: AppHandle) -> Result<serde_json::Value, String> {
    start_selected_import(&app, true)
}

#[tauri::command]
pub fn pause_import(app: AppHandle) -> Result<serde_json::Value, String> {
    with_sidecar_session(&app, |session| session.pause_import())
}

#[tauri::command]
pub fn resume_import(app: AppHandle) -> Result<serde_json::Value, String> {
    with_sidecar_session(&app, |session| session.resume_import())
}

fn start_selected_import(
    app: &AppHandle,
    start_indexing: bool,
) -> Result<serde_json::Value, String> {
    let selection = app
        .try_state::<ImportSelection>()
        .ok_or_else(|| "MemeSort import selection is unavailable.".to_owned())?;
    let source_folder = selection
        .selected_path()
        .map_err(|error| error.to_string())?;
    with_sidecar_session(app, |session| {
        session.start_import(source_folder, start_indexing)
    })
}

fn with_sidecar_session<T>(
    app: &AppHandle,
    operation: impl FnOnce(&SidecarSession) -> Result<T, SidecarError>,
) -> Result<T, String> {
    let state = app
        .try_state::<SidecarState>()
        .ok_or_else(|| "MemeSort sidecar is not available.".to_owned())?;
    let session = state
        .0
        .lock()
        .map_err(|_| "MemeSort sidecar state is unavailable.".to_owned())?;
    let session = session
        .as_ref()
        .ok_or_else(|| "MemeSort sidecar has already stopped.".to_owned())?;
    operation(session).map_err(|error| error.to_string())
}

pub fn media_protocol_response(
    app: &AppHandle,
    request: http::Request<Vec<u8>>,
) -> http::Response<Vec<u8>> {
    let result = app
        .try_state::<SidecarState>()
        .ok_or_else(|| SidecarError::new("MemeSort sidecar is not available."))
        .and_then(|state| {
            let session = state
                .0
                .lock()
                .map_err(|_| SidecarError::new("MemeSort sidecar state is unavailable."))?;
            let session = session
                .as_ref()
                .ok_or_else(|| SidecarError::new("MemeSort sidecar has already stopped."))?;
            session.media_response(&request)
        });

    match result {
        Ok(response) => response.into_tauri_response(),
        Err(error) => media_error_response(error),
    }
}

struct SidecarLaunch {
    executable: PathBuf,
    arguments: Vec<String>,
    working_directory: PathBuf,
}

impl SidecarLaunch {
    fn for_current_build(_app: &AppHandle) -> Result<Self, SidecarError> {
        let library_root = default_library_root()?;
        let log_dir = library_root.join("logs");

        #[cfg(debug_assertions)]
        {
            let repository_root = repository_root()?;
            let python = env::var_os("MEMESORT_SIDECAR_PYTHON")
                .map(PathBuf::from)
                .unwrap_or_else(|| {
                    repository_root
                        .join(".venv")
                        .join("Scripts")
                        .join("python.exe")
                });
            if !python.is_file() {
                return Err(SidecarError::new(format!(
                    "MemeSort sidecar Python is missing at {}. Run uv sync first.",
                    python.display()
                )));
            }
            Ok(Self {
                executable: python,
                arguments: vec![
                    "-m".into(),
                    "memesort_worker.sidecar_entry".into(),
                    "--library-root".into(),
                    library_root.display().to_string(),
                    "--log-dir".into(),
                    log_dir.display().to_string(),
                ],
                working_directory: repository_root,
            })
        }

        #[cfg(not(debug_assertions))]
        {
            let resource_dir = _app.path().resource_dir().map_err(|error| {
                SidecarError::new(format!(
                    "Could not resolve the Tauri resource directory: {error}"
                ))
            })?;
            let executable = resource_dir.join(SIDECAR_BINARY_NAME);
            if !executable.is_file() {
                return Err(SidecarError::new(format!(
                    "MemeSort sidecar is missing at {}.",
                    executable.display()
                )));
            }
            Ok(Self {
                executable,
                arguments: vec![
                    "--library-root".into(),
                    library_root.display().to_string(),
                    "--log-dir".into(),
                    log_dir.display().to_string(),
                ],
                working_directory: resource_dir,
            })
        }
    }

    fn spawn(&self) -> Result<Child, SidecarError> {
        Command::new(&self.executable)
            .args(&self.arguments)
            .current_dir(&self.working_directory)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| SidecarError::new(format!("Could not start the sidecar: {error}")))
    }
}

#[cfg(debug_assertions)]
fn repository_root() -> Result<PathBuf, SidecarError> {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .map(Path::to_path_buf)
        .ok_or_else(|| SidecarError::new("Could not resolve the MemeSort repository root."))
}

fn default_library_root() -> Result<PathBuf, SidecarError> {
    if let Some(root) = env::var_os("MEMESORT_LIBRARY_ROOT") {
        return Ok(PathBuf::from(root));
    }
    let appdata = env::var_os("APPDATA").ok_or_else(|| {
        SidecarError::new("APPDATA is required to resolve the MemeSort Library Root.")
    })?;
    Ok(PathBuf::from(appdata).join("MemeSort"))
}

fn parse_handshake(line: &str) -> Result<Handshake, SidecarError> {
    let handshake: Handshake = serde_json::from_str(line)
        .map_err(|error| SidecarError::new(format!("Invalid sidecar handshake: {error}")))?;
    if handshake.protocol_version != PROTOCOL_VERSION {
        return Err(SidecarError::new(format!(
            "Unsupported sidecar protocol version {}.",
            handshake.protocol_version
        )));
    }
    let origin_host = handshake
        .origin
        .strip_prefix("http://127.0.0.1:")
        .ok_or_else(|| SidecarError::new("Sidecar origin must be a 127.0.0.1 HTTP endpoint."))?;
    if origin_host.parse::<u16>().is_err()
        || !handshake.bootstrap_url.starts_with(&handshake.origin)
    {
        return Err(SidecarError::new(
            "Invalid sidecar origin or bootstrap URL.",
        ));
    }
    if !handshake.bootstrap_url.contains("?bootstrap=") || handshake.library_root.is_empty() {
        return Err(SidecarError::new("Incomplete sidecar handshake."));
    }
    Ok(handshake)
}

fn consume_bootstrap(handshake: &Handshake) -> Result<String, SidecarError> {
    let port = loopback_port(&handshake.origin)?;
    let path = handshake
        .bootstrap_url
        .strip_prefix(&handshake.origin)
        .filter(|path| path.starts_with('/'))
        .ok_or_else(|| SidecarError::new("Bootstrap URL does not belong to the sidecar origin."))?;

    let mut stream = TcpStream::connect(("127.0.0.1", port))
        .map_err(|error| SidecarError::new(format!("Could not connect to sidecar: {error}")))?;
    stream
        .write_all(
            format!("GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n")
                .as_bytes(),
        )
        .map_err(|error| SidecarError::new(format!("Could not bootstrap sidecar: {error}")))?;

    let mut response = String::new();
    stream.read_to_string(&mut response).map_err(|error| {
        SidecarError::new(format!("Could not read bootstrap response: {error}"))
    })?;
    let (headers, _) = response
        .split_once("\r\n\r\n")
        .ok_or_else(|| SidecarError::new("Malformed sidecar bootstrap response."))?;
    let mut lines = headers.lines();
    let status = lines
        .next()
        .ok_or_else(|| SidecarError::new("Missing sidecar bootstrap status."))?;
    if !status.contains(" 303 ") {
        return Err(SidecarError::new("Sidecar bootstrap was not accepted."));
    }
    lines
        .find_map(|line| {
            let (name, value) = line.split_once(':')?;
            if !name.eq_ignore_ascii_case("set-cookie") {
                return None;
            }
            value.trim().split(';').next().map(str::to_owned)
        })
        .filter(|cookie| cookie.starts_with("memesort_session="))
        .ok_or_else(|| SidecarError::new("Sidecar bootstrap did not return a session cookie."))
}

fn loopback_port(origin: &str) -> Result<u16, SidecarError> {
    origin
        .strip_prefix("http://127.0.0.1:")
        .and_then(|port| port.parse::<u16>().ok())
        .ok_or_else(|| SidecarError::new("Invalid sidecar origin."))
}

fn parse_json_response(response: &[u8]) -> Result<serde_json::Value, SidecarError> {
    let response = parse_http_response(response)?;
    if response.status != 200 {
        return Err(SidecarError::new(format!(
            "Sidecar request failed with status {}.",
            response.status
        )));
    }
    serde_json::from_slice(&response.body)
        .map_err(|error| SidecarError::new(format!("Invalid sidecar JSON response: {error}")))
}

#[derive(Debug)]
struct SidecarHttpResponse {
    status: u16,
    content_type: Option<String>,
    cache_control: Option<String>,
    body: Vec<u8>,
}

impl SidecarHttpResponse {
    fn into_tauri_response(self) -> http::Response<Vec<u8>> {
        let mut builder = http::Response::builder().status(self.status);
        if let Some(content_type) = self.content_type {
            builder = builder.header(http::header::CONTENT_TYPE, content_type);
        }
        if let Some(cache_control) = self.cache_control {
            builder = builder.header(http::header::CACHE_CONTROL, cache_control);
        }
        builder
            .body(self.body)
            .expect("validated sidecar response headers must be valid")
    }
}

fn parse_http_response(response: &[u8]) -> Result<SidecarHttpResponse, SidecarError> {
    let header_end = response
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .ok_or_else(|| SidecarError::new("Malformed sidecar response."))?;
    let headers = std::str::from_utf8(&response[..header_end])
        .map_err(|_| SidecarError::new("Sidecar response headers were not UTF-8."))?;
    let mut lines = headers.lines();
    let status_line = lines
        .next()
        .ok_or_else(|| SidecarError::new("Missing sidecar response status."))?;
    let status = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|status| status.parse::<u16>().ok())
        .ok_or_else(|| SidecarError::new("Invalid sidecar response status."))?;

    let mut content_type = None;
    let mut cache_control = None;
    for line in lines {
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        let value = value.trim();
        if name.eq_ignore_ascii_case("content-type") {
            content_type = Some(value.to_owned());
        } else if name.eq_ignore_ascii_case("cache-control") {
            cache_control = Some(value.to_owned());
        }
    }
    Ok(SidecarHttpResponse {
        status,
        content_type,
        cache_control,
        body: response[header_end + 4..].to_vec(),
    })
}

fn managed_media_path(request: &http::Request<Vec<u8>>) -> Result<String, SidecarError> {
    if request.method() != http::Method::GET {
        return Err(SidecarError::new("MemeSort media only supports GET."));
    }
    if request.uri().query().is_some() {
        return Err(SidecarError::new(
            "MemeSort media URLs cannot include a query string.",
        ));
    }
    let path = request.uri().path();
    let relative_path = path
        .strip_prefix("/media/")
        .ok_or_else(|| SidecarError::new("Unknown MemeSort media route."))?;
    if relative_path.is_empty() || relative_path.contains('\\') || relative_path.contains('%') {
        return Err(SidecarError::new("Invalid MemeSort media path."));
    }
    let mut segments = relative_path.split('/');
    let directory = segments.next().unwrap_or_default();
    let file_name = segments.next().unwrap_or_default();
    if segments.next().is_some()
        || file_name.is_empty()
        || matches!(file_name, "." | "..")
        || !matches!(
            directory,
            "originals" | "thumbnails" | "frames" | "contact_sheets"
        )
    {
        return Err(SidecarError::new("Invalid MemeSort media path."));
    }
    Ok(path.to_owned())
}

fn validate_asset_id(asset_id: &str) -> Result<String, SidecarError> {
    let is_uuid = asset_id.len() == 36
        && asset_id.chars().enumerate().all(|(index, character)| {
            if matches!(index, 8 | 13 | 18 | 23) {
                character == '-'
            } else {
                character.is_ascii_hexdigit()
            }
        });
    if !is_uuid {
        return Err(SidecarError::new("Invalid MemeSort Asset identifier."));
    }
    Ok(asset_id.to_ascii_lowercase())
}

fn validate_asset_ids(asset_ids: &[String]) -> Result<Vec<String>, SidecarError> {
    if asset_ids.is_empty() || asset_ids.len() > MAX_BATCH_ASSETS {
        return Err(SidecarError::new("Invalid MemeSort Asset selection."));
    }
    let mut validated = Vec::with_capacity(asset_ids.len());
    for asset_id in asset_ids {
        let asset_id = validate_asset_id(asset_id)?;
        if !validated.contains(&asset_id) {
            validated.push(asset_id);
        }
    }
    Ok(validated)
}

fn validate_source_path(source_path: &str) -> Result<String, SidecarError> {
    if source_path.is_empty()
        || source_path.len() > MAX_SOURCE_PATH_BYTES
        || source_path.contains('\0')
        || source_path.contains('\r')
        || source_path.contains('\n')
    {
        return Err(SidecarError::new("Invalid MemeSort Source Record."));
    }
    Ok(source_path.to_owned())
}

fn media_error_response(error: SidecarError) -> http::Response<Vec<u8>> {
    let status = if error.0.starts_with("Unknown MemeSort media route")
        || error.0.starts_with("Invalid MemeSort media path")
        || error.0.starts_with("MemeSort media only supports")
        || error.0.starts_with("MemeSort media URLs cannot")
    {
        http::StatusCode::BAD_REQUEST
    } else {
        http::StatusCode::BAD_GATEWAY
    };
    http::Response::builder()
        .status(status)
        .header(http::header::CONTENT_TYPE, "text/plain; charset=utf-8")
        .body(b"MemeSort media is unavailable.".to_vec())
        .expect("static response headers must be valid")
}

fn terminate_child(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(test)]
mod tests {
    use std::{
        io::{Read, Write},
        net::TcpListener,
        path::PathBuf,
        thread,
    };

    use super::{
        authenticated_get_json, authenticated_get_media, authenticated_post_json,
        managed_media_path, parse_handshake, parse_json_response, validate_asset_id,
        validate_asset_ids, validate_source_path, ApiRoute, AssetIdPayload, BatchAssetAction,
        BatchAssetActionPayload, EmptyPayload, ImportSelection, MutationRoute,
        RemoveSourceRecordPayload, StartImportPayload,
    };
    use tauri::http::{Method, Request, StatusCode};

    #[test]
    fn accepts_the_versioned_loopback_handshake() {
        let handshake = parse_handshake(
            r#"{"protocol_version":1,"origin":"http://127.0.0.1:43123","bootstrap_url":"http://127.0.0.1:43123/?bootstrap=secret","library_root":"C:\\Library"}"#,
        )
        .expect("handshake should be accepted");

        assert_eq!(handshake.protocol_version, 1);
    }

    #[test]
    fn rejects_a_non_loopback_handshake() {
        let error = parse_handshake(
            r#"{"protocol_version":1,"origin":"http://example.test:43123","bootstrap_url":"http://example.test:43123/?bootstrap=secret","library_root":"C:\\Library"}"#,
        )
        .expect_err("foreign origin must be rejected");

        assert!(error.to_string().contains("127.0.0.1"));
    }

    #[test]
    fn accepts_an_authenticated_state_response() {
        let payload = parse_json_response(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"library_status\":{\"total_assets\":3}}")
            .expect("state response should parse");

        assert_eq!(payload["library_status"]["total_assets"], 3);
    }

    #[test]
    fn rejects_non_successful_or_malformed_responses() {
        let error = parse_json_response(b"HTTP/1.1 401 Unauthorized\r\n\r\n{}")
            .expect_err("unauthenticated response must not reach the WebView");
        assert!(error.to_string().contains("401"));
        assert!(parse_json_response(b"not HTTP").is_err());
    }

    #[test]
    fn forwards_the_session_cookie_only_to_the_allowlisted_state_route() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("state request should connect");
            let mut request = [0_u8; 1024];
            let size = stream
                .read(&mut request)
                .expect("state request should read");
            let request = std::str::from_utf8(&request[..size]).expect("request must be UTF-8");
            assert!(request.starts_with("GET /api/state HTTP/1.1"));
            assert!(request.contains("Cookie: memesort_session=test-token"));
            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"ok\":true}",
                )
                .expect("state response should write");
        });

        let payload = authenticated_get_json(
            &format!("http://127.0.0.1:{port}"),
            "memesort_session=test-token",
            ApiRoute::State,
        )
        .expect("state request should succeed");
        server.join().expect("test server should finish");

        assert_eq!(payload["ok"], true);
    }

    #[test]
    fn forwards_asset_requests_only_to_the_fixed_asset_routes() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            for expected_path in [
                "GET /api/assets HTTP/1.1",
                "GET /api/asset-detail?asset_id=123e4567-e89b-12d3-a456-426614174000 HTTP/1.1",
            ] {
                let (mut stream, _) = listener.accept().expect("asset request should connect");
                let mut request = [0_u8; 1024];
                let size = stream
                    .read(&mut request)
                    .expect("asset request should read");
                let request = std::str::from_utf8(&request[..size]).expect("request must be UTF-8");
                assert!(request.starts_with(expected_path));
                assert!(request.contains("Cookie: memesort_session=test-token"));
                stream
                    .write_all(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"ok\":true}",
                    )
                    .expect("asset response should write");
            }
        });
        let origin = format!("http://127.0.0.1:{port}");

        authenticated_get_json(&origin, "memesort_session=test-token", ApiRoute::Assets)
            .expect("assets request should succeed");
        authenticated_get_json(
            &origin,
            "memesort_session=test-token",
            ApiRoute::AssetDetail(
                validate_asset_id("123e4567-e89b-12d3-a456-426614174000")
                    .expect("valid identifier"),
            ),
        )
        .expect("asset detail request should succeed");
        server.join().expect("test server should finish");
    }

    #[test]
    fn rejects_an_invalid_asset_identifier_before_connecting() {
        for asset_id in [
            "",
            "../library.sqlite",
            "not-a-uuid",
            "123e4567-e89b-12d3-a456-426614174000?x=1",
            "123e4567ee9b12d3a456426614174000abcd",
        ] {
            assert!(
                validate_asset_id(asset_id).is_err(),
                "{asset_id} must be rejected"
            );
        }
    }

    #[test]
    fn forwards_mutations_only_to_fixed_routes_with_the_private_cookie() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            for (path, expected_payload) in [
                (
                    "/api/delete-asset",
                    "\"asset_id\":\"123e4567-e89b-12d3-a456-426614174000\"",
                ),
                (
                    "/api/remove-source-record",
                    "\"source_path\":\"C:/Source/asset.gif\"",
                ),
                (
                    "/api/assets/batch-action",
                    "\"action\":\"rebuild-active-index\"",
                ),
            ] {
                let (mut stream, _) = listener.accept().expect("mutation request should connect");
                let mut request = Vec::new();
                loop {
                    let mut chunk = [0_u8; 1024];
                    let size = stream
                        .read(&mut chunk)
                        .expect("mutation request should read");
                    request.extend_from_slice(&chunk[..size]);
                    let Some(header_end) =
                        request.windows(4).position(|window| window == b"\r\n\r\n")
                    else {
                        continue;
                    };
                    let headers =
                        std::str::from_utf8(&request[..header_end]).expect("headers must be UTF-8");
                    let content_length = headers
                        .lines()
                        .find_map(|line| line.strip_prefix("Content-Length: "))
                        .and_then(|value| value.parse::<usize>().ok())
                        .expect("request should include a body length");
                    if request.len() >= header_end + 4 + content_length {
                        break;
                    }
                }
                let request = std::str::from_utf8(&request).expect("request must be UTF-8");
                assert!(request.starts_with(&format!("POST {path} HTTP/1.1")));
                assert!(request.contains("Cookie: memesort_session=test-token"));
                assert!(request.contains(expected_payload));
                stream
                    .write_all(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"ok\":true}",
                    )
                    .expect("mutation response should write");
            }
        });
        let origin = format!("http://127.0.0.1:{port}");

        authenticated_post_json(
            &origin,
            "memesort_session=test-token",
            MutationRoute::DeleteAsset,
            &AssetIdPayload {
                asset_id: "123e4567-e89b-12d3-a456-426614174000".to_owned(),
            },
        )
        .expect("delete request should succeed");
        authenticated_post_json(
            &origin,
            "memesort_session=test-token",
            MutationRoute::RemoveSourceRecord,
            &RemoveSourceRecordPayload {
                asset_id: "123e4567-e89b-12d3-a456-426614174000".to_owned(),
                source_path: "C:/Source/asset.gif".to_owned(),
            },
        )
        .expect("source request should succeed");
        authenticated_post_json(
            &origin,
            "memesort_session=test-token",
            MutationRoute::BatchAssetAction,
            &BatchAssetActionPayload {
                action: BatchAssetAction::RebuildActiveIndex.as_api_value(),
                asset_ids: vec!["123e4567-e89b-12d3-a456-426614174000".to_owned()],
            },
        )
        .expect("batch request should succeed");
        server.join().expect("test server should finish");
    }

    #[test]
    fn rejects_invalid_batch_selection_and_source_record_inputs() {
        assert!(validate_asset_ids(&[]).is_err());
        assert!(validate_asset_ids(&["not-an-asset".to_owned()]).is_err());
        assert!(validate_source_path("").is_err());
        assert!(validate_source_path("C:/Source/unsafe\npath.gif").is_err());
        assert!(BatchAssetAction::parse("arbitrary-request").is_err());
    }

    #[test]
    fn keeps_import_paths_in_the_native_selection_state() {
        let selection = ImportSelection::new();
        assert!(selection.selected_path().is_err());

        let selected = selection
            .replace(Some(PathBuf::from("C:/Source/Memes")))
            .expect("selection should be stored");

        assert_eq!(selected.as_deref(), Some("C:/Source/Memes"));
        assert_eq!(
            selection.selected_path().expect("path should be available"),
            "C:/Source/Memes"
        );
    }

    #[test]
    fn forwards_import_controls_only_to_fixed_routes_with_the_private_cookie() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            for (path, expected_payload) in [
                ("/api/import/start", "\"start_indexing\":true"),
                ("/api/import/pause", "{}"),
                ("/api/import/resume", "{}"),
            ] {
                let (mut stream, _) = listener.accept().expect("import request should connect");
                let mut request = Vec::new();
                loop {
                    let mut chunk = [0_u8; 1024];
                    let size = stream.read(&mut chunk).expect("import request should read");
                    request.extend_from_slice(&chunk[..size]);
                    let Some(header_end) =
                        request.windows(4).position(|window| window == b"\r\n\r\n")
                    else {
                        continue;
                    };
                    let headers =
                        std::str::from_utf8(&request[..header_end]).expect("headers must be UTF-8");
                    let content_length = headers
                        .lines()
                        .find_map(|line| line.strip_prefix("Content-Length: "))
                        .and_then(|value| value.parse::<usize>().ok())
                        .expect("request should include a body length");
                    if request.len() >= header_end + 4 + content_length {
                        break;
                    }
                }
                let request = std::str::from_utf8(&request).expect("request must be UTF-8");
                assert!(request.starts_with(&format!("POST {path} HTTP/1.1")));
                assert!(request.contains("Cookie: memesort_session=test-token"));
                assert!(request.contains(expected_payload));
                stream
                    .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"status\":\"running\"}")
                    .expect("import response should write");
            }
        });
        let origin = format!("http://127.0.0.1:{port}");

        authenticated_post_json(
            &origin,
            "memesort_session=test-token",
            MutationRoute::StartImport,
            &StartImportPayload {
                path: "C:/Source/Memes".to_owned(),
                start_indexing: true,
            },
        )
        .expect("start import should succeed");
        authenticated_post_json(
            &origin,
            "memesort_session=test-token",
            MutationRoute::PauseImport,
            &EmptyPayload {},
        )
        .expect("pause import should succeed");
        authenticated_post_json(
            &origin,
            "memesort_session=test-token",
            MutationRoute::ResumeImport,
            &EmptyPayload {},
        )
        .expect("resume import should succeed");
        server.join().expect("test server should finish");
    }

    #[test]
    fn forwards_binary_media_with_its_mime_type_through_the_managed_route() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("media request should connect");
            let mut request = [0_u8; 1024];
            let size = stream
                .read(&mut request)
                .expect("media request should read");
            let request = std::str::from_utf8(&request[..size]).expect("request must be UTF-8");
            assert!(request.starts_with("GET /media/thumbnails/asset.gif HTTP/1.1"));
            assert!(request.contains("Cookie: memesort_session=test-token"));
            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: image/gif\r\nCache-Control: public, max-age=60\r\nContent-Length: 6\r\n\r\nGIF89a",
                )
                .expect("media response should write");
        });
        let request = Request::builder()
            .method(Method::GET)
            .uri("/media/thumbnails/asset.gif")
            .body(Vec::new())
            .expect("media request should build");

        let response = authenticated_get_media(
            &format!("http://127.0.0.1:{port}"),
            "memesort_session=test-token",
            &request,
        )
        .expect("media request should succeed")
        .into_tauri_response();
        server.join().expect("test server should finish");

        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.headers()["content-type"], "image/gif");
        assert_eq!(response.headers()["cache-control"], "public, max-age=60");
        assert_eq!(response.body(), b"GIF89a");
    }

    #[test]
    fn rejects_non_media_routes_and_unsafe_media_paths_before_connecting() {
        for (method, uri) in [
            (Method::GET, "/api/state"),
            (Method::GET, "/media/originals/../library.sqlite"),
            (Method::GET, "/media/%2e%2e/library.sqlite"),
            (Method::GET, "/media/originals/C:\\outside.png"),
            (Method::GET, "/media/models/runtime-manifest.json"),
            (Method::POST, "/media/thumbnails/asset.jpg"),
            (Method::GET, "/media/thumbnails/asset.jpg?bootstrap=secret"),
        ] {
            let request = Request::builder()
                .method(method)
                .uri(uri)
                .body(Vec::new())
                .expect("request should build");
            assert!(
                managed_media_path(&request).is_err(),
                "{uri} must be rejected"
            );

            let error = authenticated_get_media(
                "http://127.0.0.1:1",
                "memesort_session=must-not-leak",
                &request,
            )
            .expect_err("unsafe request must not be sent to the sidecar");
            assert!(
                error.to_string().contains("MemeSort media")
                    || error.to_string().contains("Invalid")
            );
        }
    }
}
