use std::{
    env,
    error::Error,
    fmt, fs,
    io::{BufRead, BufReader, Read, Write},
    net::TcpStream,
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{mpsc, Mutex},
    thread,
    time::{Duration, Instant},
};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use serde::{Deserialize, Serialize};
use tauri::{http, AppHandle, Manager};

use crate::clipboard::{
    build_hdrop_payload, build_static_image_payload, classify_managed_path, write_payload_via,
    ClipboardPayload, ClipboardWriter, ManagedCopyKind, WindowsClipboardWriter,
};
use crate::native_selection::{
    validate_library_paths, ImportSelection, LibraryImportSelection, SearchImageSelection,
};

#[cfg(test)]
use crate::native_drag::{
    logical_drag_position, native_drag_counts, native_drag_summary, process_native_drag_event,
    NativeDragContext, NativeDragInput, NativeDragPhase,
};
#[cfg(test)]
use crate::native_selection::{LibrarySelectionEntry, LibrarySelectionOrigin};

const PROTOCOL_VERSION: u32 = 1;
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(15);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(15);
const MAX_BATCH_ASSETS: usize = 1_000;
const MAX_IMPORT_SOURCES: usize = 256;
const MAX_SOURCE_PATH_BYTES: usize = 32 * 1024;
const MAX_SEARCH_QUERY_BYTES: usize = 4 * 1024;
pub const MEDIA_PROTOCOL: &str = "memesort-media";
#[cfg(not(debug_assertions))]
const SIDECAR_BINARY_NAME: &str = "memesort-sidecar-x86_64-pc-windows-msvc.exe";

#[derive(Debug, Serialize)]
pub struct SidecarError {
    status: Option<u16>,
    error: String,
    detail: String,
    retryable: bool,
}

impl SidecarError {
    pub(crate) fn new(message: impl Into<String>) -> Self {
        Self {
            status: None,
            error: "SidecarError".to_owned(),
            detail: message.into(),
            retryable: true,
        }
    }

    fn backend_response(status: u16, body: &[u8]) -> Self {
        let payload = serde_json::from_slice::<serde_json::Value>(body).ok();
        let error = payload
            .as_ref()
            .and_then(|value| value.get("error"))
            .and_then(serde_json::Value::as_str)
            .unwrap_or("BackendRequestFailed")
            .to_owned();
        let detail = payload
            .as_ref()
            .and_then(|value| value.get("detail"))
            .and_then(serde_json::Value::as_str)
            .unwrap_or("The MemeSort sidecar rejected the request.")
            .chars()
            .take(4_096)
            .collect();
        Self {
            status: Some(status),
            error,
            detail,
            retryable: status == 408 || status == 429 || status >= 500,
        }
    }
}

