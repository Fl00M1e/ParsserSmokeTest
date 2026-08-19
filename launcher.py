from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path


def _log_path() -> Path:
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "OnSocialLocalParser"
    else:
        root = Path.home() / ".local" / "share" / "OnSocialLocalParser"
    root.mkdir(parents=True, exist_ok=True)
    return root / "startup-error.log"


def _show_error(message: str) -> None:
    if sys.platform == "darwin":
        try:
            # AppleScript gives a visible error even though the PyInstaller
            # app is built without a console window.
            escaped = message.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display alert "ParserOnSocial" message "{escaped}" buttons {{"OK"}} default button "OK"',
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception:
            pass

    try:
        sys.stderr.write(message + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def main() -> None:
    try:
        from app import main as app_main

        app_main()
    except BaseException:
        details = traceback.format_exc()
        try:
            path = _log_path()
            path.write_text(details, encoding="utf-8")
        except Exception:
            path = None

        hint = (
            "Приложение не смогло запуститься.\n\n"
            "Техническая ошибка записана в:\n"
            f"{path if path else 'Library/Application Support/OnSocialLocalParser/startup-error.log'}\n\n"
            "Проверьте, что установлен Google Chrome и используете сборку для Apple Silicon (arm64)."
        )
        _show_error(hint)
        raise


if __name__ == "__main__":
    main()
