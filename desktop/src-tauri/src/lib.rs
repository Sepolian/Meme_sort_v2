mod clipboard;
mod native_drag;
mod native_selection;
mod sidecar;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .on_window_event(native_drag::forward_native_drag)
        .register_asynchronous_uri_scheme_protocol(
            sidecar::MEDIA_PROTOCOL,
            |context, request, responder| {
                let app = context.app_handle().clone();
                std::thread::spawn(move || {
                    responder.respond(sidecar::media_protocol_response(&app, request));
                });
            },
        )
        .setup(|app| {
            let sidecar = sidecar::SidecarSession::start(app.handle())?;
            app.manage(sidecar::SidecarState::new(sidecar));
            app.manage(native_selection::ImportSelection::new());
            app.manage(native_selection::LibraryImportSelection::new());
            app.manage(native_selection::SearchImageSelection::new());
            app.manage(native_drag::NativeDragState::new());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            sidecar::get_app_state,
            sidecar::get_import_status,
            sidecar::get_assets,
            sidecar::get_asset_detail,
            sidecar::delete_asset,
            sidecar::remove_source_record,
            sidecar::batch_asset_action,
            native_selection::choose_import_folder,
            native_selection::choose_search_image,
            native_selection::choose_library_files,
            native_selection::choose_library_folder,
            sidecar::start_library_import,
            sidecar::start_import,
            sidecar::start_import_and_index,
            sidecar::pause_import,
            sidecar::resume_import,
            sidecar::search_text,
            sidecar::search_image,
            sidecar::find_similar,
            sidecar::get_duplicates,
            sidecar::pause_worker_loop,
            sidecar::resume_worker_loop,
            sidecar::trigger_worker_loop,
            sidecar::run_runtime_health_check,
            sidecar::retry_failed_jobs,
            sidecar::get_pending_jobs,
            sidecar::delete_pending_jobs,
            sidecar::reveal_asset,
            sidecar::open_log_directory,
            sidecar::copy_asset_to_clipboard,
            sidecar::copy_original_file,
            sidecar::copy_original_files,
            sidecar::cancel_search
        ])
        .build(tauri::generate_context!())
        .expect("error while running tauri application")
        .run(|app, event| {
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                sidecar::shutdown_managed_sidecar(app);
            }
        });
}
