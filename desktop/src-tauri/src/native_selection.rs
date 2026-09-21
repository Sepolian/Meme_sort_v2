use std::{
    fs,
    path::{Path, PathBuf},
    sync::Mutex,
    time::{Duration, Instant},
};

#[cfg(windows)]
use std::os::windows::fs::MetadataExt;

use serde::Serialize;
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::DialogExt;
use uuid::Uuid;

use crate::sidecar::{validate_source_path, SidecarError};

const MAX_IMPORT_SOURCES: usize = 256;
const LIBRARY_SELECTION_LIFETIME: Duration = Duration::from_secs(60);
const MAX_LIBRARY_SELECTIONS: usize = 16;
const SEARCH_IMAGE_SELECTION_LIFETIME: Duration = Duration::from_secs(60);
const MAX_SEARCH_IMAGE_SELECTIONS: usize = 16;
#[cfg(windows)]
const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;

/// An image file selected through the native dialog for one later Search Request.
/// The WebView never supplies this path to an image-search command.
pub struct SearchImageSelection(Mutex<Vec<SearchImageSelectionEntry>>);

#[derive(Debug)]
pub(crate) struct SearchImageSelectionEntry {
    pub(crate) request_id: String,
    pub(crate) path: PathBuf,
    created_at: Instant,
}

