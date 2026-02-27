#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# build_macos.sh — FCP Text-Based Editor  |  M-series macOS app bundle builder
# ──────────────────────────────────────────────────────────────────────────────
#
# Usage:
#   ./build_macos.sh                         # Lite build  (no Whisper, ~150 MB)
#   ./build_macos.sh --with-whisper          # Full build  (with Whisper, ~1.5 GB)
#   ./build_macos.sh --sign "Developer ID"  # Sign for Gatekeeper distribution
#   ./build_macos.sh --dmg                   # Create distributable .dmg after build
#   ./build_macos.sh --with-whisper --sign "Developer ID Application: Your Name (TEAMID)" --dmg
#
# What this script does:
#   1. Verifies prerequisites (Python 3.11+, ffmpeg)
#   2. Creates an isolated build virtualenv in .venv_build/
#   3. Installs all Python dependencies (+ optionally Whisper/PyTorch)
#   4. Runs PyInstaller with fcp_editor.spec
#   5. Copies the ffmpeg binary into Contents/MacOS/
#   6. Optionally signs the app with codesign (Deep, hardened runtime)
#   7. Optionally creates a .dmg for distribution
#
# Requirements:
#   • macOS 13+ (Ventura), Apple Silicon (M1/M2/M3/M4)
#   • Python 3.11+ from python.org or Homebrew  (NOT the system python)
#   • FFmpeg:  brew install ffmpeg
#   • For --sign: Apple Developer ID certificate in Keychain
#   • For --with-whisper: ~4 GB free disk space (PyTorch download)
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[1;33m'
BLU='\033[0;34m'; CYN='\033[0;36m'; RST='\033[0m'; BLD='\033[1m'

info()  { echo -e "${BLU}▸ $*${RST}"; }
ok()    { echo -e "${GRN}✓ $*${RST}"; }
warn()  { echo -e "${YEL}⚠ $*${RST}"; }
error() { echo -e "${RED}✗ $*${RST}"; exit 1; }
step()  { echo -e "\n${BLD}${CYN}── $* ──────────────────────────────${RST}"; }

# ── Parse arguments ───────────────────────────────────────────────────────────
WITH_WHISPER=0
SIGN_IDENTITY=""
CREATE_DMG=0
CLEAN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-whisper)  WITH_WHISPER=1       ;;
        --sign)          SIGN_IDENTITY="$2"; shift ;;
        --dmg)           CREATE_DMG=1         ;;
        --clean)         CLEAN=1              ;;
        -h|--help)
            head -40 "$0" | grep "^#" | sed 's/^# \?//'
            exit 0
            ;;
        *) warn "Unknown option: $1" ;;
    esac
    shift
done

APP_NAME="FCP Text Editor"
BUNDLE_ID="com.pkirkdevelopment.fcp-text-editor"
VERSION="0.1.0"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$REPO_DIR/.venv_build"
DIST_DIR="$REPO_DIR/dist"
APP_PATH="$DIST_DIR/${APP_NAME}.app"
DMG_PATH="$DIST_DIR/FCP-Text-Editor-${VERSION}.dmg"

cd "$REPO_DIR"

echo -e "\n${BLD}FCP Text-Based Editor — macOS App Builder${RST}"
echo -e "  Version     : ${VERSION}"
echo -e "  Whisper     : $([ $WITH_WHISPER -eq 1 ] && echo 'YES (full build ~1.5 GB)' || echo 'NO  (lite build ~150 MB)')"
echo -e "  Sign        : ${SIGN_IDENTITY:-'(none — not signed)'}"
echo -e "  Create DMG  : $([ $CREATE_DMG -eq 1 ] && echo 'YES' || echo 'NO')"
echo

# ── Step 1: prerequisites ─────────────────────────────────────────────────────
step "Checking prerequisites"

# Python version
PYTHON=$(command -v python3.11 2>/dev/null || command -v python3.12 2>/dev/null || \
         command -v python3 2>/dev/null || true)