impl fmt::Display for SidecarError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.detail)
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

    /// One Import Batch snapshot. Progress travels to the WebView without
    /// filesystem paths, so polling stays safe from any page.
    fn import_status(&self) -> Result<serde_json::Value, SidecarError> {
        authenticated_get_json(&self.origin, &self.session_cookie, ApiRoute::ImportStatus)
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

    fn start_import_batch(
        &self,
        sources: Vec<String>,
        indexing_policy: IndexingPolicy,
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_post_json(
            &self.origin,
            &self.session_cookie,
            MutationRoute::StartImport,
            &StartImportPayload {
                sources: validate_import_sources(&sources)?,
                indexing_policy: indexing_policy.as_api_value(),
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

    fn search_text_for_connection(
        origin: &str,
        session_cookie: &str,
        query: &str,
        request_id: &str,
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_get_json(
            origin,
            session_cookie,
            ApiRoute::TextSearch {
                query: validate_search_query(query)?,
                request_id: validate_asset_id(request_id)?,
            },
        )
    }

    fn cancel_search_for_connection(
        origin: &str,
        session_cookie: &str,
        request_id: &str,
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_post_json(
            origin,
            session_cookie,
            MutationRoute::CancelSearch,
            &SearchRequestPayload {
                request_id: validate_asset_id(request_id)?,
            },
        )
    }

    fn search_image_for_connection(
        origin: &str,
        session_cookie: &str,
        image_path: &str,
        request_id: &str,
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_post_json(
            origin,
            session_cookie,
            MutationRoute::SearchImage,
            &SearchImagePayload {
                path: image_path.to_owned(),
                top_k: 18,
                request_id: validate_asset_id(request_id)?,
            },
        )
    }

    fn find_similar_for_connection(
        origin: &str,
        session_cookie: &str,
        asset_id: &str,
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_get_json(
            origin,
            session_cookie,
            ApiRoute::SimilarAssets {
                asset_id: validate_asset_id(asset_id)?,
            },
        )
    }

    fn duplicates_for_connection(
        origin: &str,
        session_cookie: &str,
        threshold: f64,
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_get_json(
            origin,
            session_cookie,
            ApiRoute::Duplicates {
                threshold: validate_duplicate_threshold(threshold)?,
            },
        )
    }

    fn pause_worker_loop_for_connection(
        origin: &str,
        session_cookie: &str,
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_post_json(
            origin,
            session_cookie,
            MutationRoute::PauseWorkerLoop,
            &EmptyPayload {},
        )
    }

    fn resume_worker_loop_for_connection(
        origin: &str,
        session_cookie: &str,
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_post_json(
            origin,
            session_cookie,
            MutationRoute::ResumeWorkerLoop,
            &EmptyPayload {},
        )
    }

    fn trigger_worker_loop_for_connection(
        origin: &str,
        session_cookie: &str,
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_post_json(
            origin,
            session_cookie,
            MutationRoute::TriggerWorkerLoop,
            &EmptyPayload {},
        )
    }

    fn run_runtime_health_check_for_connection(
        origin: &str,
        session_cookie: &str,
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_post_json(
            origin,
            session_cookie,
            MutationRoute::RunRuntimeHealthCheck,
            &EmptyPayload {},
        )
    }

    fn retry_failed_jobs_for_connection(
        origin: &str,
        session_cookie: &str,
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_post_json(
            origin,
            session_cookie,
            MutationRoute::RetryFailedJobs,
            &EmptyPayload {},
        )
    }

    fn pending_jobs_for_connection(
        origin: &str,
        session_cookie: &str,
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_get_json(origin, session_cookie, ApiRoute::PendingJobs)
    }

    fn delete_pending_jobs_for_connection(
        origin: &str,
        session_cookie: &str,
        job_ids: &[String],
    ) -> Result<serde_json::Value, SidecarError> {
        authenticated_post_json(
            origin,
            session_cookie,
            MutationRoute::DeletePendingJobs,
            &PendingJobIdsPayload {
                job_ids: validate_job_ids(job_ids)?,
            },
        )
    }

    fn resolve_asset_reveal_target_for_connection(
        origin: &str,
        session_cookie: &str,
        asset_id: &str,
        target: AssetRevealTarget,
        source_path: Option<&str>,
    ) -> Result<PathBuf, SidecarError> {
        let source_path = match target {
            AssetRevealTarget::Managed => None,
            AssetRevealTarget::Source => {
                Some(validate_source_path(source_path.ok_or_else(|| {
                    SidecarError::new("Choose a Source Record to reveal.")
                })?)?)
            }
        };
        let payload = authenticated_post_json(
            origin,
            session_cookie,
            MutationRoute::ResolveAssetRevealTarget,
            &AssetRevealTargetPayload {
                asset_id: validate_asset_id(asset_id)?,
                target: target.as_api_value(),
                source_path,
            },
        )?;
        let response_target = payload
            .get("target")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| SidecarError::new("Sidecar did not return an Asset reveal target."))?;
        if response_target != target.as_api_value() {
            return Err(SidecarError::new(
                "Sidecar returned an unexpected Asset reveal target.",
            ));
        }
        let resolved_path = payload
            .get("resolved_path")
            .and_then(serde_json::Value::as_str)
            .filter(|path| !path.is_empty() && !path.contains('\0'))
            .ok_or_else(|| {
                SidecarError::new("Sidecar did not return a valid Asset reveal path.")
            })?;
        let resolved_path = PathBuf::from(resolved_path);
        if !resolved_path.is_absolute() {
            return Err(SidecarError::new(
                "Sidecar returned a non-absolute Asset reveal path.",
            ));
        }
        Ok(resolved_path)
    }

    /// Resolve one managed Library Copy through the fixed reveal-target route.
    /// The WebView supplies Asset IDs only; no path ever crosses the bridge.
    fn resolve_managed_copy_for_connection(
        origin: &str,
        session_cookie: &str,
        asset_id: &str,
    ) -> Result<PathBuf, SidecarError> {
        Self::resolve_asset_reveal_target_for_connection(
            origin,
            session_cookie,
            asset_id,
            AssetRevealTarget::Managed,
            None,
        )
    }

    fn validate_managed_file(path: &Path) -> Result<(), SidecarError> {
        if !path.is_absolute() {
            return Err(SidecarError::new(
                "Sidecar returned a non-absolute Asset Library Copy.",
            ));
        }
        let metadata = fs::metadata(path).map_err(|error| {
            SidecarError::new(format!("Could not access the Asset Library Copy: {error}"))
        })?;
        if !metadata.is_file() {
            return Err(SidecarError::new(
                "The resolved Asset Library Copy is not a file.",
            ));
        }
        Ok(())
    }

    /// Validate one Asset ID, resolve its managed Library Copy through the
    /// fixed reveal-target route, and gate on existence/type. Shared by every
    /// Clipboard Copy command so validation and resolution stay identical.
    fn resolve_checked_managed_file(
        origin: &str,
        session_cookie: &str,
        asset_id: &str,
    ) -> Result<PathBuf, SidecarError> {
        let asset_id = validate_asset_id(asset_id)?;
        let path = Self::resolve_managed_copy_for_connection(origin, session_cookie, &asset_id)?;
        Self::validate_managed_file(&path)?;
        Ok(path)
    }

    /// Build the primary Clipboard Copy payload for one Asset.
    ///
    /// Static stills decode to `CF_DIBV5` plus a registered PNG payload. GIFs
    /// keep their encoded animation via a single-file `CF_HDROP` reference.
    /// Every fallible step finishes before the caller touches the OS
    /// clipboard.
    fn prepare_copy_asset_for_connection(
        origin: &str,
        session_cookie: &str,
        asset_id: &str,
    ) -> Result<ClipboardPayload, SidecarError> {
        let path = Self::resolve_checked_managed_file(origin, session_cookie, asset_id)?;
        match classify_managed_path(&path)? {
            ManagedCopyKind::StaticImage => {
                let bytes = fs::read(&path).map_err(|error| {
                    SidecarError::new(format!(
                        "Could not read the Asset Library Copy: {error}"
                    ))
                })?;
                build_static_image_payload(&bytes)
                    .map(ClipboardPayload::StaticImage)
            }
            ManagedCopyKind::GifFile => {
                build_hdrop_payload(std::slice::from_ref(&path)).map(|hdrop| {
                    ClipboardPayload::FileDrop { hdrop }
                })
            }
        }
    }

    /// Build a single-file `CF_HDROP` payload for one Asset's Library Copy.
    fn prepare_copy_original_file_for_connection(
        origin: &str,
        session_cookie: &str,
        asset_id: &str,
    ) -> Result<ClipboardPayload, SidecarError> {
        let path = Self::resolve_checked_managed_file(origin, session_cookie, asset_id)?;
        build_hdrop_payload(std::slice::from_ref(&path))
            .map(|hdrop| ClipboardPayload::FileDrop { hdrop })
    }

    /// Build one multi-file `CF_HDROP` payload. IDs are validated and
    /// de-duplicated up front and the existing batch limit applies.
    fn prepare_copy_original_files_for_connection(
        origin: &str,
        session_cookie: &str,
        asset_ids: &[String],
    ) -> Result<ClipboardPayload, SidecarError> {
        let asset_ids = validate_asset_ids(asset_ids)?;
        let mut paths = Vec::with_capacity(asset_ids.len());
        for asset_id in &asset_ids {
            let path = Self::resolve_checked_managed_file(origin, session_cookie, asset_id)?;
            if !paths.contains(&path) {
                paths.push(path);
            }
        }
        build_hdrop_payload(&paths).map(|hdrop| ClipboardPayload::FileDrop { hdrop })
    }

    fn copy_asset_with_writer(
        origin: &str,
        session_cookie: &str,
        asset_id: &str,
        writer: &impl ClipboardWriter,
    ) -> Result<(), SidecarError> {
        let payload = Self::prepare_copy_asset_for_connection(origin, session_cookie, asset_id)?;
        write_payload_via(writer, &payload)
    }

    fn copy_original_file_with_writer(
        origin: &str,
        session_cookie: &str,
        asset_id: &str,
        writer: &impl ClipboardWriter,
    ) -> Result<(), SidecarError> {
        let payload =
            Self::prepare_copy_original_file_for_connection(origin, session_cookie, asset_id)?;
        write_payload_via(writer, &payload)
    }

    fn copy_original_files_with_writer(
        origin: &str,
        session_cookie: &str,
        asset_ids: &[String],
        writer: &impl ClipboardWriter,
    ) -> Result<(), SidecarError> {
        let payload =
            Self::prepare_copy_original_files_for_connection(origin, session_cookie, asset_ids)?;
        write_payload_via(writer, &payload)
    }

    fn resolve_log_directory_for_connection(
        origin: &str,
        session_cookie: &str,
    ) -> Result<PathBuf, SidecarError> {
        let payload = authenticated_post_json(
            origin,
            session_cookie,
            MutationRoute::ResolveLogDirectory,
            &EmptyPayload {},
        )?;
        let resolved_path = payload
            .get("resolved_path")
            .and_then(serde_json::Value::as_str)
            .filter(|path| !path.is_empty() && !path.contains('\0'))
            .ok_or_else(|| SidecarError::new("Sidecar did not return a valid log directory."))?;
        let resolved_path = PathBuf::from(resolved_path);
        if !resolved_path.is_absolute() {
            return Err(SidecarError::new(
                "Sidecar returned a non-absolute log directory.",
            ));
        }
        Ok(resolved_path)
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
    ImportStatus,
    Assets,
    AssetDetail(String),
    TextSearch { query: String, request_id: String },
    SimilarAssets { asset_id: String },
    Duplicates { threshold: f64 },
    PendingJobs,
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

#[allow(dead_code)]
#[derive(Clone, Copy)]
enum IndexingPolicy {
    Never,
    Required,
    IfReady,
}

impl IndexingPolicy {
    fn as_api_value(self) -> &'static str {
        match self {
            Self::Never => "never",
            Self::Required => "required",
            Self::IfReady => "if-ready",
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
    CancelSearch,
    SearchImage,
    PauseWorkerLoop,
    ResumeWorkerLoop,
    TriggerWorkerLoop,
    RunRuntimeHealthCheck,
    RetryFailedJobs,
    DeletePendingJobs,
    ResolveAssetRevealTarget,
    ResolveLogDirectory,
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
            Self::CancelSearch => "/api/search/cancel",
            Self::SearchImage => "/api/search-image",
            Self::PauseWorkerLoop => "/api/worker-loop/pause",
            Self::ResumeWorkerLoop => "/api/worker-loop/resume",
            Self::TriggerWorkerLoop => "/api/worker-loop/trigger",
            Self::RunRuntimeHealthCheck => "/api/health",
            Self::RetryFailedJobs => "/api/retry-failed-jobs",
            Self::DeletePendingJobs => "/api/pending-jobs/delete",
            Self::ResolveAssetRevealTarget => "/api/resolve-asset-reveal-target",
            Self::ResolveLogDirectory => "/api/resolve-log-directory",
        }
    }
}

#[derive(Clone, Copy)]
enum AssetRevealTarget {
    Managed,
    Source,
}

impl AssetRevealTarget {
    fn parse(value: &str) -> Result<Self, SidecarError> {
        match value {
            "managed" => Ok(Self::Managed),
            "source" => Ok(Self::Source),
            _ => Err(SidecarError::new("Invalid MemeSort Asset reveal target.")),
        }
    }

    fn as_api_value(self) -> &'static str {
        match self {
            Self::Managed => "managed",
            Self::Source => "source",
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
    sources: Vec<String>,
    indexing_policy: &'static str,
}

#[derive(Serialize)]
struct EmptyPayload {}

#[derive(Serialize)]
struct SearchRequestPayload {
    request_id: String,
}

#[derive(Serialize)]
struct SearchImagePayload {
    path: String,
    top_k: u8,
    request_id: String,
}

#[derive(Serialize)]
struct PendingJobIdsPayload {
    job_ids: Vec<String>,
}

#[derive(Serialize)]
struct AssetRevealTargetPayload {
    asset_id: String,
    target: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    source_path: Option<String>,
}

impl ApiRoute {
    fn path(&self) -> String {
        match self {
            Self::State => "/api/state".to_owned(),
            Self::ImportStatus => "/api/import".to_owned(),
            Self::Assets => "/api/assets".to_owned(),
            Self::AssetDetail(asset_id) => format!("/api/asset-detail?asset_id={asset_id}"),
            Self::TextSearch { query, request_id } => format!(
                "/api/search?query={}&top_k=18&request_id={request_id}",
                percent_encode_query_value(query)
            ),
            Self::SimilarAssets { asset_id } => {
                format!("/api/find-similar?asset_id={asset_id}&top_k=18")
            }
            Self::Duplicates { threshold } => format!("/api/duplicates?threshold={threshold}"),
            Self::PendingJobs => "/api/pending-jobs".to_owned(),
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
pub fn get_app_state(app: AppHandle) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_session(&app, |session| session.app_state())
}

#[tauri::command]
pub fn get_import_status(app: AppHandle) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_session(&app, |session| session.import_status())
}

#[tauri::command]
pub fn get_assets(app: AppHandle) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_session(&app, |session| session.assets())
}

#[tauri::command]
pub fn get_asset_detail(
    app: AppHandle,
    asset_id: String,
) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_session(&app, |session| session.asset_detail(&asset_id))
}

#[tauri::command]
pub fn delete_asset(app: AppHandle, asset_id: String) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_session(&app, |session| session.delete_asset(&asset_id))
}

#[tauri::command]
pub fn remove_source_record(
    app: AppHandle,
    asset_id: String,
    source_path: String,
) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_session(&app, |session| {
        session.remove_source_record(&asset_id, &source_path)
    })
}

#[tauri::command]
pub fn batch_asset_action(
    app: AppHandle,
    action: String,
    asset_ids: Vec<String>,
) -> Result<serde_json::Value, SidecarError> {
    let action = BatchAssetAction::parse(&action)?;
    with_sidecar_session(&app, |session| {
        session.batch_asset_action(action, &asset_ids)
    })
}

#[tauri::command]
pub fn start_library_import(
    app: AppHandle,
    selection_id: String,
) -> Result<serde_json::Value, SidecarError> {
    let selection_id = validate_library_selection_id(&selection_id)?;
    let selection = app
        .try_state::<LibraryImportSelection>()
        .ok_or_else(|| SidecarError::new("MemeSort Library selection is unavailable."))?;
    let entry = selection.take(&selection_id)?;
    let sources = validate_library_paths(entry.origin, &entry.paths)?;
    with_sidecar_session(&app, |session| {
        session.start_import_batch(sources, IndexingPolicy::IfReady)
    })
}

#[tauri::command]
pub fn start_import(app: AppHandle) -> Result<serde_json::Value, SidecarError> {
    start_selected_import(&app, false)
}

#[tauri::command]
pub fn start_import_and_index(app: AppHandle) -> Result<serde_json::Value, SidecarError> {
    start_selected_import(&app, true)
}

#[tauri::command]
pub fn pause_import(app: AppHandle) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_session(&app, |session| session.pause_import())
}

#[tauri::command]
pub fn resume_import(app: AppHandle) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_session(&app, |session| session.resume_import())
}

#[tauri::command]
pub async fn search_text(
    app: AppHandle,
    query: String,
    request_id: String,
) -> Result<serde_json::Value, SidecarError> {
    tauri::async_runtime::spawn_blocking(move || {
        with_sidecar_connection(&app, |origin, session_cookie| {
            SidecarSession::search_text_for_connection(origin, session_cookie, &query, &request_id)
        })
    })
    .await
    .map_err(|error| SidecarError::new(format!("Text Search Request did not complete: {error}")))?
}

