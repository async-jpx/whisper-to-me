//! Launch at login through a per-user LaunchAgent the shell writes itself,
//! offered only when running from inside a .app bundle.
//!
//! The agent never execs our binary: when a directly exec'd job's process
//! exits, launchd kills everything it spawned, including a daemon we leave
//! running to finish saving a note (measured, even with AbandonProcessGroup
//! and a separate process group). `/usr/bin/open -a <bundle>` hands the
//! launch to LaunchServices instead, and the job itself exits at once.
//!
//! The plist is the only state: absent = never set up (created enabled on
//! the first bundled launch), a `Disabled` key = the user opted out. Edits
//! shape the next login only; nothing is loaded into launchd at runtime.

use std::io;
use std::path::{Path, PathBuf};
use std::process::Command;

pub struct LoginItem {
    label: String,
    bundle: PathBuf,
    plist: PathBuf,
}

impl LoginItem {
    /// None for the dev binary (not inside a .app bundle).
    pub fn current(label: &str) -> Option<Self> {
        let exe = std::env::current_exe().ok()?.canonicalize().ok()?;
        let bundle = bundle_of(&exe)?;
        let home = std::env::var_os("HOME")?;
        let plist = Path::new(&home)
            .join("Library/LaunchAgents")
            .join(format!("{label}.plist"));
        Some(Self { label: label.to_string(), bundle, plist })
    }

    pub fn is_enabled(&self) -> bool {
        self.plist.is_file() && !disabled_key(&self.plist)
    }

    /// Runs on every launch: creates the agent enabled the first time, and
    /// otherwise rewrites it with the same on/off state so a moved .app
    /// never leaves a stale path behind.
    pub fn ensure(&self) -> io::Result<()> {
        self.set_enabled(!self.plist.is_file() || self.is_enabled())
    }

    pub fn set_enabled(&self, enabled: bool) -> io::Result<()> {
        let dir = self.plist.parent().expect("plist path has a parent");
        std::fs::create_dir_all(dir)?;
        let tmp = self.plist.with_extension("plist.tmp");
        std::fs::write(&tmp, plist_xml(&self.label, &self.bundle, enabled))?;
        std::fs::rename(&tmp, &self.plist)
    }
}

/// `<X>.app/Contents/MacOS/<bin>` -> `<X>.app`.
fn bundle_of(exe: &Path) -> Option<PathBuf> {
    let macos = exe.parent()?;
    let contents = macos.parent()?;
    let app = contents.parent()?;
    let is_bundle = macos.file_name()? == "MacOS"
        && contents.file_name()? == "Contents"
        && app.extension()? == "app";
    is_bundle.then(|| app.to_path_buf())
}

fn disabled_key(plist: &Path) -> bool {
    Command::new("/usr/bin/plutil")
        .args(["-extract", "Disabled", "raw", "-o", "-"])
        .arg(plist)
        .output()
        .map(|out| out.status.success() && String::from_utf8_lossy(&out.stdout).trim() == "true")
        .unwrap_or(false)
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

fn plist_xml(label: &str, bundle: &Path, enabled: bool) -> String {
    let disabled = if enabled { "" } else { "\n    <key>Disabled</key>\n    <true/>" };
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/open</string>
        <string>-a</string>
        <string>{}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>{disabled}
</dict>
</plist>
"#,
        xml_escape(label),
        xml_escape(&bundle.to_string_lossy()),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn extract(plist: &Path, key: &str) -> Option<String> {
        let out = Command::new("/usr/bin/plutil")
            .args(["-extract", key, "raw", "-o", "-"])
            .arg(plist)
            .output()
            .ok()?;
        out.status.success().then(|| String::from_utf8_lossy(&out.stdout).trim().to_string())
    }

    fn written(name: &str, bundle: &str, enabled: bool) -> PathBuf {
        let path = std::env::temp_dir().join(format!("wtm-login-test-{}-{name}.plist", std::process::id()));
        std::fs::write(&path, plist_xml("io.example.wtm", Path::new(bundle), enabled)).unwrap();
        path
    }

    #[test]
    fn plist_is_valid_and_launches_the_bundle_through_open() {
        let bundle = "/Applications/A & <B>.app";
        let p = written("on", bundle, true);
        assert!(Command::new("/usr/bin/plutil").arg("-lint").arg(&p).status().unwrap().success());
        assert_eq!(extract(&p, "ProgramArguments.0").as_deref(), Some("/usr/bin/open"));
        assert_eq!(extract(&p, "ProgramArguments.1").as_deref(), Some("-a"));
        assert_eq!(extract(&p, "ProgramArguments.2").as_deref(), Some(bundle));
        assert_eq!(extract(&p, "RunAtLoad").as_deref(), Some("true"));
        assert_eq!(extract(&p, "Disabled"), None);
        assert!(!disabled_key(&p));
        std::fs::remove_file(p).unwrap();
    }

    #[test]
    fn opting_out_writes_disabled() {
        let p = written("off", "/Applications/whisper-to-me.app", false);
        assert!(Command::new("/usr/bin/plutil").arg("-lint").arg(&p).status().unwrap().success());
        assert!(disabled_key(&p));
        std::fs::remove_file(p).unwrap();
    }

    #[test]
    fn only_a_bundled_executable_is_offered() {
        let exe = Path::new("/Applications/whisper-to-me.app/Contents/MacOS/whisper-to-me-desktop");
        assert_eq!(bundle_of(exe), Some(PathBuf::from("/Applications/whisper-to-me.app")));
        let dev = Path::new("/repo/desktop/src-tauri/target/debug/whisper-to-me-desktop");
        assert_eq!(bundle_of(dev), None);
    }
}
