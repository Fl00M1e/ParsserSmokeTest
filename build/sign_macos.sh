#!/usr/bin/env bash
# Sign a PyInstaller macOS .app for distribution outside the Mac App Store.
#
# Usage:
#   CODESIGN_IDENTITY='Developer ID Application: Your Name (TEAMID)' \
#     build/sign_macos.sh build/dist/ParserOnSocial.app
#
# The script signs Mach-O payloads from the inside out and then the outer
# application bundle, using a secure timestamp and hardened runtime.

set -euo pipefail

APP_PATH="${1:-}"
IDENTITY="${CODESIGN_IDENTITY:-}"

if [[ -z "$APP_PATH" ]]; then
  echo "Usage: CODESIGN_IDENTITY='Developer ID Application: ...' $0 path/to/App.app" >&2
  exit 2
fi
if [[ -z "$IDENTITY" ]]; then
  echo "ERROR: CODESIGN_IDENTITY is not set." >&2
  exit 2
fi
if [[ ! -d "$APP_PATH" ]]; then
  echo "ERROR: app bundle not found: $APP_PATH" >&2
  exit 1
fi

sign() {
  local path="$1"
  echo "Signing: $path"
  codesign --force --timestamp --options runtime --sign "$IDENTITY" "$path"
}

# Sign every Mach-O file first. This avoids relying on --deep for the actual
# signing operation and makes the final bundle deterministic.
while IFS= read -r -d '' path; do
  if file -b "$path" | grep -q 'Mach-O'; then
    sign "$path"
  fi
done < <(find "$APP_PATH/Contents" -type f -print0 | sort -z -r)

# Sign nested code containers from deepest to shallowest.
while IFS= read -r -d '' path; do
  case "$path" in
    *.framework|*.bundle|*.plugin|*.xpc|*.appex|*.app)
      sign "$path" ;;
  esac
done < <(find "$APP_PATH/Contents" -depth \( -name '*.framework' -o -name '*.bundle' -o -name '*.plugin' -o -name '*.xpc' -o -name '*.appex' -o -name '*.app' \) -type d -print0 | sort -z -r)

# Finally sign the outer app bundle.
sign "$APP_PATH"

echo "--- Developer ID signature ---"
codesign --display --verbose=4 "$APP_PATH" 2>&1

echo "--- Strict verification ---"
codesign --verify --deep --strict --verbose=4 "$APP_PATH"

echo "SIGNED_APP_OK"
