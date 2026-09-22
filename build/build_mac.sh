#!/usr/bin/env bash
#
# Сборка ParserOnSocial.app под macOS.
#
# Сборка предназначена только для Apple Silicon (arm64).
# Сборка намеренно детерминированная: PyInstaller получает
# target_arch явно, а после сборки проверяется архитектура
# главного бинарника и наличие Playwright driver.

set -euo pipefail

cd "$(dirname "$0")"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "ERROR: build_mac.sh must run on macOS." >&2
    exit 1
fi

MAC_ARCH="${MAC_ARCH:-$(uname -m)}"
if [[ "$MAC_ARCH" != "arm64" ]]; then
    echo "ERROR: unsupported MAC_ARCH=$MAC_ARCH (only arm64 is supported)." >&2
    exit 1
fi

export PYINSTALLER_TARGET_ARCH="$MAC_ARCH"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"

python3 -m venv .build-venv
# shellcheck disable=SC1091
source .build-venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements-build.txt

python - <<'PY'
import platform
import sys

print(f"Python: {sys.version}")
print(f"Python machine: {platform.machine()}")
print(f"Requested target: {__import__('os').environ['PYINSTALLER_TARGET_ARCH']}")
print(f"macOS deployment target: {__import__('os').environ['MACOSX_DEPLOYMENT_TARGET']}")
PY

rm -rf build_tmp dist

pyinstaller --clean --noconfirm \
    --workpath build_tmp \
    --distpath dist \
    ParserOnSocial.spec

APP="dist/ParserOnSocial.app"
BIN="$APP/Contents/MacOS/ParserOnSocial"
DRIVER="$APP/Contents/Frameworks/playwright/driver/node"

if [[ ! -d "$APP" ]]; then
    echo "ERROR: app bundle was not created: $APP" >&2
    exit 1
fi

if [[ ! -f "$BIN" ]]; then
    echo "ERROR: app executable missing: $BIN" >&2
    exit 1
fi

if [[ ! -f "$DRIVER" ]]; then
    echo "ERROR: Playwright driver missing: $DRIVER" >&2
    find "$APP/Contents" -maxdepth 4 -iname 'node' -o -path '*playwright*' | head -100 >&2 || true
    exit 1
fi

chmod +x "$DRIVER"

echo "--- Playwright driver architecture ---"
file "$DRIVER"
DRIVER_ARCHS="$(lipo -archs "$DRIVER" 2>/dev/null || true)"
if [[ -z "$DRIVER_ARCHS" ]]; then
    echo "ERROR: Playwright driver is not a readable Mach-O executable: $DRIVER" >&2
    exit 1
fi
if [[ "$DRIVER_ARCHS" != *"$MAC_ARCH"* ]]; then
    echo "ERROR: Playwright driver architecture mismatch. Expected $MAC_ARCH, got: $DRIVER_ARCHS" >&2
    exit 1
fi

echo "--- Final architecture ---"
file "$BIN"
lipo -archs "$BIN"

ARCHS="$(lipo -archs "$BIN")"
if [[ "$ARCHS" != *"arm64"* ]]; then
    echo "ERROR: expected arm64 executable, got: $ARCHS" >&2
    exit 1
fi

echo ""
echo "Готово: build/dist/ParserOnSocial.app"
