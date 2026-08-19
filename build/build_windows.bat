@echo off
REM Собирает ParserOnSocial.exe (один файл).
REM Запускать на Windows, из папки build\.
REM
REM Приложение использует системный Google Chrome
REM (channel="chrome" в app.py), поэтому Chrome должен быть
REM установлен и на машине, где собираем, и на машине, где
REM потом будут запускать готовый exe.

cd /d "%~dp0"

python -m venv .build-venv
call .build-venv\Scripts\activate.bat

python -m pip install --upgrade pip
pip install -r requirements-build.txt

rmdir /s /q build_tmp 2>nul
rmdir /s /q dist 2>nul

pyinstaller --clean --noconfirm --workpath build_tmp --distpath dist ParserOnSocial.win.spec

call .build-venv\Scripts\deactivate.bat

echo.
echo Готово: build\dist\ParserOnSocial.exe
pause
