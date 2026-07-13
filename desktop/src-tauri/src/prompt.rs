//! The meeting-prompt overlay: a small, undecorated, always-on-top window in
//! the top-right corner that appears when the daemon detects a meeting
//! (status "prompting") and disappears once the user answers or the meeting
//! ends unanswered. The page it shows (`/static/prompt.html`) is served by
//! the local daemon, so its Record/Ignore buttons talk to 127.0.0.1 directly
//! — the shell only manages window visibility.

use tauri::{AppHandle, Manager, PhysicalPosition, WebviewUrl, WebviewWindowBuilder};

use crate::daemon;

const LABEL: &str = "meeting-prompt";
const WIDTH: f64 = 360.0;
const HEIGHT: f64 = 148.0;
const MARGIN: f64 = 16.0;

/// Menu-bar height-ish offset so the widget sits just under the system bar.
const TOP_OFFSET: f64 = 40.0;

fn ensure_window(app: &AppHandle) -> Option<tauri::WebviewWindow> {
    if let Some(win) = app.get_webview_window(LABEL) {
        return Some(win);
    }
    let url = format!("{}/static/prompt.html", daemon::base_url());
    let win = WebviewWindowBuilder::new(app, LABEL, WebviewUrl::External(url.parse().ok()?))
        .title("Meeting detected")
        .inner_size(WIDTH, HEIGHT)
        .decorations(false)
        .resizable(false)
        .minimizable(false)
        .maximizable(false)
        .closable(false)
        .always_on_top(true)
        .visible_on_all_workspaces(true)
        .skip_taskbar(true)
        .focused(false)
        .visible(false)
        .shadow(true)
        .build()
        .ok()?;
    Some(win)
}

/// Show the overlay in the top-right corner of the primary monitor.
pub fn show(app: &AppHandle) {
    let Some(win) = ensure_window(app) else { return };
    if let Ok(Some(monitor)) = win.primary_monitor() {
        let scale = monitor.scale_factor();
        let size = monitor.size();
        let pos = monitor.position();
        let x = pos.x as f64 + size.width as f64 - (WIDTH + MARGIN) * scale;
        let y = pos.y as f64 + TOP_OFFSET * scale;
        let _ = win.set_position(PhysicalPosition::new(x, y));
    }
    // show() without focus: the user is mid-meeting — never steal their
    // keyboard away from the call window.
    let _ = win.show();
}

pub fn hide(app: &AppHandle) {
    if let Some(win) = app.get_webview_window(LABEL) {
        let _ = win.hide();
    }
}
