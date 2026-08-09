mod sidecar;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
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
            app.manage(sidecar::ImportSelection::new());
            app.manage(sidecar::SearchImageSelection::new());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            sidecar::get_app_state,
            sidecar::get_assets,
            sidecar::get_asset_detail,
            sidecar::delete_asset,
            sidecar::remove_source_record,
            sidecar::batch_asset_action,
            sidecar::choose_import_folder,
            sidecar::choose_search_image,
            sidecar::start_import,
            sidecar::start_import_and_index,
            sidecar::pause_import,
            sidecar::resume_import,
            sidecar::search_text,
            sidecar::search_image,
            sidecar::find_similar,
            sidecar::get_duplicates,
            sidecar::cancel_search
        ])
        .build(tauri::generate_context!())
        .expect("error while running tauri application")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::ExitRequested { .. }) {
                sidecar::shutdown_managed_sidecar(app);
            }
        });
}
