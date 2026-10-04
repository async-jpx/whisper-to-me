//! It must float over any app, including another app's native full-screen
//! Space, without activating us: no Space switch, no focus taken from the
//! call window. tao offers none of that (its `show()` is
//! makeKeyAndOrderFront), so `overlay_macos` turns the window into a
//! non-activating NSPanel at status-window level and orders it front with
//! orderFrontRegardless.

use tauri::{
    AppHandle, LogicalPosition, Manager, Monitor, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
};

use crate::daemon;

const LABEL: &str = "meeting-prompt";
/// The window is transparent; prompt.html insets its card by GUTTER so its
/// CSS shadow fits, which leaves the visible card 360x132.
const GUTTER: f64 = 14.0;
const WIDTH: f64 = 360.0 + 2.0 * GUTTER;
const HEIGHT: f64 = 132.0 + 2.0 * GUTTER;
const MARGIN: f64 = 16.0 - GUTTER;

/// Menu-bar height-ish offset so the widget sits just under the system bar.
const TOP_OFFSET: f64 = 40.0 - GUTTER;

fn ensure_window(app: &AppHandle) -> Option<WebviewWindow> {
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
        .skip_taskbar(true)
        .focusable(false)
        .accept_first_mouse(true)
        .focused(false)
        .visible(false)
        .transparent(true)
        // prompt.html draws the card's shadow itself, inside the gutter.
        .shadow(false)
        .build()
        .ok()?;
    #[cfg(target_os = "macos")]
    if let Ok(ns) = win.ns_window() {
        let ns = ns as usize;
        let _ = app.run_on_main_thread(move || unsafe {
            overlay_macos::configure(ns as *mut std::ffi::c_void);
        });
    }
    Some(win)
}

/// tao reports the cursor in pixels of the primary display but looks
/// monitors up in points.
fn pointer_monitor(win: &WebviewWindow) -> Option<Monitor> {
    let scale = win.primary_monitor().ok()??.scale_factor();
    let p = win.cursor_position().ok()?;
    win.monitor_from_point(p.x / scale, p.y / scale).ok()?
}

pub fn show(app: &AppHandle) {
    let Some(win) = ensure_window(app) else { return };
    if let Some(m) = pointer_monitor(&win).or_else(|| win.primary_monitor().ok().flatten()) {
        // Logical units throughout: physical sizes use each monitor's own
        // scale, which breaks the arithmetic on mixed-DPI setups.
        let s = m.scale_factor();
        let (x0, y0) = (m.position().x as f64 / s, m.position().y as f64 / s);
        let width = m.size().width as f64 / s;
        let _ = win.set_position(LogicalPosition::new(
            x0 + width - WIDTH - MARGIN,
            y0 + TOP_OFFSET,
        ));
    }
    #[cfg(target_os = "macos")]
    if let Ok(ns) = win.ns_window() {
        let ns = ns as usize;
        let _ = app.run_on_main_thread(move || unsafe {
            overlay_macos::order_front(ns as *mut std::ffi::c_void);
        });
    }
    #[cfg(not(target_os = "macos"))]
    let _ = win.show();
}

pub fn hide(app: &AppHandle) {
    if let Some(win) = app.get_webview_window(LABEL) {
        let _ = win.hide();
    }
}

#[cfg(target_os = "macos")]
mod overlay_macos {
    use std::ffi::c_void;
    use std::sync::OnceLock;

    use objc2::ffi;
    use objc2::runtime::{AnyClass, AnyObject, Bool, ClassBuilder, Imp, Sel};
    use objc2::{msg_send, sel, ClassType, MainThreadMarker};
    use objc2_app_kit::{
        NSPanel, NSStatusWindowLevel, NSWindow, NSWindowCollectionBehavior, NSWindowStyleMask,
    };

