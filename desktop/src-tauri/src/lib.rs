//! Tauri shell around the local whisper-to-me daemon.
//!
//! The daemon (`wtm serve`, FastAPI on 127.0.0.1:8737) stays the single
//! brain; this shell only spawns it, points a webview at it, and mirrors its
//! status in the menu bar. Everything here talks to loopback only — the
//! shell must never make an off-machine request.

mod daemon;
mod tray;

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            app.manage(daemon::AppState::default());

            // The window opens on the bundled loading page; daemon::boot
            // navigates it to the daemon UI once /api/status answers.
            let win = WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::App("index.html".into()),
            )
            .title("whisper-to-me")
            .inner_size(1120.0, 780.0)
            .build()?;

            // Menu-bar app: closing the window hides it, Quit lives in the tray.
            let hide_target = win.clone();
            win.on_window_event(move |event| {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = hide_target.hide();
                }
            });

            tray::setup(app)?;

            let handle = app.handle().clone();
            std::thread::spawn(move || daemon::boot(handle));
            let handle = app.handle().clone();
            std::thread::spawn(move || tray::title_ticker(handle));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building whisper-to-me")
        .run(|app, event| match event {
            tauri::RunEvent::Exit => daemon::shutdown(app),
            #[cfg(target_os = "macos")]
            tauri::RunEvent::Reopen { .. } => tray::show_main(app),
            _ => {}
        });
}
