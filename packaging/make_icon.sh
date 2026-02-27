#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# make_icon.sh  —  Convert a PNG image to a macOS .icns app icon
#
# Usage:
#   ./packaging/make_icon.sh path/to/your_icon.png
#
# Requirements:
#   • A square PNG, ideally 1024×1024 px (will be resized automatically)
#   • macOS (uses built-in sips + iconutil)
#
# Output:
#   packaging/icon.icns   (used by fcp_editor.spec for the app bundle)
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[1;33m'
BLU='\033[0;34m'; RST='\033[0m'
info()  { echo -e "${BLU}▸ $*${RST}"; }
ok()    { echo -e "${GRN}✓ $*${RST}"; }
error() { echo -e "${RED}✗ $*${RST}"; exit 1; }

SRC="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ICONSET_DIR="$SCRIPT_DIR/icon.iconset"
ICNS_OUT="$SCRIPT_DIR/icon.icns"

[[ -z "$SRC" ]] && error "Usage: $0 path/to/icon.png"
[[ ! -f "$SRC" ]] && error "File not found: $SRC"

info "Source image: $SRC"
info "Creating iconset at: $ICONSET_DIR"

mkdir -p "$ICONSET_DIR"

# Required sizes for macOS app icons
declare -a SIZES=(16 32 64 128 256 512 1024)

for SIZE in "${SIZES[@]}"; do
    info "  Generating ${SIZE}×${SIZE}…"
    sips -z $SIZE $SIZE "$SRC" \
        --out "$ICONSET_DIR/icon_${SIZE}x${SIZE}.png" \
        --setProperty format png >/dev/null

    # @2x (Retina) version — double resolution
    HALF=$(( SIZE / 2 ))
    if [[ $HALF -ge 16 ]]; then
        cp "$ICONSET_DIR/icon_${SIZE}x${SIZE}.png" \
           "$ICONSET_DIR/icon_${HALF}x${HALF}@2x.png"
    fi
done

info "Running iconutil to produce .icns…"
iconutil -c icns "$ICONSET_DIR" -o "$ICNS_OUT"

ok "Icon created: $ICNS_OUT"
echo
echo "  Now rebuild the app:  ./build_macos.sh"
echo