#[tauri::command]
pub fn cancel_search(
    app: AppHandle,
    request_id: String,
) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_connection(&app, |origin, session_cookie| {
        SidecarSession::cancel_search_for_connection(origin, session_cookie, &request_id)
    })
}

#[tauri::command]
pub async fn search_image(
    app: AppHandle,
    request_id: String,
) -> Result<serde_json::Value, SidecarError> {
    tauri::async_runtime::spawn_blocking(move || {
        let selection = app
            .try_state::<SearchImageSelection>()
            .ok_or_else(|| SidecarError::new("MemeSort image selection is unavailable."))?;
        let image_path = selection.selected_path()?;
        with_sidecar_connection(&app, |origin, session_cookie| {
            SidecarSession::search_image_for_connection(
                origin,
                session_cookie,
                &image_path,
                &request_id,
            )
        })
    })
    .await
    .map_err(|error| SidecarError::new(format!("Image Search Request did not complete: {error}")))?
}

#[tauri::command]
pub fn find_similar(app: AppHandle, asset_id: String) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_connection(&app, |origin, session_cookie| {
        SidecarSession::find_similar_for_connection(origin, session_cookie, &asset_id)
    })
}

#[tauri::command]
pub fn get_duplicates(app: AppHandle, threshold: f64) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_connection(&app, |origin, session_cookie| {
        SidecarSession::duplicates_for_connection(origin, session_cookie, threshold)
    })
}

#[tauri::command]
pub fn pause_worker_loop(app: AppHandle) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_connection(&app, |origin, session_cookie| {
        SidecarSession::pause_worker_loop_for_connection(origin, session_cookie)
    })
}

#[tauri::command]
pub fn resume_worker_loop(app: AppHandle) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_connection(&app, |origin, session_cookie| {
        SidecarSession::resume_worker_loop_for_connection(origin, session_cookie)
    })
}

#[tauri::command]
pub fn trigger_worker_loop(app: AppHandle) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_connection(&app, |origin, session_cookie| {
        SidecarSession::trigger_worker_loop_for_connection(origin, session_cookie)
    })
}

#[tauri::command]
pub fn run_runtime_health_check(app: AppHandle) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_connection(&app, |origin, session_cookie| {
        SidecarSession::run_runtime_health_check_for_connection(origin, session_cookie)
    })
}

#[tauri::command]
pub fn retry_failed_jobs(app: AppHandle) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_connection(&app, |origin, session_cookie| {
        SidecarSession::retry_failed_jobs_for_connection(origin, session_cookie)
    })
}

#[tauri::command]
pub fn get_pending_jobs(app: AppHandle) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_connection(&app, |origin, session_cookie| {
        SidecarSession::pending_jobs_for_connection(origin, session_cookie)
    })
}

#[tauri::command]
pub fn delete_pending_jobs(
    app: AppHandle,
    job_ids: Vec<String>,
) -> Result<serde_json::Value, SidecarError> {
    with_sidecar_connection(&app, |origin, session_cookie| {
        SidecarSession::delete_pending_jobs_for_connection(origin, session_cookie, &job_ids)
    })
}

#[tauri::command]
pub fn reveal_asset(
    app: AppHandle,
    asset_id: String,
    target: String,
    source_path: Option<String>,
) -> Result<(), SidecarError> {
    let target = AssetRevealTarget::parse(&target)?;
    let resolved_path = with_sidecar_connection(&app, |origin, session_cookie| {
        SidecarSession::resolve_asset_reveal_target_for_connection(
            origin,
            session_cookie,
            &asset_id,
            target,
            source_path.as_deref(),
        )
    })?;
    reveal_path_in_file_explorer(&resolved_path)
}

#[tauri::command]
pub fn open_log_directory(app: AppHandle) -> Result<(), SidecarError> {
    let logs_directory = with_sidecar_connection(&app, |origin, session_cookie| {
        SidecarSession::resolve_log_directory_for_connection(origin, session_cookie)
    })?;
    open_directory_in_file_explorer(&logs_directory)
}

/// Primary Clipboard Copy: stills publish `CF_DIBV5` plus a registered PNG
/// payload; GIFs publish a `CF_HDROP` reference to the managed `.gif` Library
/// Copy so the encoded animation is preserved. The WebView passes only the
/// Asset ID; the resolved path never leaves Rust.
#[tauri::command]
pub fn copy_asset_to_clipboard(app: AppHandle, asset_id: String) -> Result<(), SidecarError> {
    with_sidecar_connection(&app, |origin, session_cookie| {
        SidecarSession::copy_asset_with_writer(
            origin,
            session_cookie,
            &asset_id,
            &WindowsClipboardWriter,
        )
    })
}

/// Copy one raw Library Copy file reference as a `CF_HDROP` payload.
#[tauri::command]
pub fn copy_original_file(app: AppHandle, asset_id: String) -> Result<(), SidecarError> {
    with_sidecar_connection(&app, |origin, session_cookie| {
        SidecarSession::copy_original_file_with_writer(
            origin,
            session_cookie,
            &asset_id,
            &WindowsClipboardWriter,
        )
    })
}

/// Copy one multi-file `CF_HDROP` payload for the given Assets. IDs are
/// validated and de-duplicated under the existing batch limit.
#[tauri::command]
pub fn copy_original_files(app: AppHandle, asset_ids: Vec<String>) -> Result<(), SidecarError> {
    with_sidecar_connection(&app, |origin, session_cookie| {
        SidecarSession::copy_original_files_with_writer(
            origin,
            session_cookie,
            &asset_ids,
            &WindowsClipboardWriter,
        )
    })
}

fn start_selected_import(
    app: &AppHandle,
    start_indexing: bool,
) -> Result<serde_json::Value, SidecarError> {
    let selection = app
        .try_state::<ImportSelection>()
        .ok_or_else(|| SidecarError::new("MemeSort import selection is unavailable."))?;
    let source_folder = selection.selected_path()?;
    with_sidecar_session(app, |session| {
        let indexing_policy = if start_indexing {
            IndexingPolicy::Required
        } else {
            IndexingPolicy::Never
        };
        session.start_import_batch(vec![source_folder], indexing_policy)
    })
}

fn reveal_path_in_file_explorer(path: &Path) -> Result<(), SidecarError> {
    let metadata = fs::metadata(path).map_err(|error| {
        SidecarError::new(format!("Could not access the Asset reveal path: {error}"))
    })?;
    if !metadata.is_file() {
        return Err(SidecarError::new(
            "The resolved Asset reveal path is not a file.",
        ));
    }
    Command::new("explorer.exe")
        .arg(format!("/select,{}", path.display()))
        .spawn()
        .map_err(|error| SidecarError::new(format!("Could not open File Explorer: {error}")))?;
    Ok(())
}

fn open_directory_in_file_explorer(path: &Path) -> Result<(), SidecarError> {
    let metadata = fs::metadata(path).map_err(|error| {
        SidecarError::new(format!("Could not access the log directory: {error}"))
    })?;
    if !metadata.is_dir() {
        return Err(SidecarError::new(
            "The resolved log path is not a directory.",
        ));
    }
    Command::new("explorer.exe")
        .arg(path)
        .spawn()
        .map_err(|error| SidecarError::new(format!("Could not open File Explorer: {error}")))?;
    Ok(())
}

fn with_sidecar_session<T>(
    app: &AppHandle,
    operation: impl FnOnce(&SidecarSession) -> Result<T, SidecarError>,
) -> Result<T, SidecarError> {
    let state = app
        .try_state::<SidecarState>()
        .ok_or_else(|| SidecarError::new("MemeSort sidecar is not available."))?;
    let session = state
        .0
        .lock()
        .map_err(|_| SidecarError::new("MemeSort sidecar state is unavailable."))?;
    let session = session
        .as_ref()
        .ok_or_else(|| SidecarError::new("MemeSort sidecar has already stopped."))?;
    operation(session)
}

fn with_sidecar_connection<T>(
    app: &AppHandle,
    operation: impl FnOnce(&str, &str) -> Result<T, SidecarError>,
) -> Result<T, SidecarError> {
    let state = app
        .try_state::<SidecarState>()
        .ok_or_else(|| SidecarError::new("MemeSort sidecar is not available."))?;
    let (origin, session_cookie) = {
        let session = state
            .0
            .lock()
            .map_err(|_| SidecarError::new("MemeSort sidecar state is unavailable."))?;
        let session = session
            .as_ref()
            .ok_or_else(|| SidecarError::new("MemeSort sidecar has already stopped."))?;
        (session.origin.clone(), session.session_cookie.clone())
    };
    operation(&origin, &session_cookie)
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
        #[cfg(debug_assertions)]
        {
            let repository_root = repository_root()?;
            let library_root = default_library_root(&repository_root);
            let log_dir = library_root.join("logs");
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
            let portable_root = executable_root()?;
            let library_root = default_library_root(&portable_root);
            let log_dir = library_root.join("logs");
            let executable = portable_root.join("sidecar").join(SIDECAR_BINARY_NAME);
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
                    "--portable-root".into(),
                    portable_root.display().to_string(),
                ],
                working_directory: portable_root,
            })
        }
    }

    fn spawn(&self) -> Result<Child, SidecarError> {
        let mut command = Command::new(&self.executable);
        command
            .args(&self.arguments)
            .current_dir(&self.working_directory)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        command
            .spawn()
            .map_err(|error| SidecarError::new(format!("Could not start the sidecar: {error}")))
    }
}

#[cfg(not(debug_assertions))]
fn executable_root() -> Result<PathBuf, SidecarError> {
    env::current_exe()
        .map_err(|error| SidecarError::new(format!("Could not resolve MemeSort.exe: {error}")))?
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| SidecarError::new("Could not resolve the MemeSort portable root."))
}

#[cfg(debug_assertions)]
fn repository_root() -> Result<PathBuf, SidecarError> {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .map(Path::to_path_buf)
        .ok_or_else(|| SidecarError::new("Could not resolve the MemeSort repository root."))
}