[[ -z "$PYTHON" ]] && error "Python 3.11+ not found. Install from https://python.org or 'brew install python@3.11'"

PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
[[ $PY_MAJOR -lt 3 || ( $PY_MAJOR -eq 3 && $PY_MINOR -lt 11 ) ]] && \
    error "Python 3.11+ required. Found: $PY_VER"
ok "Python $PY_VER  ($PYTHON)"

# Check this is not the system Python
if [[ "$PYTHON" == "/usr/bin/python3" ]]; then
    error "Do not use the macOS system Python (/usr/bin/python3).\nInstall Python 3.11 from https://python.org or:  brew install python@3.11"
fi

# Architecture
ARCH=$(uname -m)
[[ "$ARCH" != "arm64" ]] && warn "Expected arm64 (M-series) but found $ARCH. Build may not be optimised for Apple Silicon."
ok "Architecture: $ARCH"

# ffmpeg
FFMPEG_BIN=$(command -v ffmpeg 2>/dev/null || true)
FFPROBE_BIN=$(command -v ffprobe 2>/dev/null || true)

if [[ -z "$FFMPEG_BIN" ]]; then
    error "ffmpeg not found. Install with:  brew install ffmpeg"
fi
ok "ffmpeg  : $FFMPEG_BIN"

if [[ -z "$FFPROBE_BIN" ]]; then
    warn "ffprobe not found alongside ffmpeg — video info detection may fail."
fi

# Check for icon (warn but don't fail)
if [[ ! -f "packaging/icon.icns" ]]; then
    warn "No app icon found at packaging/icon.icns"
    warn "Run  ./packaging/make_icon.sh your_image.png  to generate one."
fi

# ── Step 2: Build virtualenv ──────────────────────────────────────────────────
step "Setting up build virtualenv"

if [[ $CLEAN -eq 1 && -d "$VENV_DIR" ]]; then
    info "Removing existing venv (--clean)…"
    rm -rf "$VENV_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtualenv in .venv_build/…"
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Virtualenv created"
else
    ok "Reusing existing virtualenv"
fi

VPYTHON="$VENV_DIR/bin/python"
VPIP="$VENV_DIR/bin/pip"

# Upgrade pip silently
"$VPIP" install --quiet --upgrade pip

# ── Step 3: Install Python dependencies ───────────────────────────────────────
step "Installing Python dependencies"

info "Installing core requirements…"
"$VPIP" install --quiet \
    "customtkinter>=5.2.0" \
    "Pillow>=10.0.0" \
    "opencv-python>=4.8.0" \
    "click>=8.1.0" \
    "rich>=13.7.0" \
    "pydub>=0.25.1" \
    "numpy>=1.24.0" \
    "ffmpeg-python>=0.2.0"
ok "Core packages installed"

info "Installing PyInstaller…"
"$VPIP" install --quiet "pyinstaller>=6.3.0"
ok "PyInstaller installed"

if [[ $WITH_WHISPER -eq 1 ]]; then
    info "Installing PyTorch (Apple Silicon MPS backend)…"
    info "This may take several minutes (~1.5 GB download)…"
    "$VPIP" install --quiet torch torchvision torchaudio
    ok "PyTorch installed"

    info "Installing Whisper…"
    "$VPIP" install --quiet "openai-whisper>=20231117"
    ok "Whisper installed"
fi

# ── Step 4: Clean previous build ─────────────────────────────────────────────
step "Preparing build directory"

[[ -d "build" ]] && { info "Removing build/…"; rm -rf build; }
[[ -d "$APP_PATH" ]] && { info "Removing existing .app…"; rm -rf "$APP_PATH"; }

mkdir -p "$DIST_DIR"
ok "Build directories ready"

# ── Step 5: Run PyInstaller ───────────────────────────────────────────────────
step "Running PyInstaller (this takes a few minutes)"

export BUILD_WITH_WHISPER=$WITH_WHISPER

