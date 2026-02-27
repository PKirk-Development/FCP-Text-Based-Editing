#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# create_dmg.sh  —  Wraps the .app in a distributable .dmg
#
# Usage (called by build_macos.sh, or directly):
#   ./packaging/create_dmg.sh "dist/FCP Text Editor.app" \
#                              "dist/FCP-Text-Editor-0.1.0.dmg" \
#                              "FCP Text Editor"
#
# Requires: hdiutil (built into macOS)
# Optional: create-dmg  (brew install create-dmg) for a prettier DMG with
#           a custom background, icon positions, and drag-to-Applications.
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

APP_PATH="${1:-dist/FCP Text Editor.app}"
DMG_PATH="${2:-dist/FCP-Text-Editor.dmg}"
APP_NAME="${3:-FCP Text Editor}"

RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[1;33m'
BLU='\033[0;34m'; RST='\033[0m'; BLD='\033[1m'

info()  { echo -e "${BLU}▸ $*${RST}"; }
ok()    { echo -e "${GRN}✓ $*${RST}"; }
warn()  { echo -e "${YEL}⚠ $*${RST}"; }
error() { echo -e "${RED}✗ $*${RST}"; exit 1; }

[[ ! -d "$APP_PATH" ]] && error "App bundle not found: $APP_PATH"

# Remove any existing DMG
[[ -f "$DMG_PATH" ]] && { info "Removing existing DMG…"; rm -f "$DMG_PATH"; }

# ── Use create-dmg if available (prettier result) ─────────────────────────────
if command -v create-dmg &>/dev/null; then
    info "Building DMG with create-dmg…"
    create-dmg \
        --volname "$APP_NAME" \
        --volicon "packaging/icon.icns" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 128 \
        --icon "$APP_NAME.app" 150 185 \
        --hide-extension "$APP_NAME.app" \
        --app-drop-link 450 185 \
        --no-internet-enable \
        "$DMG_PATH" \
        "$APP_PATH"
    ok "DMG created with create-dmg: $DMG_PATH"

# ── Fall back to hdiutil (always available on macOS) ──────────────────────────
else
    info "Building DMG with hdiutil (install 'create-dmg' for a prettier result)…"

    # Create a temp staging directory
    STAGE_DIR=$(mktemp -d)
    trap 'rm -rf "$STAGE_DIR"' EXIT

    # Copy app and add an Applications symlink for drag-to-install
    cp -R "$APP_PATH" "$STAGE_DIR/"
    ln -sf /Applications "$STAGE_DIR/Applications"

    APP_SIZE_KB=$(du -sk "$APP_PATH" | awk '{print $1}')
    DMG_SIZE_KB=$(( APP_SIZE_KB + 10240 ))   # 10 MB padding

    info "Creating ${DMG_SIZE_KB} KB DMG image…"

    hdiutil create \
        -srcfolder "$STAGE_DIR" \
        -volname "$APP_NAME" \
        -fs HFS+ \
        -fsargs "-c c=8,a=8,b=8" \
        -format UDBZ \
        -size "${DMG_SIZE_KB}k" \
        "$DMG_PATH"

    ok "DMG created: $DMG_PATH"
fi

DMG_SIZE=$(du -sh "$DMG_PATH" | awk '{print $1}')
echo
echo -e "  ${BLD}DMG${RST}: $DMG_PATH  (${DMG_SIZE})"
echo -e "  ${YEL}Share this .dmg with users.${RST}"
echo -e "  ${YEL}After notarizing, run:  xcrun stapler staple \"$DMG_PATH\"${RST}"
echo
