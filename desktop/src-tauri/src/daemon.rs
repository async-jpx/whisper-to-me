//! Sidecar lifecycle for the Python daemon plus the WebSocket event feed.
//!
//! Rules the rest of the shell relies on:
//! - If a daemon already answers on the port, reuse it and never kill it.
//! - On quit, a daemon we spawned gets SIGTERM (the CLI turns that into a
//!   clean stop: save transcript, summarize). If it is still busy after a
//!   short grace period we leave it running — never SIGKILL a summarize.

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::Value;
use tauri::{AppHandle, Manager};
use tauri_plugin_notification::NotificationExt;

pub fn port() -> u16 {
    std::env::var("WTM_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8737)
}

pub fn base_url() -> String {
    format!("http://127.0.0.1:{}", port())
}

#[derive(Default, Clone)]
pub struct Status {
    pub online: bool,
    pub state: String, // idle | starting | recording | watching | stopping | summarizing
    pub mode: Option<String>, // record | watch | simulate
    pub title: Option<String>,
    pub elapsed_base: f64,
    pub received: Option<Instant>,
}

#[derive(Default)]
pub struct AppState {
    pub child: Mutex<Option<Child>>,
    pub we_spawned: AtomicBool,
    pub status: Mutex<Status>,
}

// -- HTTP helpers (loopback only) ---------------------------------------

pub fn healthy() -> bool {
    ureq::get(&format!("{}/api/status", base_url()))
        .timeout(Duration::from_millis(800))
        .call()
        .map(|resp| resp.status() == 200)
        .unwrap_or(false)
}

pub fn api_post(path: &str) {
    api_post_body(path, "{}".to_string());
}

pub fn api_post_body(path: &str, body: String) {
    let url = format!("{}{path}", base_url());
    std::thread::spawn(move || {
        let _ = ureq::post(&url)
            .timeout(Duration::from_secs(5))
            .set("Content-Type", "application/json")
            .send_string(&body);
    });
}

pub fn api_get_json(path: &str) -> Option<Value> {
    ureq::get(&format!("{}{path}", base_url()))
        .timeout(Duration::from_secs(5))
        .call()
        .ok()?
        .into_json()
        .ok()
}

// -- spawn / boot --------------------------------------------------------

fn wtm_bin() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("WTM_BIN") {
        let p = PathBuf::from(p);
        if p.is_file() {
            return Some(p);
        }
    }
    // Dev layout: repo/.venv/bin/wtm, two levels up from src-tauri. This is
    // a direct console-script (own python process), so SIGTERM reaches the
    // daemon without a `uv run` wrapper in between. A bundled build will
    // ship a frozen sidecar instead (Phase 2.5).
    let dev = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.venv/bin/wtm");
    if dev.is_file() {
        return dev.canonicalize().ok();
    }
    None
}

fn log_file() -> Option<std::fs::File> {
    let home = std::env::var("HOME").ok()?;
    let dir = std::path::Path::new(&home).join("Library/Logs/whisper-to-me");
    std::fs::create_dir_all(&dir).ok()?;
    std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join("daemon.log"))
        .ok()
}

fn spawn_daemon() -> Result<Child, String> {
    let bin = wtm_bin().ok_or_else(|| {
        "wtm not found — run `uv sync` in the repo, or set WTM_BIN".to_string()
    })?;
    let log = log_file().ok_or_else(|| "could not open daemon log file".to_string())?;
    let log_err = log.try_clone().map_err(|e| e.to_string())?;
    Command::new(&bin)
        .args(["serve", "--port", &port().to_string()])
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(log_err))
        .spawn()
        .map_err(|e| format!("failed to start {}: {e}", bin.display()))
}

fn set_loading_message(app: &AppHandle, text: &str, is_error: bool) {
    if let Some(win) = app.get_webview_window("main") {
        let js = format!(
            "var m = document.getElementById('msg'); if (m) {{ m.textContent = {}; m.className = {}; }}",
            serde_json::to_string(text).unwrap_or_default(),
            if is_error { "'error'" } else { "''" },
        );
        let _ = win.eval(&js);
    }
}

