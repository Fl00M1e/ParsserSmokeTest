from __future__ import annotations

import os
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
ACTION_DELAY_MS = 500


# ============================================================
# АНТИ-БАН: СЛУЧАЙНАЯ ЗАДЕРЖКА МЕЖДУ БЛОГЕРАМИ
# ============================================================

MIN_PROFILE_DELAY_MS = 2500
MAX_PROFILE_DELAY_MS = 6500


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
