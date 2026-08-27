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
#[cfg(windows)]
const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;

/// A source folder selected through the native dialog for this desktop session.
/// The WebView never supplies this path to an import command.
pub struct ImportSelection(Mutex<Option<PathBuf>>);

/// An image file selected through the native dialog for one later Search Request.
/// The WebView never supplies this path to an image-search command.
pub struct SearchImageSelection(Mutex<Option<PathBuf>>);

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

impl ImportSelection {
    pub fn new() -> Self {
        Self(Mutex::new(None))
    }

    pub(crate) fn replace(&self, path: Option<PathBuf>) -> Result<Option<String>, SidecarError> {
        let selected_path = path.as_ref().map(|path| path.display().to_string());
        let mut selection = self
            .0
            .lock()
            .map_err(|_| SidecarError::new("MemeSort import selection is unavailable."))?;
        *selection = path;
        Ok(selected_path)
    }

    pub(crate) fn selected_path(&self) -> Result<String, SidecarError> {
        self.0
            .lock()
            .map_err(|_| SidecarError::new("MemeSort import selection is unavailable."))?
            .as_ref()
            .map(|path| path.display().to_string())
            .ok_or_else(|| SidecarError::new("Choose a source folder before importing."))
    }
}

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
        Self(Mutex::new(None))
    }

    pub(crate) fn replace(&self, path: Option<PathBuf>) -> Result<Option<String>, SidecarError> {
        let selected_path = path.as_ref().map(|path| path.display().to_string());
        let mut selection = self
            .0
            .lock()
            .map_err(|_| SidecarError::new("MemeSort image selection is unavailable."))?;
        *selection = path;
        Ok(selected_path)
    }

    pub(crate) fn selected_path(&self) -> Result<String, SidecarError> {
        self.0
            .lock()
            .map_err(|_| SidecarError::new("MemeSort image selection is unavailable."))?
            .as_ref()
            .map(|path| path.display().to_string())
            .ok_or_else(|| SidecarError::new("Choose an image before searching."))
    }
}

#[derive(Serialize)]
pub struct NativePathSelection {
    selected_path: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct LibrarySelectionSummary {
    pub(crate) selection_id: String,
    pub(crate) count: usize,
}

#[tauri::command]
pub fn choose_import_folder(app: AppHandle) -> Result<NativePathSelection, SidecarError> {
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
        .transpose()?;
    let selection = app
        .try_state::<ImportSelection>()
        .ok_or_else(|| SidecarError::new("MemeSort import selection is unavailable."))?;
    let selected_path = selection.replace(path)?;
    Ok(NativePathSelection { selected_path })
}

#[tauri::command]
pub fn choose_search_image(app: AppHandle) -> Result<NativePathSelection, SidecarError> {
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
    let selected_path = selection.replace(path)?;
    Ok(NativePathSelection { selected_path })
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