fn default_library_root(portable_root: &Path) -> PathBuf {
    if let Some(root) = env::var_os("MEMESORT_LIBRARY_ROOT") {
        return PathBuf::from(root);
    }
    portable_root.join("MemeSortData").join("library")
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
    if !(200..300).contains(&response.status) {
        return Err(SidecarError::backend_response(
            response.status,
            &response.body,
        ));
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

fn validate_job_ids(job_ids: &[String]) -> Result<Vec<String>, SidecarError> {
    if job_ids.is_empty() || job_ids.len() > MAX_BATCH_ASSETS {
        return Err(SidecarError::new("Invalid MemeSort Pending Job selection."));
    }
    let mut validated = Vec::with_capacity(job_ids.len());
    for job_id in job_ids {
        let job_id = validate_asset_id(job_id)?;
        if !validated.contains(&job_id) {
            validated.push(job_id);
        }
    }
    Ok(validated)
}

pub(crate) fn validate_source_path(source_path: &str) -> Result<String, SidecarError> {
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

fn validate_library_selection_id(selection_id: &str) -> Result<String, SidecarError> {
    validate_asset_id(selection_id)
        .map_err(|_| SidecarError::new("Invalid MemeSort Library Import selection."))
}

fn validate_import_sources(sources: &[String]) -> Result<Vec<String>, SidecarError> {
    if sources.is_empty() || sources.len() > MAX_IMPORT_SOURCES {
        return Err(SidecarError::new(
            "An Import Batch requires 1 to 256 sources.",
        ));
    }
    sources
        .iter()
        .map(|source| validate_source_path(source))
        .collect()
}

fn validate_search_query(query: &str) -> Result<String, SidecarError> {
    if query.trim().is_empty()
        || query.len() > MAX_SEARCH_QUERY_BYTES
        || query.contains('\0')
        || query.contains('\r')
        || query.contains('\n')
    {
        return Err(SidecarError::new("Invalid MemeSort text Search Request."));
    }
    Ok(query.to_owned())
}

fn validate_duplicate_threshold(threshold: f64) -> Result<f64, SidecarError> {
    if !threshold.is_finite() || !(0.0..=1.0).contains(&threshold) {
        return Err(SidecarError::new(
            "Duplicate review threshold must be between 0 and 1.",
        ));
    }
    Ok(threshold)
}

fn percent_encode_query_value(value: &str) -> String {
    let mut encoded = String::with_capacity(value.len());
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~') {
            encoded.push(char::from(byte));
        } else {
            encoded.push('%');
            encoded.push_str(&format!("{byte:02X}"));
        }
    }
    encoded
}

fn media_error_response(error: SidecarError) -> http::Response<Vec<u8>> {
    let status = if error.detail.starts_with("Unknown MemeSort media route")
        || error.detail.starts_with("Invalid MemeSort media path")
        || error.detail.starts_with("MemeSort media only supports")
        || error.detail.starts_with("MemeSort media URLs cannot")
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
        fs,
        io::{Read, Write},
        net::TcpListener,
        path::{Path, PathBuf},
        thread,
        time::{Duration, Instant},
    };

    use super::{
        authenticated_get_json, authenticated_get_media, authenticated_post_json,
        default_library_root, logical_drag_position, managed_media_path, native_drag_counts,
        native_drag_summary, parse_handshake, parse_json_response, process_native_drag_event,
        validate_asset_id, validate_asset_ids, validate_duplicate_threshold,
        validate_import_sources, validate_library_paths, validate_search_query,
        validate_source_path, ApiRoute, AssetIdPayload, AssetRevealTarget, BatchAssetAction,
        BatchAssetActionPayload, EmptyPayload, ImportSelection, IndexingPolicy,
        LibraryImportSelection, LibrarySelectionEntry, LibrarySelectionOrigin, MutationRoute,
        NativeDragContext, NativeDragInput, NativeDragPhase, RemoveSourceRecordPayload,
        SearchImageSelection, SidecarSession, StartImportPayload,
    };
    use std::sync::Mutex;
    use tauri::http::{Method, Request, StatusCode};
    use tauri::{LogicalPosition, PhysicalPosition};

    use crate::clipboard::{
        parse_hdrop_payload, ClipboardPayload, FakeClipboardWrite, FakeClipboardWriter,
    };

    #[test]
    fn derives_the_default_library_from_the_portable_root() {
        let portable_root = Path::new(r"C:\MemeSort-portable");

        assert_eq!(
            default_library_root(portable_root),
            PathBuf::from(r"C:\MemeSort-portable\MemeSortData\library")
        );
    }

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
        let unknown_asset = parse_json_response(
            b"HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\n\r\n{\"error\":\"NotFound\",\"detail\":\"Asset was not found.\"}",
        )
        .expect_err("unknown Asset response must reach the WebView as a structured error");
        assert_eq!(unknown_asset.status, Some(404));
        assert_eq!(unknown_asset.error, "NotFound");
        assert_eq!(unknown_asset.detail, "Asset was not found.");
        assert!(!unknown_asset.retryable);

        let unavailable = parse_json_response(b"HTTP/1.1 503 Service Unavailable\r\n\r\n{}")
            .expect_err("unavailable sidecar response must be retryable");
        assert_eq!(unavailable.status, Some(503));
        assert!(unavailable.retryable);
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
                ("/api/resolve-log-directory", "{}"),
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
        authenticated_post_json(
            &origin,
            "memesort_session=test-token",
            MutationRoute::ResolveLogDirectory,
            &EmptyPayload {},
        )
        .expect("log directory request should succeed");
        server.join().expect("test server should finish");
    }

    #[test]
    fn resolves_a_managed_asset_reveal_target_only_through_the_fixed_route() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("reveal request should connect");
            let mut request = Vec::new();
            loop {
                let mut chunk = [0_u8; 1024];
                let size = stream.read(&mut chunk).expect("reveal request should read");
                request.extend_from_slice(&chunk[..size]);
                let Some(header_end) = request.windows(4).position(|window| window == b"\r\n\r\n")
                else {
                    continue;
                };
                let headers =
                    std::str::from_utf8(&request[..header_end]).expect("headers must be UTF-8");
                let content_length = headers
                    .lines()
                    .find_map(|line| line.strip_prefix("Content-Length: "))
                    .and_then(|value| value.parse::<usize>().ok())
                    .expect("reveal request should include a body length");
                if request.len() >= header_end + 4 + content_length {
                    break;
                }
            }
            let request = std::str::from_utf8(&request).expect("request must be UTF-8");
            assert!(request.starts_with("POST /api/resolve-asset-reveal-target HTTP/1.1"));
            assert!(request.contains("Cookie: memesort_session=test-token"));
            assert!(request.contains("\"asset_id\":\"123e4567-e89b-12d3-a456-426614174000\""));
            assert!(request.contains("\"target\":\"managed\""));
            assert!(!request.contains("source_path"));
            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"resolved_path\":\"C:/MemeSort/originals/asset.png\",\"target\":\"managed\"}",
                )
                .expect("reveal response should write");
        });
        let origin = format!("http://127.0.0.1:{port}");

        let path = SidecarSession::resolve_asset_reveal_target_for_connection(
            &origin,
            "memesort_session=test-token",
            "123e4567-e89b-12d3-a456-426614174000",
            AssetRevealTarget::Managed,
            None,
        )
        .expect("managed Asset reveal target should resolve");
        server.join().expect("test server should finish");

        assert_eq!(path, PathBuf::from("C:/MemeSort/originals/asset.png"));
    }

    #[test]
    fn rejects_invalid_batch_selection_and_source_record_inputs() {
        assert!(validate_asset_ids(&[]).is_err());
        assert!(validate_asset_ids(&["not-an-asset".to_owned()]).is_err());
        assert!(validate_import_sources(&[]).is_err());
        assert!(validate_import_sources(&[String::new()]).is_err());
        assert!(validate_import_sources(&["C:/Source/unsafe\npath.gif".to_owned()]).is_err());
        assert!(validate_import_sources(&["C:/Source/Memes".to_owned()]).is_ok());
        assert!(validate_source_path("").is_err());
        assert!(validate_source_path("C:/Source/unsafe\npath.gif").is_err());
        assert!(BatchAssetAction::parse("arbitrary-request").is_err());
    }

    #[test]
    fn forwards_a_uuid_scoped_text_search_and_its_cancellation() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            let (mut search_stream, _) = listener.accept().expect("search request should connect");
            let mut search_request = [0_u8; 1024];
            let size = search_stream
                .read(&mut search_request)
                .expect("search request should read");
            let search_request =
                std::str::from_utf8(&search_request[..size]).expect("request must be UTF-8");
            assert!(search_request.starts_with("GET /api/search?query=surprised%20reaction&top_k=18&request_id=123e4567-e89b-12d3-a456-426614174000 HTTP/1.1"));
            assert!(search_request.contains("Cookie: memesort_session=test-token"));
            search_stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"results\":[]}",
                )
                .expect("search response should write");
            drop(search_stream);

            let (mut cancel_stream, _) = listener.accept().expect("cancel request should connect");
            let mut cancel_request = Vec::new();
            loop {
                let mut chunk = [0_u8; 1024];
                let size = cancel_stream
                    .read(&mut chunk)
                    .expect("cancel request should read");
                cancel_request.extend_from_slice(&chunk[..size]);
                let Some(header_end) = cancel_request
                    .windows(4)
                    .position(|window| window == b"\r\n\r\n")
                else {
                    continue;
                };
                let headers = std::str::from_utf8(&cancel_request[..header_end])
                    .expect("headers must be UTF-8");
                let content_length = headers
                    .lines()
                    .find_map(|line| line.strip_prefix("Content-Length: "))
                    .and_then(|value| value.parse::<usize>().ok())
                    .expect("cancel request should include a body length");
                if cancel_request.len() >= header_end + 4 + content_length {
                    break;
                }
            }
            let cancel_request =
                std::str::from_utf8(&cancel_request).expect("request must be UTF-8");
            assert!(cancel_request.starts_with("POST /api/search/cancel HTTP/1.1"));
            assert!(cancel_request.contains("Cookie: memesort_session=test-token"));
            assert!(
                cancel_request.contains("\"request_id\":\"123e4567-e89b-12d3-a456-426614174000\"")
            );
            cancel_stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"cancelled\":true}")
                .expect("cancel response should write");
        });
        let origin = format!("http://127.0.0.1:{port}");
        let request_id = "123e4567-e89b-12d3-a456-426614174000";

        SidecarSession::search_text_for_connection(
            &origin,
            "memesort_session=test-token",
            "surprised reaction",
            request_id,
        )
        .expect("text search should succeed");
        SidecarSession::cancel_search_for_connection(
            &origin,
            "memesort_session=test-token",
            request_id,
        )
        .expect("search cancellation should succeed");
        server.join().expect("test server should finish");

        assert!(validate_search_query("\n").is_err());
        assert!(validate_search_query("valid query").is_ok());
    }

    #[test]
    fn forwards_a_native_selected_image_search_only_to_its_fixed_route() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("image search should connect");
            let mut request = Vec::new();
            loop {
                let mut chunk = [0_u8; 1024];
                let size = stream.read(&mut chunk).expect("image search should read");
                request.extend_from_slice(&chunk[..size]);
                let Some(header_end) = request.windows(4).position(|window| window == b"\r\n\r\n")
                else {
                    continue;
                };
                let headers =
                    std::str::from_utf8(&request[..header_end]).expect("headers must be UTF-8");
                let content_length = headers
                    .lines()
                    .find_map(|line| line.strip_prefix("Content-Length: "))
                    .and_then(|value| value.parse::<usize>().ok())
                    .expect("image search should include a body length");
                if request.len() >= header_end + 4 + content_length {
                    break;
                }
            }
            let request = std::str::from_utf8(&request).expect("request must be UTF-8");
            assert!(request.starts_with("POST /api/search-image HTTP/1.1"));
            assert!(request.contains("Cookie: memesort_session=test-token"));
            assert!(request.contains("\"path\":\"C:/Source/query.png\""));
            assert!(request.contains("\"top_k\":18"));
            assert!(request.contains("\"request_id\":\"123e4567-e89b-12d3-a456-426614174000\""));
            assert!(!request.contains("start_indexing"));
            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"results\":[]}",
                )
                .expect("image search response should write");
        });
        let origin = format!("http://127.0.0.1:{port}");
        let selection = SearchImageSelection::new();
        selection
            .replace(Some(PathBuf::from("C:/Source/query.png")))
            .expect("native image selection should be stored");

        SidecarSession::search_image_for_connection(
            &origin,
            "memesort_session=test-token",
            &selection
                .selected_path()
                .expect("selected image path should be available"),
            "123e4567-e89b-12d3-a456-426614174000",
        )
        .expect("image search should succeed");
        server.join().expect("test server should finish");
    }

    #[test]
    fn forwards_similar_asset_retrieval_only_to_its_fixed_route() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("similar request should connect");
            let mut request = [0_u8; 1024];
            let size = stream
                .read(&mut request)
                .expect("similar request should read");
            let request = std::str::from_utf8(&request[..size]).expect("request must be UTF-8");
            assert!(request.starts_with("GET /api/find-similar?asset_id=123e4567-e89b-12d3-a456-426614174000&top_k=18 HTTP/1.1"));
            assert!(request.contains("Cookie: memesort_session=test-token"));
            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"results\":[]}",
                )
                .expect("similar response should write");
        });
        let origin = format!("http://127.0.0.1:{port}");

        SidecarSession::find_similar_for_connection(
            &origin,
            "memesort_session=test-token",
            "123e4567-e89b-12d3-a456-426614174000",
        )
        .expect("similar retrieval should succeed");
        server.join().expect("test server should finish");
    }

    #[test]
    fn forwards_duplicate_review_only_with_a_bounded_threshold() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("duplicate request should connect");
            let mut request = [0_u8; 1024];
            let size = stream
                .read(&mut request)
                .expect("duplicate request should read");
            let request = std::str::from_utf8(&request[..size]).expect("request must be UTF-8");
            assert!(request.starts_with("GET /api/duplicates?threshold=0.92 HTTP/1.1"));
            assert!(request.contains("Cookie: memesort_session=test-token"));
            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"pairs\":[]}",
                )
                .expect("duplicate response should write");
        });
        let origin = format!("http://127.0.0.1:{port}");

        SidecarSession::duplicates_for_connection(&origin, "memesort_session=test-token", 0.92)
            .expect("duplicate review should succeed");
        server.join().expect("test server should finish");
        assert!(validate_duplicate_threshold(-0.01).is_err());
        assert!(validate_duplicate_threshold(1.01).is_err());
    }

    #[test]
    fn forwards_worker_loop_controls_only_to_fixed_routes() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            for path in [
                "/api/worker-loop/pause",
                "/api/worker-loop/resume",
                "/api/worker-loop/trigger",
                "/api/health",
                "/api/retry-failed-jobs",
            ] {
                let (mut stream, _) = listener.accept().expect("worker control should connect");
                let mut request = Vec::new();
                loop {
                    let mut chunk = [0_u8; 1024];
                    let size = stream.read(&mut chunk).expect("worker control should read");
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
                        .expect("worker control should include a body length");
                    if request.len() >= header_end + 4 + content_length {
                        break;
                    }
                }
                let request = std::str::from_utf8(&request).expect("request must be UTF-8");
                assert!(request.starts_with(&format!("POST {path} HTTP/1.1")));
                assert!(request.contains("Cookie: memesort_session=test-token"));
                stream
                    .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"running\":true}")
                    .expect("worker control response should write");
            }
        });
        let origin = format!("http://127.0.0.1:{port}");

        SidecarSession::pause_worker_loop_for_connection(&origin, "memesort_session=test-token")
            .expect("pause should succeed");
        SidecarSession::resume_worker_loop_for_connection(&origin, "memesort_session=test-token")
            .expect("resume should succeed");
        SidecarSession::trigger_worker_loop_for_connection(&origin, "memesort_session=test-token")
            .expect("trigger should succeed");
        SidecarSession::run_runtime_health_check_for_connection(
            &origin,
            "memesort_session=test-token",
        )
        .expect("health check should succeed");
        SidecarSession::retry_failed_jobs_for_connection(&origin, "memesort_session=test-token")
            .expect("retry failed Jobs should succeed");
        server.join().expect("test server should finish");
    }

    #[test]
    fn forwards_pending_job_management_only_to_fixed_routes() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            let (mut list_stream, _) = listener.accept().expect("pending jobs should connect");
            let mut list_request = [0_u8; 1024];
            let size = list_stream
                .read(&mut list_request)
                .expect("pending jobs should read");
            let list_request =
                std::str::from_utf8(&list_request[..size]).expect("request must be UTF-8");
            assert!(list_request.starts_with("GET /api/pending-jobs HTTP/1.1"));
            assert!(list_request.contains("Cookie: memesort_session=test-token"));
            list_stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"jobs\":[]}",
                )
                .expect("pending jobs response should write");
            drop(list_stream);

            let (mut delete_stream, _) = listener.accept().expect("pending delete should connect");
            let mut delete_request = Vec::new();
            loop {
                let mut chunk = [0_u8; 1024];
                let size = delete_stream
                    .read(&mut chunk)
                    .expect("pending delete should read");
                delete_request.extend_from_slice(&chunk[..size]);
                let Some(header_end) = delete_request
                    .windows(4)
                    .position(|window| window == b"\r\n\r\n")
                else {
                    continue;
                };
                let headers = std::str::from_utf8(&delete_request[..header_end])
                    .expect("headers must be UTF-8");
                let content_length = headers
                    .lines()
                    .find_map(|line| line.strip_prefix("Content-Length: "))
                    .and_then(|value| value.parse::<usize>().ok())
                    .expect("pending delete should include a body length");
                if delete_request.len() >= header_end + 4 + content_length {
                    break;
                }
            }
            let delete_request =
                std::str::from_utf8(&delete_request).expect("request must be UTF-8");
            assert!(delete_request.starts_with("POST /api/pending-jobs/delete HTTP/1.1"));
            assert!(delete_request.contains("Cookie: memesort_session=test-token"));
            assert!(
                delete_request.contains("\"job_ids\":[\"123e4567-e89b-12d3-a456-426614174000\"]")
            );
            delete_stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"deleted_job_ids\":[]}")
                .expect("pending delete response should write");
        });
        let origin = format!("http://127.0.0.1:{port}");
        let job_ids = vec!["123e4567-e89b-12d3-a456-426614174000".to_owned()];

        SidecarSession::pending_jobs_for_connection(&origin, "memesort_session=test-token")
            .expect("pending jobs should succeed");
        SidecarSession::delete_pending_jobs_for_connection(
            &origin,
            "memesort_session=test-token",
            &job_ids,
        )
        .expect("pending delete should succeed");
        server.join().expect("test server should finish");
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
    fn validates_library_native_selections_against_existing_regular_entries() {
        let root = std::env::temp_dir().join(format!(
            "memesort-library-selection-validation-{}",
            std::process::id()
        ));
        let file = root.join("sample.png");
        let folder = root.join("folder");
        fs::create_dir_all(&folder).expect("test folder should be created");
        fs::write(&file, b"test").expect("test file should be written");

        assert!(validate_library_paths(LibrarySelectionOrigin::Files, &[file]).is_ok());
        assert!(validate_library_paths(
            LibrarySelectionOrigin::Folder,
            std::slice::from_ref(&folder)
        )
        .is_ok());
        assert!(validate_library_paths(LibrarySelectionOrigin::Files, &[folder]).is_err());
        assert!(validate_library_paths(
            LibrarySelectionOrigin::Folder,
            &[root.join("missing-folder")]
        )
        .is_err());
        assert!(validate_library_paths(LibrarySelectionOrigin::Files, &[]).is_err());

        fs::remove_dir_all(&root).expect("test root should be removed");
    }

    #[test]
    fn stores_library_selections_as_distinct_consumed_entries() {
        let selection = LibraryImportSelection::new();
        let root = std::env::temp_dir().join(format!(
            "memesort-library-selection-registry-{}",
            std::process::id()
        ));
        let first = root.join("first.png");
        let second = root.join("second.gif");
        let folder = root.join("folder");
        fs::create_dir_all(&folder).expect("test folder should be created");
        fs::write(&first, b"first").expect("first test file should be written");
        fs::write(&second, b"second").expect("second test file should be written");

        let files = selection
            .store(
                LibrarySelectionOrigin::Files,
                vec![first.clone(), second.clone()],
            )
            .expect("file selection should be stored");
        let folder = selection
            .store(LibrarySelectionOrigin::Folder, vec![folder])
            .expect("folder selection should be stored");

        assert_ne!(files.selection_id, folder.selection_id);
        assert_eq!(files.count, 2);
        assert_eq!(folder.count, 1);

        let taken = selection
            .take(&files.selection_id)
            .expect("file selection should be consumed");
        assert_eq!(taken.origin, LibrarySelectionOrigin::Files);
        assert_eq!(taken.paths.len(), 2);
        assert!(selection.take(&files.selection_id).is_err());

        fs::remove_dir_all(&root).expect("test root should be removed");
    }

    #[test]
    fn expires_and_evicts_old_library_selections() {
        let selection = LibraryImportSelection::new();
        {
            let mut entries = selection
                .0
                .lock()
                .expect("Library selection should be lockable");
            for index in 0..17 {
                entries.push(LibrarySelectionEntry {
                    id: format!("selection-{index}"),
                    origin: LibrarySelectionOrigin::Files,
                    paths: Vec::new(),
                    created_at: Instant::now(),
                });
            }
            entries.push(LibrarySelectionEntry {
                id: "expired-selection".to_owned(),
                origin: LibrarySelectionOrigin::Folder,
                paths: Vec::new(),
                created_at: Instant::now() - Duration::from_secs(61),
            });
        }

        assert!(selection.take("expired-selection").is_err());

        let root = std::env::temp_dir().join(format!(
            "memesort-library-selection-cap-{}",
            std::process::id()
        ));
        fs::create_dir_all(&root).expect("test root should be created");
        let file = root.join("sample.png");
        fs::write(&file, b"sample").expect("test file should be written");
        let summary = selection
            .store(LibrarySelectionOrigin::Files, vec![file])
            .expect("new selection should be stored");

        let entries = selection
            .0
            .lock()
            .expect("Library selection should be lockable");
        assert_eq!(entries.len(), 16);
        assert!(entries.iter().any(|entry| entry.id == summary.selection_id));
        drop(entries);
        fs::remove_dir_all(&root).expect("test root should be removed");
    }

    #[test]
    fn forwards_import_controls_only_to_fixed_routes_with_the_private_cookie() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            for path in [
                "/api/import/start",
                "/api/import/pause",
                "/api/import/resume",
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
                if path == "/api/import/start" {
                    assert!(request.contains("\"sources\":[\"C:/Source/Memes\"]"));
                    assert!(request.contains("\"indexing_policy\":\"required\""));
                } else {
                    assert!(request.contains("{}"));
                }
                let response: &[u8] = if path == "/api/import/start" {
                    b"HTTP/1.1 202 Accepted\r\nContent-Type: application/json\r\n\r\n{\"status\":\"running\"}"
                } else {
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"status\":\"running\"}"
                };
                stream
                    .write_all(response)
                    .expect("import response should write");
            }
        });
        let origin = format!("http://127.0.0.1:{port}");

        authenticated_post_json(
            &origin,
            "memesort_session=test-token",
            MutationRoute::StartImport,
            &StartImportPayload {
                sources: vec!["C:/Source/Memes".to_owned()],
                indexing_policy: IndexingPolicy::Required.as_api_value(),
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
    fn forwards_import_status_only_to_the_fixed_read_route_with_the_private_cookie() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("status request should connect");
            let mut request = Vec::new();
            loop {
                let mut chunk = [0_u8; 1024];
                let size = stream.read(&mut chunk).expect("status request should read");
                request.extend_from_slice(&chunk[..size]);
                if request.windows(4).any(|window| window == b"\r\n\r\n") {
                    break;
                }
            }
            let request = std::str::from_utf8(&request).expect("request must be UTF-8");
            assert!(request.starts_with("GET /api/import HTTP/1.1"));
            assert!(request.contains("Cookie: memesort_session=test-token"));
            assert!(!request.contains("path"));
            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"batch_id\":null,\"status\":\"idle\",\"running\":false}",
                )
                .expect("status response should write");
        });
        let origin = format!("http://127.0.0.1:{port}");

        let status = authenticated_get_json(
            &origin,
            "memesort_session=test-token",
            ApiRoute::ImportStatus,
        )
        .expect("import status should be readable");

        assert_eq!(
            status.get("status").and_then(serde_json::Value::as_str),
            Some("idle")
        );
        server.join().expect("test server should finish");
    }

    #[test]
    fn forwards_library_imports_only_to_the_fixed_if_ready_route() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("library import should connect");
            let mut request = Vec::new();
            loop {
                let mut chunk = [0_u8; 1024];
                let size = stream.read(&mut chunk).expect("library import should read");
                request.extend_from_slice(&chunk[..size]);
                let Some(header_end) = request.windows(4).position(|window| window == b"\r\n\r\n")
                else {
                    continue;
                };
                let headers =
                    std::str::from_utf8(&request[..header_end]).expect("headers must be UTF-8");
                let content_length = headers
                    .lines()
                    .find_map(|line| line.strip_prefix("Content-Length: "))
                    .and_then(|value| value.parse::<usize>().ok())
                    .expect("library import should include a body length");
                if request.len() >= header_end + 4 + content_length {
                    break;
                }
            }
            let request = std::str::from_utf8(&request).expect("request must be UTF-8");
            assert!(request.starts_with("POST /api/import/start HTTP/1.1"));
            assert!(request.contains("Cookie: memesort_session=test-token"));
            assert!(request.contains("\"sources\":[\"C:/Source/Memes\"]"));
            assert!(request.contains("\"indexing_policy\":\"if-ready\""));
            stream
                .write_all(
                    b"HTTP/1.1 202 Accepted\r\nContent-Type: application/json\r\n\r\n{\"status\":\"running\"}",
                )
                .expect("library import response should write");
        });
        let origin = format!("http://127.0.0.1:{port}");

        authenticated_post_json(
            &origin,
            "memesort_session=test-token",
            MutationRoute::StartImport,
            &StartImportPayload {
                sources: vec!["C:/Source/Memes".to_owned()],
                indexing_policy: IndexingPolicy::IfReady.as_api_value(),
            },
        )
        .expect("library import should start");
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

    fn native_drop_fixture(name: &str) -> (PathBuf, PathBuf, PathBuf) {
        let root = std::env::temp_dir().join(format!("{name}-{}", std::process::id()));
        let file = root.join("drop-file.png");
        let folder = root.join("drop folder");
        fs::create_dir_all(&folder).expect("drop fixture folder should be created");
        fs::write(&file, b"drop").expect("drop fixture file should be written");
        (root, file, folder)
    }

    #[test]
    fn converts_physical_drag_coordinates_at_windows_display_scales() {
        let physical = PhysicalPosition::new(1600.0, 1000.0);
        for (scale_factor, expected) in [
            (1.0, LogicalPosition::new(1600.0, 1000.0)),
            (1.25, LogicalPosition::new(1280.0, 800.0)),
            (
                1.5,
                LogicalPosition::new(1_066.666_666_666_666_7, 666.666_666_666_666_7),
            ),
            (2.0, LogicalPosition::new(800.0, 500.0)),
        ] {
            let logical = logical_drag_position(physical, scale_factor);
            assert!(
                (logical.x - expected.x).abs() < 1e-9 && (logical.y - expected.y).abs() < 1e-9,
                "scale {scale_factor} must convert {physical:?} to {expected:?}, got {logical:?}"
            );
        }
    }

    #[test]
    fn summarizes_path_free_native_drag_previews() {
        let (root, file, folder) = native_drop_fixture("memesort-native-drag-summary");
        let paths = vec![file.clone(), folder.clone()];
        let position = PhysicalPosition::new(300.0, 240.0);

        let (enter, context) =
            native_drag_summary(NativeDragPhase::Enter, Some(&paths), None, position, 1.5);
        assert_eq!(enter.phase, NativeDragPhase::Enter);
        let counts = native_drag_counts(&paths);
        assert_eq!(counts.file_count, 1);
        assert_eq!(counts.folder_count, 1);
        assert!(enter.accepted);
        assert!(enter.drop_id.is_none());
        assert!((enter.x - 200.0).abs() < 1e-9);
        assert!((enter.y - 160.0).abs() < 1e-9);

        let payload = serde_json::to_string(&enter).expect("summary should serialize");
        assert!(payload.contains("\"file_count\":1"));
        assert!(payload.contains("\"accepted\":true"));
        assert!(!payload.contains("drop-file"));
        assert!(!payload.contains("drop folder"));
        assert!(!payload.to_lowercase().contains("path"));

        let (over, kept) = native_drag_summary(
            NativeDragPhase::Over,
            None,
            context,
            PhysicalPosition::new(320.0, 260.0),
            1.5,
        );
        assert_eq!((over.file_count, over.folder_count), (1, 1));
        assert!(over.accepted);
        assert_eq!(kept, context);

        let (leave, cleared) = native_drag_summary(
            NativeDragPhase::Leave,
            None,
            context,
            PhysicalPosition::new(320.0, 260.0),
            1.5,
        );
        assert!(!leave.accepted);
        assert_eq!((leave.file_count, leave.folder_count), (0, 0));
        assert!(cleared.is_none());

        let (invalid_drop, no_context) = native_drag_summary(
            NativeDragPhase::Drop,
            Some(&[root.join("missing.png")]),
            context,
            position,
            1.5,
        );
        assert!(!invalid_drop.accepted);
        assert!(invalid_drop.drop_id.is_none());
        assert!(no_context.is_none());

        fs::remove_dir_all(&root).expect("drop fixture should be removed");
    }

    #[test]
    fn processes_native_drops_into_one_time_ids_without_setup_changes() {
        let (root, file, folder) = native_drop_fixture("memesort-native-drag-process");
        let paths = vec![file.clone(), folder.clone()];
        let selections = LibraryImportSelection::new();
        let setup_selection = ImportSelection::new();
        setup_selection
            .replace(Some(PathBuf::from("C:/Source/Setup-folder")))
            .expect("Setup selection should be stored");
        let slot = Mutex::new(None::<NativeDragContext>);
        let mut emitted: Vec<(NativeDragPhase, bool, Option<String>)> = Vec::new();

        process_native_drag_event(
            &selections,
            &slot,
            2.0,
            NativeDragInput::Enter(paths.clone(), PhysicalPosition::new(400.0, 300.0)),
            |summary| {
                emitted.push((summary.phase, summary.accepted, summary.drop_id.clone()));
            },
        );
        assert_eq!(emitted.len(), 1);
        assert!(emitted[0].1);
        assert!(emitted[0].2.is_none());
        assert!(slot.lock().expect("drag context").is_some());

        process_native_drag_event(
            &selections,
            &slot,
            2.0,
            NativeDragInput::Drop(paths.clone(), PhysicalPosition::new(410.0, 310.0)),
            |summary| {
                emitted.push((summary.phase, summary.accepted, summary.drop_id.clone()));
            },
        );
        assert!(emitted[1].1, "a valid mixed drop must be accepted");
        let drop_id = emitted[1]
            .2
            .clone()
            .expect("validated drop should carry an id");

        let taken = selections
            .take(&drop_id)
            .expect("drop selection should be consumable once");
        assert_eq!(taken.origin, LibrarySelectionOrigin::Drop);
        assert_eq!(taken.paths.len(), 2);
        assert!(
            selections.take(&drop_id).is_err(),
            "duplicate drop events for one ID cannot start a second import"
        );

        assert_eq!(
            setup_selection
                .selected_path()
                .expect("Setup selection should remain"),
            "C:/Source/Setup-folder",
            "native drops must not alter Setup selection"
        );

        let remaining = selections.0.lock().expect("registry").len();
        process_native_drag_event(
            &selections,
            &slot,
            1.0,
            NativeDragInput::Drop(
                vec![root.join("vanished.png")],
                PhysicalPosition::new(0.0, 0.0),
            ),
            |summary| {
                emitted.push((summary.phase, summary.accepted, summary.drop_id.clone()));
            },
        );
        assert!(!emitted[2].1);
        assert!(emitted[2].2.is_none());
        assert_eq!(
            selections.0.lock().expect("registry").len(),
            remaining,
            "invalid drops must not store selections"
        );
        assert!(slot.lock().expect("drag context").is_none());

        fs::remove_dir_all(&root).expect("drop fixture should be removed");
    }

    #[test]
    fn keeps_chooser_origins_strict_while_drop_origin_accepts_mixtures() {
        let (root, file, folder) = native_drop_fixture("memesort-native-drop-origin");
        let mixed = vec![file.clone(), folder.clone()];

        assert!(
            validate_library_paths(LibrarySelectionOrigin::Files, std::slice::from_ref(&file))
                .is_ok()
        );
        assert!(validate_library_paths(
            LibrarySelectionOrigin::Files,
            std::slice::from_ref(&folder)
        )
        .is_err());
        assert!(validate_library_paths(
            LibrarySelectionOrigin::Folder,
            std::slice::from_ref(&folder)
        )
        .is_ok());
        assert!(validate_library_paths(
            LibrarySelectionOrigin::Folder,
            std::slice::from_ref(&file)
        )
        .is_err());
        assert!(validate_library_paths(LibrarySelectionOrigin::Drop, &mixed).is_ok());
        assert!(
            validate_library_paths(LibrarySelectionOrigin::Drop, &[root.join("missing.png")])
                .is_err()
        );

        fs::remove_dir_all(&root).expect("drop fixture should be removed");
    }

    const CLIPBOARD_ASSET_A: &str = "123e4567-e89b-12d3-a456-426614174000";
    const CLIPBOARD_ASSET_B: &str = "223e4567-e89b-12d3-a456-426614174001";
    const CLIPBOARD_COOKIE: &str = "memesort_session=test-token";

    fn clipboard_fixture_root(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!("{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).expect("clipboard fixture root should be created");
        root
    }

    fn write_clipboard_png(path: &Path, width: u32, height: u32) {
        let frame: image::ImageBuffer<image::Rgba<u8>, Vec<u8>> =
            image::ImageBuffer::from_pixel(width, height, image::Rgba([18, 120, 220, 255]));
        image::DynamicImage::ImageRgba8(frame)
            .save(path)
            .expect("png clipboard fixture should be written");
    }

    fn write_clipboard_photo(path: &Path, width: u32, height: u32) {
        let frame: image::ImageBuffer<image::Rgba<u8>, Vec<u8>> =
            image::ImageBuffer::from_pixel(width, height, image::Rgba([90, 140, 60, 255]));
        // The image format follows the file extension, so `.jpg` and `.webp`
        // fixtures exercise their real decoders end to end.
        image::DynamicImage::ImageRgba8(frame)
            .save(path)
            .expect("photo clipboard fixture should be written");
    }

    fn write_clipboard_bmp(path: &Path, width: u32, height: u32) {
        use std::io::Cursor;
        let frame: image::ImageBuffer<image::Rgba<u8>, Vec<u8>> =
            image::ImageBuffer::from_pixel(width, height, image::Rgba([220, 40, 90, 255]));
        let mut bytes = Vec::new();
        image::DynamicImage::ImageRgba8(frame)
            .write_to(&mut Cursor::new(&mut bytes), image::ImageFormat::Bmp)
            .expect("bmp clipboard fixture should encode");
        fs::write(path, bytes).expect("bmp clipboard fixture should be written");
    }

    fn write_clipboard_gif(path: &Path) {
        // Minimal 1x1 GIF89a. The static builder has no GIF decoder, so this
        // also proves GIF Library Copies never flatten through the image path.
        fs::write(
            path,
            vec![
                0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x01, 0x00, 0x01, 0x00, 0x80, 0x00,
                0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0x21, 0xF9, 0x04, 0x01, 0x00,
                0x00, 0x00, 0x00, 0x2C, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00,
                0x00, 0x02, 0x02, 0x44, 0x01, 0x00, 0x3B,
            ],
        )
        .expect("gif clipboard fixture should be written");
    }

    fn read_clipboard_request(stream: &mut std::net::TcpStream) -> String {
        let mut request = Vec::new();
        loop {
            let mut chunk = [0_u8; 1024];
            let size = stream.read(&mut chunk).expect("request should read");
            if size == 0 {
                break;
            }
            request.extend_from_slice(&chunk[..size]);
            let Some(header_end) = request.windows(4).position(|window| window == b"\r\n\r\n")
            else {
                continue;
            };
            let headers =
                std::str::from_utf8(&request[..header_end]).expect("headers must be UTF-8");
            let content_length = headers
                .lines()
                .find_map(|line| line.strip_prefix("Content-Length: "))
                .and_then(|value| value.parse::<usize>().ok())
                .unwrap_or(0);
            if request.len() >= header_end + 4 + content_length {
                break;
            }
        }
        String::from_utf8(request).expect("request must be UTF-8")
    }

    /// Fake the fixed managed reveal-target route, serving one resolved path
    /// per connection in order. Every request must target the managed route.
    fn spawn_managed_copy_server(paths: Vec<PathBuf>) -> (String, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let server = thread::spawn(move || {
            for path in paths {
                let (mut stream, _) = listener.accept().expect("resolve request should connect");
                let request = read_clipboard_request(&mut stream);
                assert!(request.starts_with("POST /api/resolve-asset-reveal-target HTTP/1.1"));
                assert!(request.contains("Cookie: memesort_session=test-token"));
                assert!(request.contains("\"target\":\"managed\""));
                assert!(!request.contains("source_path"));
                let body = serde_json::json!({
                    "resolved_path": path.to_str().expect("fixture path must be Unicode"),
                    "target": "managed",
                })
                .to_string();
                stream
                    .write_all(
                        format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{body}")
                            .as_bytes(),
                    )
                    .expect("resolve response should write");
            }
        });
        (format!("http://127.0.0.1:{port}"), server)
    }

    #[test]
    fn rejects_invalid_clipboard_ids_before_connecting() {
        let fake = FakeClipboardWriter::new();
        let closed = "http://127.0.0.1:1";

        for invalid in ["", "not-a-uuid", "../library.sqlite"] {
            let error = SidecarSession::copy_asset_with_writer(
                closed,
                CLIPBOARD_COOKIE,
                invalid,
                &fake,
            )
            .expect_err("invalid Asset ID must be rejected");
            assert!(
                error.to_string().contains("Invalid MemeSort Asset"),
                "unexpected error: {error}"
            );
            let error = SidecarSession::copy_original_file_with_writer(
                closed,
                CLIPBOARD_COOKIE,
                invalid,
                &fake,
            )
            .expect_err("invalid Asset ID must be rejected");
            assert!(error.to_string().contains("Invalid MemeSort Asset"));
        }

        let error = SidecarSession::copy_original_files_with_writer(
            closed,
            CLIPBOARD_COOKIE,
            &[],
            &fake,
        )
        .expect_err("empty batch must be rejected");
        assert!(error.to_string().contains("Invalid MemeSort Asset selection"));

        let oversized = vec![CLIPBOARD_ASSET_A.to_owned(); 1_001];
        let error = SidecarSession::copy_original_files_with_writer(
            closed,
            CLIPBOARD_COOKIE,
            &oversized,
            &fake,
        )
        .expect_err("oversized batch must be rejected");
        assert!(error.to_string().contains("Invalid MemeSort Asset selection"));

        assert_eq!(
            fake.write_count(),
            0,
            "ID validation must precede any clipboard mutation"
        );
    }

    #[test]
    fn resolves_a_static_copy_only_through_the_fixed_managed_route() {
        let root = clipboard_fixture_root("memesort-clipboard-managed-png");
        let still = root.join("still.png");
        write_clipboard_png(&still, 4, 3);
        let (origin, server) = spawn_managed_copy_server(vec![still.clone()]);

        let payload = SidecarSession::prepare_copy_asset_for_connection(
            &origin,
            CLIPBOARD_COOKIE,
            CLIPBOARD_ASSET_A,
        )
        .expect("static copy should prepare");
        server.join().expect("test server should finish");

        match payload {
            ClipboardPayload::StaticImage(image) => {
                assert_eq!((image.width, image.height), (4, 3));
                assert_eq!(image.dibv5.len(), 124 + 4 * 3 * 4);
                assert_eq!(&image.png[0..8], &[137, 80, 78, 71, 13, 10, 26, 10]);
            }
            ClipboardPayload::FileDrop { .. } => panic!("PNG must publish image payloads"),
        }

        fs::remove_dir_all(&root).expect("fixture should be removed");
    }

    #[test]
    fn builds_a_bmp_copy_without_changing_dimensions() {
        let root = clipboard_fixture_root("memesort-clipboard-managed-bmp");
        let still = root.join("still.bmp");
        write_clipboard_bmp(&still, 5, 2);
        let (origin, server) = spawn_managed_copy_server(vec![still.clone()]);
        let fake = FakeClipboardWriter::new();

        SidecarSession::copy_asset_with_writer(&origin, CLIPBOARD_COOKIE, CLIPBOARD_ASSET_A, &fake)
            .expect("bmp copy should succeed");
        server.join().expect("test server should finish");

        assert_eq!(fake.write_count(), 1);
        let writes = fake.writes.lock().expect("fake clipboard lock");
        match &writes[0] {
            FakeClipboardWrite::StaticImage { dibv5, png } => {
                assert_eq!(dibv5.len(), 124 + 5 * 2 * 4);
                assert_eq!(i32::from_le_bytes(dibv5[4..8].try_into().unwrap()), 5);
                assert_eq!(i32::from_le_bytes(dibv5[8..12].try_into().unwrap()), 2);
                let decoded = image::load_from_memory(png).expect("png round-trip");
                assert_eq!((decoded.width(), decoded.height()), (5, 2));
            }
            FakeClipboardWrite::FileDrop { .. } => panic!("BMP must publish image payloads"),
        }

        fs::remove_dir_all(&root).expect("fixture should be removed");
    }

    #[test]
    fn resolves_jpeg_and_webp_copies_through_the_fixed_route() {
        let root = clipboard_fixture_root("memesort-clipboard-managed-photo");
        let photo = root.join("photo.jpg");
        let sticker = root.join("sticker.webp");
        write_clipboard_photo(&photo, 6, 4);
        write_clipboard_photo(&sticker, 7, 5);
        let (origin, server) = spawn_managed_copy_server(vec![photo.clone(), sticker.clone()]);
        let fake = FakeClipboardWriter::new();

        SidecarSession::copy_asset_with_writer(&origin, CLIPBOARD_COOKIE, CLIPBOARD_ASSET_A, &fake)
            .expect("jpeg copy should succeed");
        SidecarSession::copy_asset_with_writer(&origin, CLIPBOARD_COOKIE, CLIPBOARD_ASSET_B, &fake)
            .expect("webp copy should succeed");
        server.join().expect("test server should finish");

        assert_eq!(fake.write_count(), 2);
        let writes = fake.writes.lock().expect("fake clipboard lock");
        for (write, expected) in writes.iter().zip([(6, 4), (7, 5)]) {
            match write {
                FakeClipboardWrite::StaticImage { dibv5, png } => {
                    assert_eq!(dibv5.len() as u32, 124 + expected.0 * expected.1 * 4);
                    assert_eq!(
                        i32::from_le_bytes(dibv5[4..8].try_into().unwrap()),
                        expected.0 as i32
                    );
                    assert_eq!(
                        i32::from_le_bytes(dibv5[8..12].try_into().unwrap()),
                        expected.1 as i32
                    );
                    let decoded = image::load_from_memory(png).expect("png round-trip");
                    assert_eq!((decoded.width(), decoded.height()), (expected.0, expected.1));
                }
                FakeClipboardWrite::FileDrop { .. } => {
                    panic!("JPEG/WebP must publish image payloads")
                }
            }
        }

        fs::remove_dir_all(&root).expect("fixture should be removed");
    }

    #[test]
    fn publishes_gif_copies_as_hdrop_without_decoding() {
        let root = clipboard_fixture_root("memesort-clipboard-managed-gif");
        let animated = root.join("animated.gif");
        write_clipboard_gif(&animated);
        let (origin, server) = spawn_managed_copy_server(vec![animated.clone()]);
        let fake = FakeClipboardWriter::new();

        SidecarSession::copy_asset_with_writer(&origin, CLIPBOARD_COOKIE, CLIPBOARD_ASSET_A, &fake)
            .expect("gif copy should succeed");
        server.join().expect("test server should finish");

        assert_eq!(fake.write_count(), 1);
        let writes = fake.writes.lock().expect("fake clipboard lock");
        match &writes[0] {
            FakeClipboardWrite::FileDrop { hdrop } => {
                assert_eq!(
                    parse_hdrop_payload(hdrop).expect("hdrop round-trip"),
                    vec![animated]
                );
            }
            FakeClipboardWrite::StaticImage { .. } => {
                panic!("GIF must keep its animation via a file reference")
            }
        }

        fs::remove_dir_all(&root).expect("fixture should be removed");
    }

    #[test]
    fn copies_single_and_multi_original_files_as_hdrop() {
        let root = clipboard_fixture_root("memesort-clipboard-original-files");
        let first = root.join("first.png");
        let second = root.join("second.png");
        write_clipboard_png(&first, 2, 2);
        write_clipboard_png(&second, 2, 2);

        let (single_origin, single_server) =
            spawn_managed_copy_server(vec![first.clone()]);
        let fake = FakeClipboardWriter::new();
        SidecarSession::copy_original_file_with_writer(
            &single_origin,
            CLIPBOARD_COOKIE,
            CLIPBOARD_ASSET_A,
            &fake,
        )
        .expect("single file copy should succeed");
        single_server.join().expect("test server should finish");
        assert_eq!(fake.write_count(), 1);
        {
            let writes = fake.writes.lock().expect("fake clipboard lock");
            match &writes[0] {
                FakeClipboardWrite::FileDrop { hdrop } => {
                    assert_eq!(parse_hdrop_payload(hdrop).expect("round-trip"), vec![first.clone()]);
                }
                FakeClipboardWrite::StaticImage { .. } => {
                    panic!("original-file copy must be a file reference")
                }
            }
        }

        // Duplicated IDs resolve once: three inputs, two connections, two paths.
        let (multi_origin, multi_server) =
            spawn_managed_copy_server(vec![first.clone(), second.clone()]);
        let fake = FakeClipboardWriter::new();
        SidecarSession::copy_original_files_with_writer(
            &multi_origin,
            CLIPBOARD_COOKIE,
            &[
                CLIPBOARD_ASSET_A.to_owned(),
                CLIPBOARD_ASSET_A.to_owned(),
                CLIPBOARD_ASSET_B.to_owned(),
            ],
            &fake,
        )
        .expect("multi file copy should succeed");
        multi_server.join().expect("test server should finish");
        assert_eq!(fake.write_count(), 1);
        let writes = fake.writes.lock().expect("fake clipboard lock");
        match &writes[0] {
            FakeClipboardWrite::FileDrop { hdrop } => {
                assert_eq!(
                    parse_hdrop_payload(hdrop).expect("round-trip"),
                    vec![first, second]
                );
            }
            FakeClipboardWrite::StaticImage { .. } => {
                panic!("multi-file copy must be one file reference payload")
            }
        }

        fs::remove_dir_all(&root).expect("fixture should be removed");
    }

    #[test]
    fn fails_clipboard_preflight_before_mutation() {
        let fake = FakeClipboardWriter::new();

        // Missing Library Copy.
        let root = clipboard_fixture_root("memesort-clipboard-preflight");
        let missing = root.join("missing.png");
        let (origin, server) = spawn_managed_copy_server(vec![missing]);
        assert!(SidecarSession::copy_asset_with_writer(
            &origin,
            CLIPBOARD_COOKIE,
            CLIPBOARD_ASSET_A,
            &fake,
        )
        .is_err());
        server.join().expect("test server should finish");

        // Directory instead of a file.
        let folder = root.join("folder");
        fs::create_dir_all(&folder).expect("folder fixture should be created");
        let (origin, server) = spawn_managed_copy_server(vec![folder]);
        assert!(SidecarSession::copy_original_file_with_writer(
            &origin,
            CLIPBOARD_COOKIE,
            CLIPBOARD_ASSET_A,
            &fake,
        )
        .is_err());
        server.join().expect("test server should finish");

        // Corrupt still bytes.
        let corrupt = root.join("corrupt.png");
        fs::write(&corrupt, b"not an image").expect("corrupt fixture should be written");
        let (origin, server) = spawn_managed_copy_server(vec![corrupt]);
        assert!(SidecarSession::copy_asset_with_writer(
            &origin,
            CLIPBOARD_COOKIE,
            CLIPBOARD_ASSET_A,
            &fake,
        )
        .is_err());
        server.join().expect("test server should finish");

        // Unsupported media type.
        let notes = root.join("notes.tiff");
        fs::write(&notes, b"unsupported").expect("unsupported fixture should be written");
        let (origin, server) = spawn_managed_copy_server(vec![notes]);
        assert!(SidecarSession::copy_asset_with_writer(
            &origin,
            CLIPBOARD_COOKIE,
            CLIPBOARD_ASSET_A,
            &fake,
        )
        .is_err());
        server.join().expect("test server should finish");

        // Unknown Asset reported by the sidecar.
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener should bind");
        let port = listener.local_addr().expect("listener address").port();
        let unknown = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("request should connect");
            let _ = read_clipboard_request(&mut stream);
            stream
                .write_all(
                    b"HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\n\r\n{\"error\":\"NotFound\",\"detail\":\"Asset was not found.\"}",
                )
                .expect("error response should write");
        });
        assert!(SidecarSession::copy_original_file_with_writer(
            &format!("http://127.0.0.1:{port}"),
            CLIPBOARD_COOKIE,
            CLIPBOARD_ASSET_A,
            &fake,
        )
        .is_err());
        unknown.join().expect("test server should finish");

        // The WebView never learns a resolved path, even on failure.
        let error = SidecarSession::copy_original_file_with_writer(
            &format!("http://127.0.0.1:{port}"),
            CLIPBOARD_COOKIE,
            CLIPBOARD_ASSET_A,
            &fake,
        )
        .expect_err("unknown Asset must fail");
        assert!(!error.to_string().contains(&root.display().to_string()));

        assert_eq!(
            fake.write_count(),
            0,
            "every preflight failure must precede clipboard mutation"
        );
        fs::remove_dir_all(&root).expect("fixture should be removed");
    }
}
