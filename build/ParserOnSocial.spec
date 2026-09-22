# -*- mode: python ; coding: utf-8 -*-
#
# Спека PyInstaller для сборки macOS .app.
#
# ВАЖНО: собирать нужно ЗАПУСКАЯ pyinstaller НА macOS
# (или через GitHub Actions runner macos-latest — см.
# .github/workflows/build-macos.yml в корне проекта).
# PyInstaller не умеет кросс-компилировать с Windows на macOS.
#
# Playwright хранит внутри своего pip-пакета отдельный
# "driver" (обвязку с Node.js рантаймом), через который
# запускается браузер. Этот driver платформо-специфичен —
# он появится правильным только если `pip install playwright`
# выполнялся на macOS. Ниже мы находим этот driver в
# установленном окружении и явно подключаем его как данные,
# иначе собранное приложение не сможет запустить браузер.

import glob
import os

import playwright

block_cipher = None

playwright_pkg_dir = os.path.dirname(playwright.__file__)
driver_dir = os.path.join(playwright_pkg_dir, "driver")

driver_datas = []
if os.path.isdir(driver_dir):
    for path in glob.glob(os.path.join(driver_dir, "**"), recursive=True):
        if os.path.isfile(path):
            rel = os.path.relpath(path, playwright_pkg_dir)
            dest_dir = os.path.dirname(os.path.join("playwright", rel))
            driver_datas.append((path, dest_dir))

target_arch = os.environ.get("PYINSTALLER_TARGET_ARCH")
if target_arch not in {None, "arm64"}:
    raise SystemExit(f"Unsupported PYINSTALLER_TARGET_ARCH: {target_arch}; only arm64 is supported")

a = Analysis(
    ["../launcher.py"],
    pathex=["../"],
    binaries=[],
    datas=driver_datas,
    hiddenimports=[
        "playwright.sync_api",
        "selftest",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ParserOnSocial",
    debug=False,
    target_arch=target_arch,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="ParserOnSocial",
)

app = BUNDLE(
    coll,
    name="ParserOnSocial.app",
    icon=None,
    bundle_identifier="local.parseronsocial.app",
    info_plist={
        "CFBundleShortVersionString": "1.1.0",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
    },
)
