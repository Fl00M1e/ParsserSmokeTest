# -*- mode: python ; coding: utf-8 -*-
#
# Спека PyInstaller для сборки Windows .exe (один файл).
#
# Собирать нужно ЗАПУСКАЯ pyinstaller НА Windows — как раз то
# окружение, где у вас уже есть venv с проектом. Кросс-сборка
# с macOS/Linux на Windows не поддерживается PyInstaller'ом.
#
# Как и в mac-спеке, отдельно подключаем playwright "driver"
# (внутренний Node.js-рантайм пакета playwright) как данные —
# иначе собранный exe не сможет запустить браузер.

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

a = Analysis(
    [os.path.join(SPECPATH, "..", "launcher.py")],
    pathex=[os.path.join(SPECPATH, "..")],
    binaries=[],
    datas=driver_datas,
    hiddenimports=[
        "playwright.sync_api",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# --onefile: всё (код + датасы + бинарники) упаковано в один
# exe-файл. exclude_binaries=False и передача a.binaries/a.datas
# прямо в EXE — это и есть режим one-file.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ParserOnSocial",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon=None,
)
