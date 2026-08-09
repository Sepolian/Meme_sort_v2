mod sidecar;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
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
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            sidecar::get_app_state,
            sidecar::get_assets,
            sidecar::get_asset_detail
        ])
        .build(tauri::generate_context!())
        .expect("error while running tauri application")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::ExitRequested { .. }) {
                sidecar::shutdown_managed_sidecar(app);
            }
        });
}