/// Runs on its own thread for the app's whole lifetime: ensure a daemon is
/// up, point the window at it, then follow its event stream forever.
pub fn boot(app: AppHandle) {
    let state = app.state::<AppState>();
    if !healthy() {
        match spawn_daemon() {
            Ok(child) => {
                *state.child.lock().unwrap() = Some(child);
                state.we_spawned.store(true, Ordering::SeqCst);
            }
            Err(err) => set_loading_message(&app, &err, true),
        }

        if state.we_spawned.load(Ordering::SeqCst) {
            let deadline = Instant::now() + Duration::from_secs(25);
            while Instant::now() < deadline && !healthy() {
                let died = state
                    .child
                    .lock()
                    .unwrap()
                    .as_mut()
                    .and_then(|c| c.try_wait().ok().flatten());
                if let Some(exit) = died {
                    set_loading_message(
                        &app,
                        &format!(
                            "The daemon exited ({exit}) — see ~/Library/Logs/whisper-to-me/daemon.log"
                        ),
                        true,
                    );
                    break;
                }
                std::thread::sleep(Duration::from_millis(300));
            }
            if !healthy() {
                set_loading_message(
                    &app,
                    "The daemon did not come up — see ~/Library/Logs/whisper-to-me/daemon.log",
                    true,
                );
            }
        }
    }

    if healthy() {
        if let Some(win) = app.get_webview_window("main") {
            let _ = win.eval(&format!("window.location.replace('{}/')", base_url()));
        }
    }

    ws_loop(app);
}

// -- event feed ----------------------------------------------------------

fn ws_loop(app: AppHandle) {
    loop {
        if let Ok((mut sock, _)) =
            tungstenite::connect(format!("ws://127.0.0.1:{}/api/events", port()))
        {
            mark_online(&app, true);
            loop {
                match sock.read() {
                    Ok(tungstenite::Message::Text(txt)) => {
                        if let Ok(evt) = serde_json::from_str::<Value>(&txt) {
                            handle_event(&app, &evt);
                        }
                    }
                    Ok(_) => {}
                    Err(_) => break,
                }
            }
            mark_online(&app, false);
        }
        std::thread::sleep(Duration::from_secs(2));
    }
}

fn mark_online(app: &AppHandle, online: bool) {
    {
        let state = app.state::<AppState>();
        let mut status = state.status.lock().unwrap();
        status.online = online;
        if !online {
            status.state.clear();
            status.title = None;
            status.mode = None;
        }
    }
    if !online {
        crate::prompt::hide(app); // never leave a stale prompt floating
    }
    crate::tray::refresh(app);
}

fn notify(app: &AppHandle, title: &str, body: &str) {
    let _ = app
        .notification()
        .builder()
        .title(title)
        .body(body)
        .show();
}

fn handle_event(app: &AppHandle, evt: &Value) {
    match evt.get("type").and_then(Value::as_str) {
        Some("status") => {
            let new_state = evt.get("state").and_then(Value::as_str).unwrap_or("idle");
            let title = evt.get("title").and_then(Value::as_str).map(str::to_string);
            let prev_state;
            {
                let state = app.state::<AppState>();
                let mut status = state.status.lock().unwrap();
                prev_state = std::mem::replace(&mut status.state, new_state.to_string());
                status.online = true;
                status.mode = evt.get("mode").and_then(Value::as_str).map(str::to_string);
                status.title = title.clone();
                status.elapsed_base = evt.get("elapsed_s").and_then(Value::as_f64).unwrap_or(0.0);
                status.received = Some(Instant::now());
            }
            if (prev_state == "watching" || prev_state == "prompting")
                && new_state == "recording"
            {
                notify(
                    app,
                    "Meeting detected — recording",
                    title.as_deref().unwrap_or(""),
                );
            }
            // The Notion-style overlay: visible exactly while the daemon is
            // asking record/ignore. Its buttons talk to the daemon directly;
            // the shell only mirrors visibility off the status feed.
            if new_state == "prompting" {
                if prev_state != "prompting" {
                    notify(
                        app,
                        "Meeting detected",
                        title.as_deref().unwrap_or("Record it?"),
                    );
                }
                crate::prompt::show(app);
            } else {
                crate::prompt::hide(app);
            }
            crate::tray::refresh(app);
        }
        Some("saved") => {
            let title = evt.get("title").and_then(Value::as_str).unwrap_or("note");
            notify(app, "Note saved", title);
        }
        Some("error") => {
            let msg = evt.get("message").and_then(Value::as_str).unwrap_or("error");
            notify(app, "whisper-to-me", msg);
        }
        _ => {}
    }
}

// -- shutdown ------------------------------------------------------------

pub fn shutdown(app: &AppHandle) {
    let state = app.state::<AppState>();
    if !state.we_spawned.load(Ordering::SeqCst) {
        return; // we attached to an existing daemon — not ours to stop
    }
    let child = state.child.lock().unwrap().take();
    if let Some(mut child) = child {
        unsafe {
            libc::kill(child.id() as i32, libc::SIGTERM);
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            match child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) => std::thread::sleep(Duration::from_millis(100)),
                Err(_) => return,
            }
        }
        // Still running: it is saving/summarizing. Let it finish and exit
        // on its own rather than risk losing a note.
        eprintln!("whisper-to-me daemon still finishing a save; leaving it running");
    }
}