"$VENV_DIR/bin/pyinstaller" \
    --noconfirm \
    --distpath "$DIST_DIR" \
    --workpath "$REPO_DIR/build" \
    fcp_editor.spec

ok "PyInstaller build complete"

# ── Step 6: Bundle ffmpeg binary ─────────────────────────────────────────────
step "Bundling ffmpeg binary"

MACOS_DIR="$APP_PATH/Contents/MacOS"

cp "$FFMPEG_BIN" "$MACOS_DIR/ffmpeg"
chmod +x "$MACOS_DIR/ffmpeg"
ok "Bundled: ffmpeg → $MACOS_DIR/ffmpeg"

if [[ -n "$FFPROBE_BIN" ]]; then
    cp "$FFPROBE_BIN" "$MACOS_DIR/ffprobe"
    chmod +x "$MACOS_DIR/ffprobe"
    ok "Bundled: ffprobe → $MACOS_DIR/ffprobe"
fi

# ── Step 7: Verify the bundle ─────────────────────────────────────────────────
step "Verifying bundle"

[[ ! -d "$APP_PATH" ]] && error "App bundle not found at $APP_PATH"

BUNDLE_SIZE=$(du -sh "$APP_PATH" | awk '{print $1}')
EXEC_PATH="$APP_PATH/Contents/MacOS/$APP_NAME"
[[ ! -x "$EXEC_PATH" ]] && error "Executable not found: $EXEC_PATH"

ok "Bundle exists  ($BUNDLE_SIZE):  $APP_PATH"

# ── Step 8: Code signing ──────────────────────────────────────────────────────
if [[ -n "$SIGN_IDENTITY" ]]; then
    step "Code signing"

    info "Signing all dylibs and executables…"
    find "$APP_PATH" -type f \( -name "*.dylib" -o -name "*.so" -o -name "*.framework" \) | \
        while read -r f; do
            codesign --force --options runtime \
                --entitlements packaging/entitlements.plist \
                --sign "$SIGN_IDENTITY" "$f" 2>/dev/null || true
        done

    info "Signing main app bundle…"
    codesign --force --deep --options runtime \
        --entitlements packaging/entitlements.plist \
        --sign "$SIGN_IDENTITY" \
        "$APP_PATH"
    ok "App signed with: $SIGN_IDENTITY"

    info "Verifying signature…"
    codesign --verify --deep --strict --verbose=2 "$APP_PATH" && ok "Signature valid"

    echo
    warn "Next step — Notarize for distribution outside App Store:"
    warn "  xcrun notarytool submit \"$DMG_PATH\" \\"
    warn "      --apple-id YOUR@APPLE.ID \\"
    warn "      --team-id YOUR_TEAM_ID \\"
    warn "      --password APP_SPECIFIC_PASSWORD \\"
    warn "      --wait"
    warn "  xcrun stapler staple \"$APP_PATH\""
else
    warn "App not signed. Double-clicking on another Mac will be blocked by Gatekeeper."
    warn "To sign:  ./build_macos.sh --sign \"Developer ID Application: Your Name (TEAMID)\""
fi

# ── Step 9: Create .dmg ───────────────────────────────────────────────────────
if [[ $CREATE_DMG -eq 1 ]]; then
    step "Creating .dmg"
    bash packaging/create_dmg.sh "$APP_PATH" "$DMG_PATH" "$APP_NAME"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo
echo -e "${BLD}${GRN}╔═══════════════════════════════════════════════════╗"
echo -e "║          Build complete ✓                         ║"
echo -e "╚═══════════════════════════════════════════════════╝${RST}"
echo
echo -e "  App bundle : ${BLD}$APP_PATH${RST}  (${BUNDLE_SIZE})"
[[ $CREATE_DMG -eq 1 ]] && echo -e "  DMG        : ${BLD}$DMG_PATH${RST}"
echo
echo -e "  ${YEL}Test locally:${RST}  open \"$APP_PATH\""
echo -e "  ${YEL}Distribute  :${RST}  Share the .dmg after notarizing"
echo