    /// SAFETY: `ns_window` must be the live NSWindow of a tao window.
    pub unsafe fn configure(ns_window: *mut c_void) {
        if MainThreadMarker::new().is_none() {
            eprintln!("overlay: configure called off the main thread; skipped");
            return;
        }
        let obj = ns_window.cast::<AnyObject>();
        let win = &*ns_window.cast::<NSWindow>();
        if become_panel(obj) {
            win.setStyleMask(win.styleMask() | NSWindowStyleMask::NonactivatingPanel);
            // NSPanel defaults to YES, and we are never the active app.
            win.setHidesOnDeactivate(false);
            // Private AppKit call: without it a click on the panel still
            // activates our app. Missing on some future macOS => the
            // overlay still shows, but a click brings us to the front.
            let prevent = sel!(_setPreventsActivation:);
            let responds: bool = msg_send![&*obj, respondsToSelector: prevent];
            if responds {
                let _: () = msg_send![&*obj, _setPreventsActivation: true];
            }
        }
        win.setCollectionBehavior(
            NSWindowCollectionBehavior::CanJoinAllSpaces
                | NSWindowCollectionBehavior::FullScreenAuxiliary
                | NSWindowCollectionBehavior::Stationary
                | NSWindowCollectionBehavior::IgnoresCycle,
        );
        win.setLevel(NSStatusWindowLevel);
    }

    /// SAFETY: as for `configure`.
    pub unsafe fn order_front(ns_window: *mut c_void) {
        if MainThreadMarker::new().is_none() {
            return;
        }
        (*ns_window.cast::<NSWindow>()).orderFrontRegardless();
    }

    static TAO_SEND_EVENT: OnceLock<Imp> = OnceLock::new();

    extern "C-unwind" fn never(_this: &AnyObject, _sel: Sel) -> Bool {
        Bool::NO
    }

    extern "C-unwind" fn send_event(this: &AnyObject, sel: Sel, event: *mut AnyObject) {
        if let Some(&imp) = TAO_SEND_EVENT.get() {
            let f: extern "C-unwind" fn(&AnyObject, Sel, *mut AnyObject) =
                unsafe { std::mem::transmute(imp) };
            f(this, sel, event);
        }
    }

    /// NSPanel subclass with TaoWindow's one ivar, so an instance can be
    /// re-classed in place (the technique tauri-nspanel uses).
    fn panel_class(tao: &AnyClass) -> Option<&'static AnyClass> {
        static CLS: OnceLock<Option<&'static AnyClass>> = OnceLock::new();
        *CLS.get_or_init(|| {
            let imp = tao.instance_method(sel!(sendEvent:))?.implementation();
            let _ = TAO_SEND_EVENT.set(imp);
            let mut b = ClassBuilder::new(c"WtmOverlayPanel", NSPanel::class())?;
            b.add_ivar::<Bool>(c"focusable");
            unsafe {
                b.add_method(sel!(canBecomeKeyWindow), never as extern "C-unwind" fn(_, _) -> _);
                b.add_method(sel!(canBecomeMainWindow), never as extern "C-unwind" fn(_, _) -> _);
                b.add_method(sel!(sendEvent:), send_event as extern "C-unwind" fn(_, _, _));
            }
            Some(b.register())
        })
    }

    unsafe fn become_panel(obj: *mut AnyObject) -> bool {
        let isa = &*ffi::object_getClass(obj);
        // The live class is AppKit's KVO subclass NSKVONotifying_TaoWindow.
        let mut tao = isa;
        while tao.name() != c"TaoWindow" {
            match tao.superclass() {
                Some(s) => tao = s,
                None => {
                    eprintln!("overlay: no TaoWindow above {:?}; plain window", isa.name());
                    return false;
                }
            }
        }
        let Some(panel) = panel_class(tao) else {
            eprintln!("overlay: could not build WtmOverlayPanel; plain window");
            return false;
        };
        let offset = |c: &AnyClass| c.instance_variable(c"focusable").map(|i| i.offset());
        let sizes = [isa.instance_size(), tao.instance_size(), panel.instance_size()];
        let same_ivar = offset(tao).is_some() && offset(tao) == offset(panel);
        if sizes[0] != sizes[1] || sizes[1] != sizes[2] || !same_ivar {
            eprintln!(
                "overlay: layout mismatch (sizes {sizes:?}, focusable ivar {:?} vs {:?}); plain window",
                offset(tao),
                offset(panel)
            );
            return false;
        }
        ffi::object_setClass(obj, panel);
        true
    }
}
