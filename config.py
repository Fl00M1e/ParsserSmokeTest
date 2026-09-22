from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


# ============================================================
# ПУТИ
# ============================================================

# Исходники/ресурсы приложения.
BASE_DIR = Path(__file__).resolve().parent


def _application_data_dir() -> Path:
    """Возвращает каталог пользовательских данных, доступный для записи."""

    if sys.platform == "darwin":
        # Никогда не хранить изменяемые данные внутри .app:
        # приложение может находиться в /Applications и быть read-only.
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "OnSocialLocalParser"
        )

    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return Path(root) / "OnSocialLocalParser"
        return Path.home() / "AppData" / "Local" / "OnSocialLocalParser"

    return Path.home() / ".local" / "share" / "OnSocialLocalParser"


APP_DATA_DIR = _application_data_dir()

# Профиль Chrome, который используется Playwright.
# Здесь сохраняется авторизация ON Social и Google.
BROWSER_PROFILE = APP_DATA_DIR / "browser_profile"


# ============================================================
# БРАУЗЕР
# ============================================================

# False = Chrome открывается обычным видимым окном.
HEADLESS = False


def find_system_chrome() -> Path | None:
    """Return the installed Chrome executable, if present."""

    home = Path.home()
    if sys.platform == "darwin":
        candidates = (
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            home / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            Path("/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
        )
    elif os.name == "nt":
        candidates = tuple(
            Path(root) / "Google/Chrome/Application/chrome.exe"
            for root in (
                os.environ.get("PROGRAMFILES"),
                os.environ.get("PROGRAMFILES(X86)"),
                os.environ.get("LOCALAPPDATA"),
            )
            if root
        )
    else:
        candidates = tuple(Path(value) for value in (
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
        ))

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    for name in ("google-chrome", "google-chrome-stable", "chrome"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


# ============================================================
# ON SOCIAL
# ============================================================

ONSOCIAL_START_URL = (
    "https://app.onsocial.ai/influencer-identification"
)


# ============================================================
# ТАЙМАУТЫ
# ============================================================

DEFAULT_TIMEOUT_MS = 30000
# No artificial throttling: synchronization waits below are kept only where
# the browser must finish navigation or render a new list.
ACTION_DELAY_MS = 0


# ============================================================
# ПАУЗА МЕЖДУ ПРОФИЛЯМИ (0 = без искусственного ожидания)
# ============================================================

MIN_PROFILE_DELAY_MS = 0
MAX_PROFILE_DELAY_MS = 0


# ============================================================
# ЛИМИТЫ
# ============================================================

DEFAULT_MAX_INFLUENCERS = 100
DEFAULT_MAX_UNLOCKS = 10


# ============================================================
# CONTACTS
# ============================================================

CONTACTS_HEADING = "Contacts"

CONTACTS_END_MARKERS = [
    "Audience",
    "Report",
    "View More",
    "Posts",
    "Content",
]


# ============================================================
# UNLOCK
# ============================================================

UNLOCK_BUTTON_RE = r"^\s*Unlock next(?:\s+\d+)?\s*$"
