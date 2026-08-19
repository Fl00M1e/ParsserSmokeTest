from __future__ import annotations

import os
import platform
import shutil
import sys
import tempfile
import traceback
from pathlib import Path


def _chrome_executable_candidates() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        return [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            home / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome",
            Path("/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
        ]
    if os.name == "nt":
        roots = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        return [
            Path(root) / "Google/Chrome/Application/chrome.exe"
            for root in roots
            if root
        ]
    return [Path(p) for p in ("/usr/bin/google-chrome", "/usr/bin/google-chrome-stable")]


def _find_chrome() -> Path | None:
    for candidate in _chrome_executable_candidates():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    for name in ("google-chrome", "google-chrome-stable", "chrome"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _application_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OnSocialLocalParser"
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return Path(root) / "OnSocialLocalParser"
        return Path.home() / "AppData" / "Local" / "OnSocialLocalParser"
    return Path.home() / ".local" / "share" / "OnSocialLocalParser"


def run_self_test() -> int:
    print("=== ParserOnSocial self-test ===")
    print(f"Python: {sys.version}")
    print(f"Machine: {platform.machine()}")
    print(f"Platform: {sys.platform}")
    print(f"Frozen: {getattr(sys, 'frozen', False)}")
    if sys.platform == "darwin":
        print(f"Executable: {sys.executable}")

    data_dir = _application_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    probe = data_dir / ".selftest-write-probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)
    print(f"PASS: writable application-data directory: {data_dir}")

    import config  # noqa: F401
    import parser  # noqa: F401
    import sheets  # noqa: F401
    import app  # noqa: F401
    print("PASS: application Python modules imported")

    import playwright
    from playwright.sync_api import sync_playwright

    pkg_dir = Path(playwright.__file__).resolve().parent
    driver = pkg_dir / "driver" / ("node.exe" if os.name == "nt" else "node")
    if not driver.is_file():
        raise RuntimeError(f"Playwright driver is missing: {driver}")
    if not os.access(driver, os.X_OK):
        raise RuntimeError(f"Playwright driver is not executable: {driver}")
    print(f"PASS: Playwright driver exists: {driver}")

    chrome = _find_chrome()
    if chrome is None:
        raise RuntimeError(
            "Google Chrome was not found. ParserOnSocial uses channel=\"chrome\" "
            "and therefore requires Google Chrome to be installed on the Mac."
        )
    print(f"PASS: Google Chrome found: {chrome}")

    with tempfile.TemporaryDirectory(prefix="onsocial-selftest-") as temp_profile:
        with sync_playwright() as pw:
            print("PASS: Playwright driver started")
            context = None
            try:
                context = pw.chromium.launch_persistent_context(
                    temp_profile,
                    channel="chrome",
                    headless=True,
                    viewport={"width": 1280, "height": 800},
                    args=["--disable-gpu"],
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("about:blank", wait_until="domcontentloaded", timeout=15000)
                page.set_content("<html><body><h1>ParserOnSocial self-test</h1></body></html>")
                if page.locator("h1").inner_text() != "ParserOnSocial self-test":
                    raise RuntimeError("Playwright page interaction test returned unexpected content")
                print("PASS: persistent Chrome profile launched and page interaction works")
            finally:
                if context is not None:
                    context.close()

    print("PASS: core runtime, Playwright and Chrome checks completed")
    print("SELF-TEST PASSED")
    return 0


def main() -> int:
    try:
        return run_self_test()
    except BaseException:
        traceback.print_exc()
        print("SELF-TEST FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
