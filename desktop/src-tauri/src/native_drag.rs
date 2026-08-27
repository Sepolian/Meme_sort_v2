use std::{fs, path::PathBuf, sync::Mutex};

use serde::Serialize;
use tauri::{DragDropEvent, Emitter, LogicalPosition, Manager, PhysicalPosition, WindowEvent};

use crate::native_selection::{
    validate_library_paths, LibraryImportSelection, LibrarySelectionOrigin,
};

pub const NATIVE_DRAG_EVENT: &str = "library-native-drag";

/// Counts dragged files and folders so hover previews stay informative
/// without re-reading the dragged entries.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct NativeDragContext {
    pub(crate) file_count: usize,
    pub(crate) folder_count: usize,
}

pub(crate) fn native_drag_counts(paths: &[PathBuf]) -> NativeDragContext {
    let mut file_count = 0;
    let mut folder_count = 0;
    for path in paths {
        match fs::symlink_metadata(path) {
            Ok(metadata) if metadata.file_type().is_dir() => folder_count += 1,
            Ok(_) => file_count += 1,
            Err(_) => {}
        }
    }
    NativeDragContext {
        file_count,
        folder_count,
    }
}

/// Managed acceptance cache for one in-flight Explorer drag gesture.
pub struct NativeDragState(pub(crate) Mutex<Option<NativeDragContext>>);

impl NativeDragState {
    pub fn new() -> Self {
        Self(Mutex::new(None))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub(crate) enum NativeDragPhase {
    Enter,
    Over,
    Leave,
    Drop,
}

/// A path-free summary of one native drag event. Counts and logical
/// coordinates travel to the WebView; source paths never do.
#[derive(Debug, Clone, Serialize)]
pub(crate) struct NativeDragSummary {
    pub(crate) phase: NativeDragPhase,
    pub(crate) file_count: usize,
    pub(crate) folder_count: usize,
    pub(crate) x: f64,
    pub(crate) y: f64,
    pub(crate) accepted: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) drop_id: Option<String>,
}

/// A typed native drag input, decoupled from the windowing runtime for tests.
pub(crate) enum NativeDragInput {
    Enter(Vec<PathBuf>, PhysicalPosition<f64>),
    Over(PhysicalPosition<f64>),
    Leave,
    Drop(Vec<PathBuf>, PhysicalPosition<f64>),
}

pub(crate) fn logical_drag_position(
    position: PhysicalPosition<f64>,
    scale_factor: f64,
) -> LogicalPosition<f64> {
    position.to_logical(scale_factor)
}

pub(crate) fn native_drag_summary(
    phase: NativeDragPhase,
    paths: Option<&[PathBuf]>,
    context: Option<NativeDragContext>,
    position: PhysicalPosition<f64>,
    scale_factor: f64,
) -> (NativeDragSummary, Option<NativeDragContext>) {
    let logical = logical_drag_position(position, scale_factor);
    let summary = |file_count: usize, folder_count: usize, accepted: bool| NativeDragSummary {
        phase,
        file_count,
        folder_count,
        x: logical.x,
        y: logical.y,
        accepted,
        drop_id: None,
    };
    match phase {
        NativeDragPhase::Enter => {
            let dragged = paths.unwrap_or(&[]);
            let accepted = validate_library_paths(LibrarySelectionOrigin::Drop, dragged).is_ok();
            let counts = native_drag_counts(dragged);
            let next = accepted.then_some(counts);
            (
                summary(counts.file_count, counts.folder_count, accepted),
                next,
            )
        }
        NativeDragPhase::Over => match context {
            Some(context) => (
                summary(context.file_count, context.folder_count, true),
                Some(context),
            ),
            None => (summary(0, 0, false), None),
        },
        NativeDragPhase::Leave => (summary(0, 0, false), None),
        NativeDragPhase::Drop => {
            let dropped = paths.unwrap_or(&[]);
            let accepted = validate_library_paths(LibrarySelectionOrigin::Drop, dropped).is_ok();
            let counts = native_drag_counts(dropped);
            (
                summary(counts.file_count, counts.folder_count, accepted),
                None,
            )
        }
    }
}

/// Folds one native drag event into a path-free summary and a managed
/// one-time drop selection. Setup selection state is never touched.
pub(crate) fn process_native_drag_event<F>(
    selections: &LibraryImportSelection,
    context_slot: &Mutex<Option<NativeDragContext>>,
    scale_factor: f64,
    event: NativeDragInput,
    mut emit: F,
) where
    F: FnMut(NativeDragSummary),
{
    let park_context = |context_slot: &Mutex<Option<NativeDragContext>>,
                        next: Option<NativeDragContext>| {
        if let Ok(mut slot) = context_slot.lock() {
            *slot = next;
        }
    };
    match event {
        NativeDragInput::Enter(paths, position) => {
            let (summary, next) = native_drag_summary(
                NativeDragPhase::Enter,
                Some(&paths),
                None,
                position,
                scale_factor,
            );
            park_context(context_slot, next);
            emit(summary);
        }
        NativeDragInput::Over(position) => {
            let current = context_slot.lock().ok().and_then(|context| *context);
            let (summary, next) =
                native_drag_summary(NativeDragPhase::Over, None, current, position, scale_factor);
            park_context(context_slot, next);
            emit(summary);
        }
        NativeDragInput::Leave => {
            let (summary, _) = native_drag_summary(
                NativeDragPhase::Leave,
                None,
                None,
                PhysicalPosition::new(0.0, 0.0),
                scale_factor,
            );
            park_context(context_slot, None);
            emit(summary);
        }
        NativeDragInput::Drop(paths, position) => {
            let (mut summary, _) = native_drag_summary(
                NativeDragPhase::Drop,
                Some(&paths),
                None,
                position,
                scale_factor,
            );
            if summary.accepted {
                summary.drop_id = selections
                    .store(LibrarySelectionOrigin::Drop, paths)
                    .ok()
                    .map(|entry| entry.selection_id);
                summary.accepted = summary.drop_id.is_some();
            }
            park_context(context_slot, None);
            emit(summary);
        }
    }
}

/// Converts raw window drag events into path-free summaries and managed
/// one-time drop selections for the main Library window.
pub fn forward_native_drag(window: &tauri::Window, event: &WindowEvent) {
    let WindowEvent::DragDrop(drag) = event else {
        return;
    };
    let Some(selections) = window.try_state::<LibraryImportSelection>() else {
        return;
    };
    let Some(drag_state) = window.try_state::<NativeDragState>() else {
        return;
    };
    let input = match drag {
        DragDropEvent::Enter { paths, position } => {
            NativeDragInput::Enter(paths.clone(), *position)
        }
        DragDropEvent::Over { position } => NativeDragInput::Over(*position),
        DragDropEvent::Drop { paths, position } => NativeDragInput::Drop(paths.clone(), *position),
        DragDropEvent::Leave => NativeDragInput::Leave,
        _ => return,
    };
    let Ok(scale_factor) = window.scale_factor() else {
        // Without a known display scale, logical coordinates could not be
        // trusted; fail closed instead of accepting a mis-scaled drop.
        return;
    };
    process_native_drag_event(&selections, &drag_state.0, scale_factor, input, |summary| {
        let _ = window.emit_to("main", NATIVE_DRAG_EVENT, summary);
    });
}