#[cfg(test)]
impl SearchImageSelectionEntry {
    pub(crate) fn expire(&mut self) {
        self.created_at =
            Instant::now() - SEARCH_IMAGE_SELECTION_LIFETIME - Duration::from_secs(1);
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum LibrarySelectionOrigin {
    Files,
    Folder,
    /// An Explorer drag-and-drop selection, which may mix files and folders.
    Drop,
}

#[derive(Debug)]
pub(crate) struct LibrarySelectionEntry {
    pub(crate) id: String,
    pub(crate) origin: LibrarySelectionOrigin,
    pub(crate) paths: Vec<PathBuf>,
    pub(crate) created_at: Instant,
}

/// Temporary native Library selections. Each entry is origin-tagged, one-time,
/// time-bounded, and never exposed to the WebView as filesystem paths.
pub struct LibraryImportSelection(pub(crate) Mutex<Vec<LibrarySelectionEntry>>);

impl LibraryImportSelection {
    pub fn new() -> Self {
        Self(Mutex::new(Vec::new()))
    }

    pub(crate) fn store(
        &self,
        origin: LibrarySelectionOrigin,
        paths: Vec<PathBuf>,
    ) -> Result<LibrarySelectionSummary, SidecarError> {
        validate_library_paths(origin, &paths)?;
        let id = Uuid::new_v4().to_string();
        let count = paths.len();
        let created_at = Instant::now();
        let mut entries = self
            .0
            .lock()
            .map_err(|_| SidecarError::new("MemeSort Library selection is unavailable."))?;
        entries.retain(|entry| entry.created_at.elapsed() < LIBRARY_SELECTION_LIFETIME);
        entries.push(LibrarySelectionEntry {
            id: id.clone(),
            origin,
            paths,
            created_at,
        });
        while entries.len() > MAX_LIBRARY_SELECTIONS {
            entries.remove(0);
        }
        Ok(LibrarySelectionSummary {
            selection_id: id,
            count,
        })
    }

    pub(crate) fn take(&self, selection_id: &str) -> Result<LibrarySelectionEntry, SidecarError> {
        let mut entries = self
            .0
            .lock()
            .map_err(|_| SidecarError::new("MemeSort Library selection is unavailable."))?;
        entries.retain(|entry| entry.created_at.elapsed() < LIBRARY_SELECTION_LIFETIME);
        let index = entries
            .iter()
            .position(|entry| entry.id == selection_id)
            .ok_or_else(|| {
                SidecarError::new("Library Import selection has expired or was already consumed.")
            })?;
        Ok(entries.remove(index))
    }
}

impl SearchImageSelection {
    pub fn new() -> Self {
        Self(Mutex::new(Vec::new()))
    }

    pub(crate) fn replace(
        &self,
        request_id: &str,
        path: Option<PathBuf>,
    ) -> Result<Option<String>, SidecarError> {
        let selected_path = path.as_ref().map(|path| path.display().to_string());
        let mut selection = self
            .0
            .lock()
            .map_err(|_| SidecarError::new("MemeSort image selection is unavailable."))?;
        selection.retain(|entry| {
            entry.request_id != request_id
                && entry.created_at.elapsed() < SEARCH_IMAGE_SELECTION_LIFETIME
        });
        if let Some(path) = path {
            selection.push(SearchImageSelectionEntry {
                request_id: request_id.to_owned(),
                path,
                created_at: Instant::now(),
            });
            while selection.len() > MAX_SEARCH_IMAGE_SELECTIONS {
                selection.remove(0);
            }
        }
        Ok(selected_path)
    }

    #[cfg(test)]
    pub(crate) fn selected_path(&self, request_id: &str) -> Result<String, SidecarError> {
        let mut selection = self
            .0
            .lock()
            .map_err(|_| SidecarError::new("MemeSort image selection is unavailable."))?;
        selection.retain(|entry| entry.created_at.elapsed() < SEARCH_IMAGE_SELECTION_LIFETIME);
        selection
            .iter()
            .find(|entry| entry.request_id == request_id)
            .map(|entry| entry.path.display().to_string())
            .ok_or_else(SidecarError::image_selection_unavailable)
    }

    pub(crate) fn take(
        &self,
        request_id: &str,
    ) -> Result<SearchImageSelectionEntry, SidecarError> {
        let mut selection = self
            .0
            .lock()
            .map_err(|_| SidecarError::new("MemeSort image selection is unavailable."))?;
        selection.retain(|entry| entry.created_at.elapsed() < SEARCH_IMAGE_SELECTION_LIFETIME);
        let index = selection
            .iter()
            .position(|entry| entry.request_id == request_id)
            .ok_or_else(SidecarError::image_selection_unavailable)?;
        Ok(selection.remove(index))
    }

    pub(crate) fn restore(&self, entry: SearchImageSelectionEntry) -> Result<bool, SidecarError> {
        let mut selection = self
            .0
            .lock()
            .map_err(|_| SidecarError::new("MemeSort image selection is unavailable."))?;
        selection.retain(|current| {
            current.created_at.elapsed() < SEARCH_IMAGE_SELECTION_LIFETIME
        });
        if entry.created_at.elapsed() >= SEARCH_IMAGE_SELECTION_LIFETIME
            || selection
                .iter()
                .any(|current| current.request_id == entry.request_id)
        {
            return Ok(false);
        }
        let request_id = entry.request_id.clone();
        let index = selection.partition_point(|current| current.created_at <= entry.created_at);
        selection.insert(index, entry);
        while selection.len() > MAX_SEARCH_IMAGE_SELECTIONS {
            selection.remove(0);
        }
        Ok(selection
            .iter()
            .any(|current| current.request_id == request_id))
    }
}

#[derive(Serialize)]
pub struct NativePathSelection {
    request_id: String,
    selected_path: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct LibrarySelectionSummary {
    pub(crate) selection_id: String,
    pub(crate) count: usize,
}

#[tauri::command]
pub fn choose_search_image(
    app: AppHandle,
    request_id: String,
) -> Result<NativePathSelection, SidecarError> {
    let request_id = crate::sidecar::validate_search_request_id(&request_id)?;
    let path = app
        .dialog()
        .file()
        .set_title("Choose an image to search with MemeSort")
        .add_filter("Image files", &["jpg", "jpeg", "png", "webp", "gif", "bmp"])
        .blocking_pick_file();
    let path = path
        .map(|path| {
            path.into_path()
                .map_err(|error| SidecarError::new(error.to_string()))
        })
        .transpose()?;
    let selection = app
        .try_state::<SearchImageSelection>()
        .ok_or_else(|| SidecarError::new("MemeSort image selection is unavailable."))?;
    let selected_path = selection.replace(&request_id, path)?;
    Ok(NativePathSelection {
        request_id,
        selected_path,
    })
}

#[tauri::command]
pub fn choose_library_files(
    app: AppHandle,
) -> Result<Option<LibrarySelectionSummary>, SidecarError> {
    let paths = app
        .dialog()
        .file()
        .set_title("Choose image files to import into MemeSort")
        .add_filter("Image files", &["jpg", "jpeg", "png", "webp", "gif", "bmp"])
        .blocking_pick_files();
    let paths = paths
        .map(|paths| {
            paths
                .into_iter()
                .map(|path| {
                    path.into_path()
                        .map_err(|error| SidecarError::new(error.to_string()))
                })
                .collect::<Result<Vec<_>, _>>()
        })
        .transpose()?;
    let Some(paths) = paths else {
        return Ok(None);
    };
    let selection = app
        .try_state::<LibraryImportSelection>()
        .ok_or_else(|| SidecarError::new("MemeSort Library selection is unavailable."))?;
    Ok(Some(selection.store(LibrarySelectionOrigin::Files, paths)?))
}

#[tauri::command]
pub fn choose_library_folder(
    app: AppHandle,
) -> Result<Option<LibrarySelectionSummary>, SidecarError> {
    let path = app
        .dialog()
        .file()
        .set_title("Choose a folder to import into MemeSort Library")
        .blocking_pick_folder();
    let path = path
        .map(|path| {
            path.into_path()
                .map_err(|error| SidecarError::new(error.to_string()))
        })
        .transpose()?;
    let Some(path) = path else {
        return Ok(None);
    };
    let selection = app
        .try_state::<LibraryImportSelection>()
        .ok_or_else(|| SidecarError::new("MemeSort Library selection is unavailable."))?;
    Ok(Some(
        selection.store(LibrarySelectionOrigin::Folder, vec![path])?,
    ))
}

pub(crate) fn validate_library_paths(
    origin: LibrarySelectionOrigin,
    paths: &[PathBuf],
) -> Result<Vec<String>, SidecarError> {
    if paths.is_empty() || paths.len() > MAX_IMPORT_SOURCES {
        return Err(SidecarError::new(
            "A Library Import Batch requires 1 to 256 sources.",
        ));
    }
    let mut sources = Vec::with_capacity(paths.len());
    for path in paths {
        sources.push(validate_library_path(path, origin)?);
    }
    Ok(sources)
}

#[cfg(test)]
mod tests {
    use super::{
        SearchImageSelection, MAX_SEARCH_IMAGE_SELECTIONS, SEARCH_IMAGE_SELECTION_LIFETIME,
    };
    use std::{
        path::PathBuf,
        time::{Duration, Instant},
    };

    const FIRST_REQUEST: &str = "123e4567-e89b-12d3-a456-426614174000";
    const SECOND_REQUEST: &str = "123e4567-e89b-12d3-a456-426614174001";
    const UNKNOWN_REQUEST: &str = "123e4567-e89b-12d3-a456-426614174099";

    #[test]
    fn keeps_overlapping_image_selections_bound_to_their_request_ids() {
        let selections = SearchImageSelection::new();
        selections
            .replace(SECOND_REQUEST, Some(PathBuf::from("C:/Source/second.png")))
            .expect("second selection should be stored");
        selections
            .replace(FIRST_REQUEST, Some(PathBuf::from("C:/Source/first.png")))
            .expect("first selection should be stored");

        assert_eq!(
            selections
                .selected_path(FIRST_REQUEST)
                .expect("first path should match"),
            "C:/Source/first.png"
        );
        assert_eq!(
            selections
                .selected_path(SECOND_REQUEST)
                .expect("second path should match"),
            "C:/Source/second.png"
        );
        assert!(selections.selected_path(UNKNOWN_REQUEST).is_err());
    }

    #[test]
    fn cancellation_clears_only_its_request_selection() {
        let selections = SearchImageSelection::new();
        selections
            .replace(FIRST_REQUEST, Some(PathBuf::from("C:/Source/first.png")))
            .expect("first selection should be stored");
        selections
            .replace(SECOND_REQUEST, Some(PathBuf::from("C:/Source/second.png")))
            .expect("second selection should be stored");

        selections
            .replace(FIRST_REQUEST, None)
            .expect("first cancellation should clear its selection");

        assert!(selections.selected_path(FIRST_REQUEST).is_err());
        assert_eq!(
            selections
                .selected_path(SECOND_REQUEST)
                .expect("second path should remain"),
            "C:/Source/second.png"
        );
    }

    fn assert_selection_unavailable(error: crate::sidecar::SidecarError) {
        let payload = serde_json::to_value(error).expect("selection error should serialize");
        assert_eq!(payload["error"], "ImageSelectionUnavailable");
        assert_eq!(
            payload["detail"],
            "The selected image is no longer available. Choose another image."
        );
        assert_eq!(payload["retryable"], false);
        assert_eq!(payload["status"], serde_json::Value::Null);
    }

    #[test]
    fn reports_expired_image_selection_with_non_retryable_metadata() {
        let selections = SearchImageSelection::new();
        selections
            .replace(FIRST_REQUEST, Some(PathBuf::from("C:/Source/expired.png")))
            .expect("selection should be stored");
        {
            let mut entries = selections.0.lock().expect("selection lock should be available");
            entries[0].created_at =
                Instant::now() - SEARCH_IMAGE_SELECTION_LIFETIME - Duration::from_secs(1);
        }

        assert_selection_unavailable(
            selections
                .selected_path(FIRST_REQUEST)
                .expect_err("expired selection must not be reused"),
        );
    }

    #[test]
    fn evicts_oldest_image_selection_at_the_bounded_limit() {
        let selections = SearchImageSelection::new();
        let request_ids = (0..=MAX_SEARCH_IMAGE_SELECTIONS)
            .map(|index| format!("123e4567-e89b-12d3-a456-42661417{index:04x}"))
            .collect::<Vec<_>>();
        for request_id in &request_ids {
            selections
                .replace(request_id, Some(PathBuf::from("C:/Source/query.png")))
                .expect("selection should be stored");
        }

        assert_selection_unavailable(
            selections
                .selected_path(&request_ids[0])
                .expect_err("oldest selection must be evicted"),
        );
        assert_eq!(
            selections
                .selected_path(request_ids.last().expect("last request should exist"))
                .expect("newest selection should remain"),
            "C:/Source/query.png"
        );
    }

    #[test]
    fn reports_consumed_and_mismatched_image_selection_as_unavailable() {
        let selections = SearchImageSelection::new();
        selections
            .replace(FIRST_REQUEST, Some(PathBuf::from("C:/Source/query.png")))
            .expect("selection should be stored");
        selections
            .replace(SECOND_REQUEST, Some(PathBuf::from("C:/Source/other.png")))
            .expect("other selection should be stored");
        selections
            .replace(FIRST_REQUEST, None)
            .expect("selection should be retired");

        assert_selection_unavailable(
            selections
                .selected_path(FIRST_REQUEST)
                .expect_err("consumed selection must not be reused"),
        );
        assert_selection_unavailable(
            selections
                .selected_path(UNKNOWN_REQUEST)
                .expect_err("mismatched request must not access another selection"),
        );
        assert_eq!(
            selections
                .selected_path(SECOND_REQUEST)
                .expect("other selection should remain available"),
            "C:/Source/other.png"
        );
    }

    #[test]
    fn taking_an_image_selection_is_atomic_and_restore_makes_only_that_request_retryable() {
        let selections = SearchImageSelection::new();
        selections
            .replace(FIRST_REQUEST, Some(PathBuf::from("C:/Source/query.png")))
            .expect("selection should be stored");

        let entry = selections
            .take(FIRST_REQUEST)
            .expect("selection should be consumed");
        assert_selection_unavailable(
            selections
                .take(FIRST_REQUEST)
                .expect_err("a concurrent replay must not reuse the selection"),
        );

        assert!(selections
            .restore(entry)
            .expect("retryable failure should restore the selection"));
        assert_eq!(
            selections
                .selected_path(FIRST_REQUEST)
                .expect("restored selection should be available"),
            "C:/Source/query.png"
        );
    }

    #[test]
    fn restore_reports_when_expiry_or_capacity_prevents_reinsertion() {
        let selections = SearchImageSelection::new();
        selections
            .replace(FIRST_REQUEST, Some(PathBuf::from("C:/Source/query.png")))
            .expect("selection should be stored");
        let mut expired = selections
            .take(FIRST_REQUEST)
            .expect("selection should be consumed");
        expired.expire();
        assert!(!selections.restore(expired).expect("restore should complete"));

        selections
            .replace(FIRST_REQUEST, Some(PathBuf::from("C:/Source/query.png")))
            .expect("selection should be stored");
        let entry = selections
            .take(FIRST_REQUEST)
            .expect("selection should be consumed");
        for index in 0..MAX_SEARCH_IMAGE_SELECTIONS {
            selections
                .replace(
                    &format!("123e4567-e89b-12d3-a456-42661418{index:04x}"),
                    Some(PathBuf::from("C:/Source/newer.png")),
                )
                .expect("newer selection should be stored");
        }
        assert!(!selections.restore(entry).expect("restore should complete"));
    }
}

fn validate_library_path(
    path: &Path,
    origin: LibrarySelectionOrigin,
) -> Result<String, SidecarError> {
    if !path.is_absolute() {
        return Err(SidecarError::new(
            "Library Import sources must use absolute paths.",
        ));
    }
    let source = path
        .to_str()
        .ok_or_else(|| SidecarError::new("Library Import source paths must be valid Unicode."))?;
    let source = validate_source_path(source)?;
    let metadata = fs::symlink_metadata(path).map_err(|_| {
        SidecarError::new("A Library Import source is missing or cannot be accessed.")
    })?;
    if is_reparse_point(&metadata) {
        return Err(SidecarError::new(
            "Library Import sources cannot be symlinks, junctions, or reparse points.",
        ));
    }
    let is_file = metadata.file_type().is_file();
    let is_dir = metadata.file_type().is_dir();
    let valid = match origin {
        LibrarySelectionOrigin::Files => is_file,
        LibrarySelectionOrigin::Folder => is_dir,
        LibrarySelectionOrigin::Drop => is_file || is_dir,
    };
    if !valid {
        return Err(SidecarError::new(
            "A Library Import source has an irregular file type.",
        ));
    }
    Ok(source)
}

#[cfg(windows)]
fn is_reparse_point(metadata: &fs::Metadata) -> bool {
    metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
}

#[cfg(not(windows))]
fn is_reparse_point(metadata: &fs::Metadata) -> bool {
    metadata.file_type().is_symlink()
}
