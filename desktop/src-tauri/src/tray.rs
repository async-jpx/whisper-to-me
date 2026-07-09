//! Menu-bar presence: template icon, status line, session controls.
//!
//! Menu state mirrors the daemon's /api/events status feed (see daemon.rs);
//! every mutation funnels through `refresh` so the menu can never disagree
//! with the last event received.

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{TrayIcon, TrayIconBuilder};
use tauri::{App, AppHandle, Manager, Wry};

use crate::daemon;

pub struct TrayHandles {
    tray: TrayIcon,
    status_line: MenuItem<Wry>,
    start: MenuItem<Wry>,
    stop: MenuItem<Wry>,
    watch: MenuItem<Wry>,
    unwatch: MenuItem<Wry>,
    open_last: MenuItem<Wry>,
}

pub fn setup(app: &App) -> tauri::Result<()> {
    let status_line = MenuItem::with_id(app, "status", "Starting daemon…", false, None::<&str>)?;
    let start = MenuItem::with_id(app, "start", "Start recording", false, None::<&str>)?;
    let stop = MenuItem::with_id(app, "stop", "Stop recording", false, None::<&str>)?;
    let watch = MenuItem::with_id(app, "watch", "Start watching for meetings", false, None::<&str>)?;
    let unwatch = MenuItem::with_id(app, "unwatch", "Stop watching", false, None::<&str>)?;
    let open_last = MenuItem::with_id(app, "open-last", "Open last note", false, None::<&str>)?;
    let show = MenuItem::with_id(app, "show", "Open whisper-to-me", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;

    let menu = Menu::with_items(
        app,
        &[
            &status_line,
            &PredefinedMenuItem::separator(app)?,
            &start,
            &stop,
            &watch,
            &unwatch,
            &PredefinedMenuItem::separator(app)?,
            &open_last,
            &show,
            &PredefinedMenuItem::separator(app)?,
            &quit,
        ],
    )?;

    let icon = tauri::image::Image::from_bytes(include_bytes!("../icons/tray.png"))?;
    let tray = TrayIconBuilder::with_id("wtm-tray")
        .icon(icon)
        .icon_as_template(true)
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| on_menu(app, event.id.as_ref()))
        .build(app)?;

    app.manage(TrayHandles {
        tray,
        status_line,
        start,
        stop,
        watch,
        unwatch,
        open_last,
    });
    Ok(())
}

fn on_menu(app: &AppHandle, id: &str) {
    match id {
        "start" => daemon::api_post("/api/record/start"),
        "stop" => daemon::api_post("/api/record/stop"),
        "watch" => daemon::api_post("/api/watch/start"),
        "unwatch" => daemon::api_post("/api/watch/stop"),
        "open-last" => open_last_note(app),
        "show" => show_main(app),
        "quit" => app.exit(0),
        _ => {}
    }
}

pub fn show_main(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

fn open_last_note(app: &AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || {
        show_main(&app);
        let Some(notes) = daemon::api_get_json("/api/notes") else {
            return;
        };
        let Some(name) = notes
            .get(0)
            .and_then(|n| n.get("name"))
            .and_then(serde_json::Value::as_str)
        else {
            return;
        };
        if let Some(win) = app.get_webview_window("main") {
            // Reset first so re-opening the same note still fires hashchange.
            let js = format!(
                "location.hash = ''; location.hash = '#note=' + encodeURIComponent({});",
                serde_json::to_string(name).unwrap_or_default()
            );
            let _ = win.eval(&js);
        }
    });
}

/// Re-derive every menu label/enabled flag from the status snapshot.
pub fn refresh(app: &AppHandle) {
    let status = app.state::<daemon::AppState>().status.lock().unwrap().clone();
    let Some(handles) = app.try_state::<TrayHandles>() else {
        return;
    };

    let mode = status.mode.as_deref().unwrap_or("");
    let line = if !status.online {
        "Daemon offline".to_string()
    } else {
        match status.state.as_str() {
            "starting" => "Starting the recorder…".to_string(),
            "recording" => match &status.title {
                Some(t) => format!("Recording — {t}"),
                None => "Recording".to_string(),
            },
            "watching" => "Watching for meetings".to_string(),
            "stopping" => "Finishing — transcribing the last audio…".to_string(),
            "summarizing" => "Summarizing…".to_string(),
            _ => "Idle — ready".to_string(),
        }
    };
    let _ = handles.status_line.set_text(line);
    let _ = handles.start.set_enabled(status.online && status.state == "idle");
    let _ = handles.stop.set_enabled(
        status.online
            && mode == "record"
            && matches!(status.state.as_str(), "starting" | "recording"),
    );
    let _ = handles.watch.set_enabled(status.online && status.state == "idle");
    let _ = handles
        .unwatch
        .set_enabled(status.online && mode == "watch" && status.state != "idle");
    let _ = handles.open_last.set_enabled(status.online);

    // Always Some(...): set_title(None) does not clear an existing title on
    // macOS, so idle must overwrite with an empty string.
    let _ = handles.tray.set_title(Some(tray_title(&status)));
}

fn tray_title(status: &daemon::Status) -> String {
    match status.state.as_str() {
        "recording" => {
            let elapsed = status.elapsed_base
                + status.received.map(|t| t.elapsed().as_secs_f64()).unwrap_or(0.0);
            let total = elapsed.max(0.0) as u64;
            format!("{}:{:02}", total / 60, total % 60)
        }
        "stopping" | "summarizing" => "…".to_string(),
        _ => String::new(),
    }
}

/// Keeps the menu-bar elapsed time ticking between status events.
pub fn title_ticker(app: AppHandle) {
    loop {
        std::thread::sleep(std::time::Duration::from_secs(1));
        let recording = {
            let state = app.state::<daemon::AppState>();
            let status = state.status.lock().unwrap();
            status.state == "recording"
        };
        if recording {
            if let Some(handles) = app.try_state::<TrayHandles>() {
                let status = app.state::<daemon::AppState>().status.lock().unwrap().clone();
                let _ = handles.tray.set_title(Some(tray_title(&status)));
            }
        }
    }
}
