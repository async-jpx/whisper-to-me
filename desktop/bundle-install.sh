#!/usr/bin/env bash
#
# Build the whisper-to-me Tauri app bundle and install it into /Applications.
#
# The app is a thin menu-bar shell: it spawns the repo's own daemon
# (.venv/bin/wtm serve) at a path baked in at build time, so this repo and its
# .venv must stay put after installing — the .app is not standalone yet
# (frozen sidecar is Phase 2.5).
#
# Signing: if a code-signing identity is found (or given), the bundle is signed
# with it, giving a stable identity across rebuilds so macOS keeps the mic /
# System Audio Recording permissions instead of re-prompting every build. With
# no identity it falls back to Tauri's ad-hoc signing (permissions reset each
# rebuild, and Gatekeeper needs the quarantine strip below).
#
# Env knobs:
#   SIGN_IDENTITY   code-signing identity to sign with (default: auto-detect a
#                   "whisper-to-me" identity; else ad-hoc)
#   BUNDLES         Tauri bundle targets (default: app; e.g. "app,dmg")
#   INSTALL_DIR     where to install (default: /Applications)
#   OPEN_AFTER      open the app when done (default: 1; set 0 to skip)

set -euo pipefail

# --- locate the repo (this script lives in desktop/) --------------------------
DESKTOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$DESKTOP_DIR/.." && pwd)"
APP_NAME="whisper-to-me"
INSTALL_DIR="${INSTALL_DIR:-/Applications}"
BUNDLES="${BUNDLES:-app}"
OPEN_AFTER="${OPEN_AFTER:-1}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --- toolchain: cargo (brew rustup) and node (possibly via nvm) ---------------
if ! command -v cargo >/dev/null 2>&1; then
  export PATH="/opt/homebrew/opt/rustup/bin:$PATH"
fi
command -v cargo >/dev/null 2>&1 || die "cargo not found (install rustup, or fix PATH)"

if ! command -v node >/dev/null 2>&1; then
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  # shellcheck disable=SC1091
  [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
fi
command -v node >/dev/null 2>&1 || die "node not found (needed by the Tauri CLI)"

command -v uv >/dev/null 2>&1 || die "uv not found (needed to build the daemon venv)"

# --- 1. ensure the daemon the app will spawn actually exists ------------------
if [ ! -x "$REPO_DIR/.venv/bin/wtm" ]; then
  say "Building the daemon venv (uv sync) — the app spawns .venv/bin/wtm"
  ( cd "$REPO_DIR" && uv sync )
fi
[ -x "$REPO_DIR/.venv/bin/wtm" ] || die ".venv/bin/wtm still missing after uv sync"

# --- 2. Tauri CLI --------------------------------------------------------------
cd "$DESKTOP_DIR"
if [ ! -x "$DESKTOP_DIR/node_modules/.bin/tauri" ]; then
  say "Installing the Tauri CLI (npm install)"
  npm install
fi
TAURI="$DESKTOP_DIR/node_modules/.bin/tauri"

# --- 3. pick a signing identity (optional) ------------------------------------
IDENTITY="${SIGN_IDENTITY:-}"
if [ -z "$IDENTITY" ]; then
  IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
    | grep -o '"[^"]*whisper-to-me[^"]*"' | head -1 | tr -d '"' || true)"
fi
if [ -n "$IDENTITY" ]; then
  say "Signing with identity: $IDENTITY"
  export APPLE_SIGNING_IDENTITY="$IDENTITY"
else
  say "No code-signing identity found — Tauri will ad-hoc sign (see header note)"
fi

# --- 4. build the bundle ------------------------------------------------------
say "Building the app bundle (targets: $BUNDLES) — this takes a few minutes"
"$TAURI" build --bundles "$BUNDLES"

APP_SRC="$(ls -dt "$DESKTOP_DIR/src-tauri/target/release/bundle/macos/"*.app 2>/dev/null | head -1 || true)"
[ -n "$APP_SRC" ] && [ -d "$APP_SRC" ] || die "build produced no .app under target/release/bundle/macos"
say "Built: $APP_SRC"

# --- 5. install into INSTALL_DIR ----------------------------------------------
DEST="$INSTALL_DIR/$(basename "$APP_SRC")"
# Quit a running instance first so we can replace it cleanly (this SIGTERMs a
# daemon it spawned, which saves + summarizes any in-progress note).
osascript -e "quit app \"$APP_NAME\"" >/dev/null 2>&1 || true
sleep 1

say "Installing to $DEST"
rm -rf "$DEST" 2>/dev/null || die "could not remove old $DEST (try: sudo BUNDLES=$BUNDLES $0)"
ditto "$APP_SRC" "$DEST" || die "could not copy into $INSTALL_DIR (try running with sudo)"

# Self-signed / ad-hoc apps are quarantined on copy; strip it so Gatekeeper
# doesn't block the first launch (a self-signed cert is still not Apple-trusted).
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

say "Installed. The app runs the daemon from: $REPO_DIR/.venv/bin/wtm"
say "Daemon logs: ~/Library/Logs/whisper-to-me/daemon.log"

if [ "$OPEN_AFTER" = "1" ]; then
  say "Launching…"
  open "$DEST"
fi
