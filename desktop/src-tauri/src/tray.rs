//! Menu-bar presence: template icon, status line, session controls.
//!
//! Menu state mirrors the daemon's /api/events status feed (see daemon.rs);
//! every mutation funnels through `refresh` so the menu can never disagree
//! with the last event received.

use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{TrayIcon, TrayIconBuilder};
use tauri::{App, AppHandle, Manager, Wry};

use crate::daemon::{self, Phase};
use crate::login_item::LoginItem;

pub struct TrayHandles {
    tray: TrayIcon,
    status_line: MenuItem<Wry>,
    start: MenuItem<Wry>,
    stop: MenuItem<Wry>,
    record_meeting: MenuItem<Wry>,
    dismiss_meeting: MenuItem<Wry>,
    open_last: MenuItem<Wry>,
    login: CheckMenuItem<Wry>,
    login_item: Option<LoginItem>,
}

pub fn setup(app: &App, login_item: Option<LoginItem>) -> tauri::Result<()> {
    let status_line = MenuItem::with_id(app, "status", "Starting daemon…", false, None::<&str>)?;
    let start = MenuItem::with_id(app, "start", "Start recording", false, None::<&str>)?;
    let stop = MenuItem::with_id(app, "stop", "Stop recording", false, None::<&str>)?;
    let record_meeting =
        MenuItem::with_id(app, "record-meeting", "Record this meeting", false, None::<&str>)?;
    let dismiss_meeting =
        MenuItem::with_id(app, "dismiss-meeting", "Dismiss this meeting", false, None::<&str>)?;
    let open_last = MenuItem::with_id(app, "open-last", "Open last note", false, None::<&str>)?;
    let show = MenuItem::with_id(app, "show", "Open whisper-to-me", true, None::<&str>)?;
    let login = match &login_item {
        Some(item) => {
            let on = item.is_enabled();
            CheckMenuItem::with_id(app, "login", "Launch at login", true, on, None::<&str>)?
        }
        None => {
            let text = "Launch at login (bundled app only)";
            CheckMenuItem::with_id(app, "login", text, false, false, None::<&str>)?
        }
    };
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;

    let menu = Menu::with_items(
        app,
        &[
            &status_line,
            &PredefinedMenuItem::separator(app)?,
            &start,
            &stop,
            &record_meeting,
            &dismiss_meeting,
            &PredefinedMenuItem::separator(app)?,
            &open_last,
            &show,
            &login,
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
        record_meeting,
        dismiss_meeting,
        open_last,
        login,
        login_item,
    });
    Ok(())
}

fn on_menu(app: &AppHandle, id: &str) {
    match id {
        "start" => daemon::api_post("/api/record/start"),
        "stop" => daemon::api_post("/api/record/stop"),
        "record-meeting" | "dismiss-meeting" => {
            let prompt = app.state::<daemon::AppState>().status.lock().unwrap().prompt.clone();
            if let Some(p) = prompt {
                daemon::answer_prompt(&p.id, id == "record-meeting");
            }
        }
        "login" => toggle_login(app),
        "open-last" => open_last_note(app),
        "show" => show_main(app),
        "quit" => app.exit(0),
        _ => {}
    }
}

/// The click has already flipped the checkmark.
fn toggle_login(app: &AppHandle) {
    let Some(handles) = app.try_state::<TrayHandles>() else {
        return;
    };
    let Some(item) = &handles.login_item else {
        return;
    };
    let wanted = handles.login.is_checked().unwrap_or(false);
    if let Err(err) = item.set_enabled(wanted) {
        eprintln!("launch at login: could not write the LaunchAgent: {err}");
    }
    let _ = handles.login.set_checked(item.is_enabled());
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

    let _ = handles.status_line.set_text(status_line(&status));
    let _ = handles.start.set_enabled(status.can_start());
    let _ = handles.stop.set_enabled(status.can_stop());
    let prompting = status.online && status.prompt.is_some();
    let _ = handles.record_meeting.set_enabled(prompting);
    let _ = handles.dismiss_meeting.set_enabled(prompting);
    let _ = handles.open_last.set_enabled(status.online);

    // Always Some(...): set_title(None) does not clear an existing title on
    // macOS, so idle must overwrite with an empty string.
    let _ = handles.tray.set_title(Some(tray_title(&status)));
}

fn status_line(status: &daemon::Status) -> String {
    if !status.online {
        return "Daemon offline".to_string();
    }
    match status.phase {
        Phase::Idle => "Ready".to_string(),
        Phase::Prompting => match &status.prompt {
            Some(p) => {
                let left = (p.expires_in_s - status.since_received()).max(0.0).ceil();
                format!("Meeting detected — {} ({left}s)", p.title)
            }
            None => "Meeting detected".to_string(),
        },
        Phase::Starting => "Starting the recorder…".to_string(),
        Phase::Recording => match &status.title {
            Some(t) => format!("Recording — {t}"),
            None => "Recording".to_string(),
        },
        Phase::Stopping => "Finishing — transcribing the last audio…".to_string(),
        Phase::Summarizing => "Summarizing…".to_string(),
    }
}

fn tray_title(status: &daemon::Status) -> String {
    match status.phase {
        Phase::Recording => {
            let total = (status.elapsed_base + status.since_received()).max(0.0) as u64;
            format!("{}:{:02}", total / 60, total % 60)
        }
        Phase::Stopping | Phase::Summarizing => "…".to_string(),
        _ => String::new(),
    }
}

pub fn title_ticker(app: AppHandle) {
    loop {
        std::thread::sleep(std::time::Duration::from_secs(1));
        let status = app.state::<daemon::AppState>().status.lock().unwrap().clone();
        let Some(handles) = app.try_state::<TrayHandles>() else {
            continue;
        };
        match status.phase {
            Phase::Recording => {
                let _ = handles.tray.set_title(Some(tray_title(&status)));
            }
            Phase::Prompting => {
                let _ = handles.status_line.set_text(status_line(&status));
            }
            _ => {}
        }
    }
}
