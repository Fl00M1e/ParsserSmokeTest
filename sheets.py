from __future__ import annotations

import csv
import io
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit
from dedup import extract_pairs, filter_new_rows, normalize_social_url, normalize_email
from typing import Iterable, Optional

import pyperclip
from playwright.sync_api import BrowserContext, Page


# ============================================================
# GOOGLE SHEETS HOTKEYS
# ============================================================
# Cmd+Space на macOS — системный Spotlight, поэтому для выбора
# колонки Google Sheets всегда используем Control+Space.
# Копирование/вставка используют Cmd на macOS и Ctrl на Windows.

COPY_MOD = "Meta" if sys.platform == "darwin" else "Control"
COLUMN_SELECT_MOD = "Control"


class SheetsSafetyError(RuntimeError):
    """A sheet operation cannot safely continue without a fresh snapshot."""


class GoogleSheetsWriter:
    """
    Google Sheets writer БЕЗ Google Sheets API.

    Работа происходит исключительно через обычный интерфейс
    Google Sheets в уже открытом браузере.

    Используется:
        - Playwright
        - пользовательская Google-сессия браузера
        - системный Clipboard
        - Ctrl+V

    API НЕ используется.
    credentials.json НЕ используется.
    token.json НЕ используется.

    Начальная ячейка задаётся пользователем в приложении:
        A2
        B5
        D10
        и т.д.

    После каждой записи курсор автоматически перемещается
    вниз на количество записанных строк.
    """

    def __init__(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        browser_context: Optional[BrowserContext] = None,
        log=print,
    ):
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name
        self.browser_context = browser_context
        self.log = log

        self.sheet_page: Optional[Page] = None
        self.connected = False
        self.write_blocked = False
        self.verified_pairs: set[tuple[str, str]] = set()
        # Pairs confirmed by the latest paste.
        self.last_verified_pairs: set[tuple[str, str]] = set()

        # --------------------------------------------------------
        # Начальная позиция записи.
        #
        # По умолчанию A2, но app.py обязательно может изменить
        # её через set_start_cell().
        # --------------------------------------------------------

        self.start_cell = "A2"

        # Текущая ячейка, куда будет записываться следующая строка.
        self.current_row = 2
        self.current_column = 1

    # ============================================================
    # SPREADSHEET ID
    # ============================================================

    @staticmethod
    def extract_spreadsheet_id(value: str) -> str:
        value = (value or "").strip()

        if "/spreadsheets/d/" in value:
            return (
                value
                .split("/spreadsheets/d/", 1)[1]
                .split("/", 1)[0]
                .split("?", 1)[0]
                .split("#", 1)[0]
            )

        return value

    # ============================================================
    # START CELL
    # ============================================================

    def set_start_cell(self, cell: str):
        """
        Устанавливает начальную ячейку.

        Например:

            A2
            B5
            D10
            AA25

        Именно эта ячейка будет первой ячейкой первой
        записываемой строки.
        """

        cell = (cell or "").strip().upper()

        column, row = self._parse_cell(cell)

        self.start_cell = cell
        self.current_column = column
        self.current_row = row

        self.log(
            f"Google Sheets: начальная ячейка установлена: {cell}"
        )

    # ============================================================
    # CONNECT
    # ============================================================

    def connect(self):
        if not self.browser_context:
            raise RuntimeError(
                "Браузер ещё не открыт. "
                "Сначала нажми «1. Открыть ON Social»."
            )

        url = (
            f"https://docs.google.com/spreadsheets/d/"
            f"{self.spreadsheet_id}/edit"
        )

        self.log(
            "Открываю Google Sheets через браузер..."
        )

        # --------------------------------------------------------
        # Ищем уже открытую вкладку Google Sheets.
        # --------------------------------------------------------

        self.sheet_page = None

        for page in self.browser_context.pages:
            try:
                if "docs.google.com/spreadsheets" in page.url:
                    self.sheet_page = page
                    break
            except Exception:
                continue

        # --------------------------------------------------------
        # Если вкладки нет — создаём.
        # --------------------------------------------------------

        if self.sheet_page is None:
            self.sheet_page = self.browser_context.new_page()

            self.sheet_page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=30000,
            )

        # --------------------------------------------------------
        # Если открыта другая таблица — переходим в нужную.
        # --------------------------------------------------------

        elif self.spreadsheet_id not in self.sheet_page.url:
            self.sheet_page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=30000,
            )

        self.sheet_page.bring_to_front()

        self.log(
            "Google Sheets открыта."
        )

        self.log(
            "Если Google просит авторизацию — войди вручную."
        )

        # --------------------------------------------------------
        # Ждём загрузки интерфейса.
        # --------------------------------------------------------

        self.sheet_page.wait_for_timeout(3000)

        # --------------------------------------------------------
        # Пытаемся выбрать нужный лист.
        # --------------------------------------------------------

        self._select_sheet_tab()

        self.connected = True

        self.log(
            f"Google Sheets: готово — лист «{self.sheet_name}»"
        )

        self.log(
            "Режим работы: БЕЗ Google Sheets API."
        )

        self.log(
            f"Первая ячейка записи: {self.start_cell}"
        )

    # ============================================================
    # SELECT SHEET TAB
    # ============================================================

    def _select_sheet_tab(self):
        """
        Пытается активировать указанный лист.

        Google Sheets меняет DOM со временем, поэтому используем
        несколько вариантов поиска.

        Если лист уже активен — ничего страшного не произойдёт.
        """

        page = self.sheet_page

        if page is None:
            return

        name = self.sheet_name.strip()

        if not name:
            return

        # --------------------------------------------------------
        # Вариант 1: ARIA tab
        # --------------------------------------------------------

        try:
            tabs = page.locator('[role="tab"]')

            count = tabs.count()

            for i in range(count):
                tab = tabs.nth(i)

                try:
                    if not tab.is_visible():
                        continue

                    text = (tab.inner_text() or "").strip()

                    if text == name:
                        tab.click()
                        page.wait_for_timeout(500)

                        self.log(
                            f"Google Sheets: выбран лист «{name}»."
                        )

                        return

                except Exception:
                    continue

        except Exception:
            pass

        # --------------------------------------------------------
        # Вариант 2: aria-label
        # --------------------------------------------------------

        selectors = [
            f'[aria-label="{name}"]',
            f'[data-tooltip="{name}"]',
        ]

        for selector in selectors:
            try:
                locator = page.locator(selector)

                count = locator.count()

                for i in range(count):
                    item = locator.nth(i)

                    if item.is_visible():
                        item.click()
                        page.wait_for_timeout(500)

                        self.log(
                            f"Google Sheets: выбран лист «{name}»."
                        )

                        return

            except Exception:
                continue

        # --------------------------------------------------------
        # Если не нашли.
        #
        # Не падаем, потому что часто нужный лист уже активен.
        # --------------------------------------------------------

        self.log(
            f"Google Sheets: не удалось автоматически найти "
            f"вкладку «{name}». Если она уже активна — всё нормально."
        )

    # ============================================================
    # LOAD ALL TABS FOR DEDUPLICATION
    # ============================================================

    def _open_all_sheets_menu(self) -> bool:
        """Open Google Sheets' 'All sheets' popup if available."""
        page = self.sheet_page
        if page is None:
            raise RuntimeError("Страница Google Sheets недоступна.")

        selectors = [
            '[aria-label*="All sheets"]',
            '[data-tooltip*="All sheets"]',
            '[title*="All sheets"]',
            '[aria-label*="Все листы"]',
            '[data-tooltip*="Все листы"]',
            '[title*="Все листы"]',
            'button[aria-label*="All sheets"]',
            'button[aria-label*="Все листы"]',
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = min(loc.count(), 20)
                for i in range(count):
                    item = loc.nth(i)
                    try:
                        if item.is_visible():
                            item.click()
                            page.wait_for_timeout(300)
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    def _close_all_sheets_menu(self):
        try:
            self.sheet_page.keyboard.press("Escape")
            self.sheet_page.wait_for_timeout(150)
        except Exception:
            pass

    def _normalize_sheet_label(self, value: str) -> str:
        value = re.sub(r"\s+", " ", (value or "")).strip()
        value = re.sub(r"\s+[⌘⌥⇧⌃].*$", "", value).strip()
        return value

    def _collect_rendered_sheet_names(self) -> list[str]:
        """Collect names only from DOM nodes that look like actual sheet tabs."""
        page = self.sheet_page
        if page is None:
            return []

        names: list[str] = []
        # Google Sheets' tab DOM has historically used docs-sheet-tab / docs-sheet-tab-name.
        # We deliberately do NOT use generic [role=tab], because Google Sheets' top
        # application menu can expose unrelated nodes with tab-like ARIA roles.
        selectors = [
            '.docs-sheet-tab',
            '[class*="docs-sheet-tab"]',
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = min(loc.count(), 300)
                for i in range(count):
                    item = loc.nth(i)
                    try:
                        if not item.is_visible():
                            continue
                        label = (
                            item.locator('.docs-sheet-tab-name').first.text_content()
                            if item.locator('.docs-sheet-tab-name').count()
                            else None
                        )
                        label = label or item.get_attribute('data-tooltip') or item.get_attribute('aria-label') or item.inner_text()
                        label = self._normalize_sheet_label(label)
                        if not label:
                            continue
                        low = label.casefold()
                        if low in {'all sheets', 'все листы'}:
                            continue
                        # Hard block common Google Sheets application-menu labels. These are
                        # never sheet names and this prevents a false-positive fallback.
                        if low in {
                            'файл', 'правка', 'вид', 'вставка', 'формат', 'данные',
                            'инструменты', 'расширения', 'справка',
                            'file', 'edit', 'view', 'insert', 'format', 'data',
                            'tools', 'extensions', 'help',
                        }:
                            continue
                        if label not in names:
                            names.append(label)
                    except Exception:
                        continue
                if names:
                    break
            except Exception:
                continue
        return names

    def _collect_all_sheets_menu_names(self) -> list[str]:
        """Collect sheet names from the real 'All sheets' popup only."""
        page = self.sheet_page
        if page is None:
            return []
        names: list[str] = []
        # Prefer the sheet-specific menu item classes. Only fall back to generic roles
        # after confirming that the popup is actually open.
        selectors = [
            '.docs-sheet-tab-menu-item',
            '[class*="docs-sheet-tab-menu"]',
            '[data-sheet-id]',
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = min(loc.count(), 300)
                for i in range(count):
                    item = loc.nth(i)
                    try:
                        if not item.is_visible():
                            continue
                        label = item.get_attribute('aria-label') or item.get_attribute('data-tooltip')
                        if not label:
                            if item.locator('.docs-sheet-tab-name').count():
                                label = item.locator('.docs-sheet-tab-name').first.text_content()
                            else:
                                label = item.inner_text()
                        label = self._normalize_sheet_label(label)
                        if not label:
                            continue
                        low = label.casefold()
                        if low in {'all sheets', 'все листы'}:
                            continue
                        if low in {
                            'файл', 'правка', 'вид', 'вставка', 'формат', 'данные',
                            'инструменты', 'расширения', 'справка',
                            'file', 'edit', 'view', 'insert', 'format', 'data',
                            'tools', 'extensions', 'help',
                        }:
                            continue
                        if label not in names:
                            names.append(label)
                    except Exception:
                        continue
                if names:
                    return names
            except Exception:
                continue
        return names

    def list_sheet_tabs(self) -> list[str]:
        """
        Return the real Google Sheets worksheet names only.

        The implementation intentionally avoids generic ARIA roles such as
        [role="tab"] / [role="menuitem"] because Google Sheets can expose
        unrelated application-menu elements through those roles. We use the
        sheet-specific DOM classes first and only accept names that survive the
        strict filters above.
        """
        self._require_connection()
        page = self.sheet_page
        if page is None:
            raise RuntimeError("Страница Google Sheets недоступна.")

        names: list[str] = []

        # 1. Open the dedicated 'All sheets' popup and read only sheet-specific nodes.
        if self._open_all_sheets_menu():
            try:
                page.wait_for_timeout(250)
                names = self._collect_all_sheets_menu_names()
            finally:
                self._close_all_sheets_menu()

        # 2. Fallback to the actual rendered worksheet tabs, never generic role=tab.
        if not names:
            names = self._collect_rendered_sheet_names()

        if not names:
            raise RuntimeError(
                "Не удалось определить реальные листы Google Sheets. "
                "Загрузка остановлена, чтобы бот не прочитал меню Google Sheets вместо листов."
            )

        self.log("Google Sheets: обнаружены реальные листы: " + ", ".join(f'«{n}»' for n in names))
        return names

    def _select_sheet_specific_element(self, target: str) -> bool:
        """Click a visible real worksheet-tab DOM element by exact label."""
        page = self.sheet_page
        if page is None:
            return False
        target = self._normalize_sheet_label(target)
        selectors = ['.docs-sheet-tab', '[class*="docs-sheet-tab"]']
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = min(loc.count(), 300)
                for i in range(count):
                    item = loc.nth(i)
                    try:
                        if not item.is_visible():
                            continue
                        label = None
                        if item.locator('.docs-sheet-tab-name').count():
                            label = item.locator('.docs-sheet-tab-name').first.text_content()
                        label = label or item.get_attribute('data-tooltip') or item.get_attribute('aria-label') or item.inner_text()
                        label = self._normalize_sheet_label(label)
                        if label == target:
                            item.click()
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    def _activate_sheet_tab_by_name(self, name: str):
        """Activate one real Google Sheets worksheet tab by exact name.

        This is the single entry point used by full-table loading.  It deliberately
        delegates to the strict sheet-specific selector so application menu items
        such as «Файл», «Правка» and «Вид» can never be selected as worksheet tabs.
        """
        page = self.sheet_page
        if page is None:
            raise RuntimeError("Страница Google Sheets недоступна.")

        target = self._normalize_sheet_label(name)
        if not target:
            raise RuntimeError("Пустое имя листа Google Sheets.")

        self._select_sheet_tab_name(target)
        page.wait_for_timeout(600)

    def _select_sheet_tab_name(self, name: str):
        """Strictly activate a real worksheet tab, including hidden/overflowed tabs."""
        page = self.sheet_page
        if page is None:
            raise RuntimeError("Страница Google Sheets недоступна.")
        target = self._normalize_sheet_label(name)

        # Visible worksheet tab.
        if self._select_sheet_specific_element(target):
            return

        # Hidden/overflowed worksheet: use the dedicated All sheets popup.
        if self._open_all_sheets_menu():
            try:
                page.wait_for_timeout(250)
                selectors = [
                    '.docs-sheet-tab-menu-item',
                    '[class*="docs-sheet-tab-menu"]',
                    '[data-sheet-id]',
                ]
                for selector in selectors:
                    try:
                        loc = page.locator(selector)
                        count = min(loc.count(), 300)
                        for i in range(count):
                            item = loc.nth(i)
                            try:
                                if not item.is_visible():
                                    continue
                                label = item.get_attribute('aria-label') or item.get_attribute('data-tooltip')
                                if not label:
                                    if item.locator('.docs-sheet-tab-name').count():
                                        label = item.locator('.docs-sheet-tab-name').first.text_content()
                                    else:
                                        label = item.inner_text()
                                label = self._normalize_sheet_label(label)
                                if label == target:
                                    item.click()
                                    page.wait_for_timeout(450)
                                    return
                            except Exception:
                                continue
                    except Exception:
                        continue
            finally:
                self._close_all_sheets_menu()

        raise RuntimeError(
            f"Не удалось активировать реальный лист «{name}». "
            "Загрузка всей таблицы остановлена."
        )

    def _get_sheet_gid_by_name(self, name: str) -> str:
        """Return the current worksheet gid using several robust fallbacks.

        Google Sheets changes its internal tab DOM frequently. The previous
        implementation depended on a ``data-sheet-id`` attribute that is not
        present in some Chrome/Sheets builds. After activating the target sheet,
        the most reliable public signal is the ``#gid=...`` fragment in the
        current Sheets URL. We also keep DOM attribute fallbacks for versions
        where that fragment is not updated synchronously.
        """
        page = self.sheet_page
        if page is None:
            raise RuntimeError("Страница Google Sheets недоступна.")

        target = self._normalize_sheet_label(name)

        # 1) The selected sheet is reflected in the page URL as #gid=<id>.
        try:
            page.wait_for_timeout(300)
            current_url = page.url or ""
            match = re.search(r"(?:#|[?&])gid=(\d+)", current_url)
            if match:
                gid = match.group(1)
                self.log(
                    f"Google Sheets: gid листа «{target}» определён из URL: {gid}."
                )
                return gid
        except Exception:
            pass

        def scan(selector: str) -> Optional[str]:
            try:
                loc = page.locator(selector)
                count = min(loc.count(), 300)
                for i in range(count):
                    item = loc.nth(i)
                    try:
                        if not item.is_visible():
                            continue
                        label = None
                        if item.locator('.docs-sheet-tab-name').count():
                            label = item.locator('.docs-sheet-tab-name').first.text_content()
                        label = label or item.get_attribute('data-tooltip') or item.get_attribute('aria-label') or item.inner_text()
                        label = self._normalize_sheet_label(label)
                        if label != target:
                            continue
                        for attr in (
                            'data-sheet-id', 'data-id', 'data-sheet-id-value',
                            'data-sheetid', 'data-gid',
                        ):
                            gid = item.get_attribute(attr)
                            if gid and str(gid).strip().isdigit():
                                return str(gid).strip()
                    except Exception:
                        continue
            except Exception:
                return None
            return None

        # 2) Visible worksheet tab DOM fallbacks.
        for selector in ('.docs-sheet-tab', '[class*="docs-sheet-tab"]'):
            gid = scan(selector)
            if gid:
                self.log(
                    f"Google Sheets: gid листа «{target}» определён из DOM: {gid}."
                )
                return gid

        # 3) Overflow/hidden sheet via All Sheets menu.
        if self._open_all_sheets_menu():
            try:
                page.wait_for_timeout(250)
                for selector in (
                    '.docs-sheet-tab-menu-item',
                    '[data-sheet-id]',
                    '[class*="docs-sheet-tab-menu"]',
                ):
                    gid = scan(selector)
                    if gid:
                        self.log(
                            f"Google Sheets: gid листа «{target}» определён из меню: {gid}."
                        )
                        return gid
            finally:
                self._close_all_sheets_menu()

        raise RuntimeError(
            f"Не удалось определить gid листа «{name}» даже после активации листа. "
            "Чтение Google Sheets остановлено."
        )

    def _read_active_sheet_matrix_via_export(self, sheet_name: str) -> list[list[str]]:
        """Read a CSV snapshot; HTML, failed requests and ambiguous formats fail closed."""
        self._require_connection()
        page = self.sheet_page
        expected = f"/spreadsheets/d/{self.spreadsheet_id}/"
        parts = urlsplit(page.url)
        if parts.scheme != "https" or parts.hostname != "docs.google.com" or not parts.path.startswith(expected):
            raise SheetsSafetyError("Открыта другая таблица или страница входа Google.")
        page.bring_to_front()
        self._activate_sheet_tab_by_name(sheet_name)
        gid = self._get_sheet_gid_by_name(sheet_name)
        url = (
            f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}/export"
            f"?format=csv&gid={gid}&_onsocial={time.time_ns()}"
        )
        response = None
        try:
            response = self.browser_context.request.get(
                url, timeout=30000, headers={"Cache-Control": "no-cache"}
            )
            media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if response.status != 200 or media_type not in {
                "text/csv", "application/csv", "application/vnd.ms-excel",
                "application/octet-stream",
            }:
                raise SheetsSafetyError(
                    f"Google Sheets: CSV не получен (HTTP {response.status}, {media_type})."
                )
            raw = response.text().lstrip("\ufeff")
            if re.match(r"\s*<(?:!doctype|html|head|body)\b", raw, re.I):
                raise SheetsSafetyError("Google вернул HTML вместо данных листа.")
            matrix = list(csv.reader(io.StringIO(raw), strict=True))
            while matrix and not any(cell.strip() for cell in matrix[-1]):
                matrix.pop()
            return matrix
        except SheetsSafetyError:
            raise
        except Exception as exc:
            raise SheetsSafetyError("Не удалось надёжно прочитать целевой лист. Запись остановлена.") from exc
        finally:
            if response is not None:
                response.dispose()

    def _read_active_sheet_matrix(self) -> list[list[str]]:
        """
        Надёжно копирует весь используемый диапазон активного листа.

        Важно: Google Sheets иногда не отдаёт Clipboard, если фокус остаётся
        в Name Box или если первый Ctrl+A сработал не по сетке. Поэтому здесь
        несколько попыток с возвратом фокуса в сетку, более длинным ожиданием и
        дополнительной проверкой через browser Clipboard API.
        """
        self._require_connection()
        page = self.sheet_page
        if page is None:
            raise RuntimeError("Страница Google Sheets недоступна.")

        page.bring_to_front()
        self._goto_cell("A1")
        page.wait_for_timeout(500)

        sentinel = "__ONSOCIAL_FULL_SHEET_SENTINEL_8F2A__"

        def read_clipboard() -> str:
            # На macOS читаем системный pasteboard напрямую через pbpaste.
            # Это надёжнее pyperclip для приложений, запущенных из Finder/VS Code,
            # и не зависит от выбора backend pyperclip.
            if sys.platform == "darwin":
                try:
                    result = subprocess.run(
                        ["/usr/bin/pbpaste"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    value = result.stdout or ""
                    if value and value != sentinel:
                        return value
                except Exception:
                    pass

            # Windows/Linux и запасной вариант для macOS.
            try:
                value = pyperclip.paste() or ""
                if value and value != sentinel:
                    return value
            except Exception:
                pass

            # Затем пробуем Clipboard API самой страницы.
            try:
                value = page.evaluate(
                    "navigator.clipboard.readText()"
                ) or ""
                if value and value != sentinel:
                    return value
            except Exception:
                pass
            return ""

        def write_sentinel() -> None:
            if sys.platform == "darwin":
                try:
                    subprocess.run(
                        ["/usr/bin/pbcopy"],
                        input=sentinel,
                        text=True,
                        capture_output=True,
                        timeout=2,
                    )
                    return
                except Exception:
                    pass
            pyperclip.copy(sentinel)

        last_error = ""
        clipboard_backend = "pbpaste/pbcopy + pyperclip" if sys.platform == "darwin" else "pyperclip"
        self.log(f"Google Sheets: проверяю системный Clipboard через {clipboard_backend}.")
        for attempt in range(1, 6):
            try:
                # Гарантируем фокус на самой таблице, а не в Name Box.
                # Клик немного правее/ниже Name Box обычно попадает в grid.
                try:
                    page.mouse.click(650, 320)
                    page.wait_for_timeout(150)
                except Exception:
                    pass

                write_sentinel()

                # Escape снимает возможный фокус/редактирование ячейки.
                page.keyboard.press("Escape")
                page.wait_for_timeout(100)

                # В Google Sheets: Ctrl+A первый раз = текущий регион данных,
                # второй = весь используемый диапазон листа.
                page.keyboard.press(f"{COPY_MOD}+A")
                page.wait_for_timeout(250)
                page.keyboard.press(f"{COPY_MOD}+A")
                page.wait_for_timeout(400)
                page.keyboard.press(f"{COPY_MOD}+C")

                deadline = time.time() + 10.0
                raw = ""
                while time.time() < deadline:
                    page.wait_for_timeout(250)
                    raw = read_clipboard()
                    if raw:
                        break

                if raw:
                    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                    while lines and not lines[-1].strip():
                        lines.pop()
                    matrix = [line.split("\t") for line in lines]
                    self.log(
                        f"Google Sheets: Clipboard успешно прочитан с попытки {attempt}; "
                        f"строк: {len(matrix):,}"
                    )
                    return matrix

                last_error = "Clipboard остался пустым или равен sentinel."
            except Exception as exc:
                last_error = repr(exc)

            self.log(
                f"Google Sheets: не удалось получить Clipboard с попытки {attempt}/5: {last_error}"
            )
            page.wait_for_timeout(900)

        raise RuntimeError(
            "Google Sheets не вернул содержимое активной вкладки в Clipboard "
            f"после 5 попыток. Последняя причина: {last_error}"
        )

    _extract_pairs_from_matrix = staticmethod(extract_pairs)

    def load_target_sheet_profile_email_pairs(self) -> set[tuple[str, str]]:
        """
        Загружает комбинации «соцсеть + email» ТОЛЬКО из целевого листа.

        Целевой лист — это ``self.sheet_name``, тот же лист, в который
        приложение будет записывать найденных блогеров. Другие листы
        таблицы вообще не читаются.
        """
        self._require_connection()

        target_name = self._normalize_sheet_label(self.sheet_name)
        if not target_name:
            raise RuntimeError(
                "Не задан целевой лист Google Sheets, в который будут записываться блогеры."
            )

        self.log("========================================")
        self.log("ЗАГРУЗКА ДАННЫХ ЦЕЛЕВОГО ЛИСТА")
        self.log(
            f"Читаю только лист «{target_name}» — именно в него будут записываться новые блогеры."
        )

        # Переключаемся ровно на тот лист, который используется для записи.
        self._activate_sheet_tab_by_name(target_name)
        self.log(f"Google Sheets: активирован целевой лист «{target_name}».")

        matrix = self._read_active_sheet_matrix_via_export(target_name)
        rows_count = len(matrix)
        self.log(
            f"Целевой лист «{target_name}»: прочитано строк/рядов: {rows_count:,}"
        )

        pairs = self._extract_pairs_from_matrix(matrix)
        self.log(
            f"Целевой лист «{target_name}»: найдено {len(pairs):,} уникальных пар «соцсеть + email»."
        )

        if not pairs:
            self.log(
                f"Целевой лист «{target_name}» не содержит ни одной пары «соцсеть + email»."
            )
        else:
            self.log(
                f"✅ Загружено в память: {len(pairs):,} уникальных комбинаций «соцсеть + email» "
                f"только из листа «{target_name}»."
            )

        self.log(
            "Другие листы Google Sheets НЕ читаются и НЕ участвуют в антидедупликации."
        )
        self.log("========================================")
        self.write_blocked = False
        self.verified_pairs = set(pairs)
        return pairs

    def load_all_tabs_profile_email_pairs(self) -> set[tuple[str, str]]:
        """Совместимый старый метод: теперь читает только целевой лист."""
        return self.load_target_sheet_profile_email_pairs()

    # ============================================================
    # WRITE ROWS
    # ============================================================

    def append_rows(self, rows: Iterable[list[str]]) -> list[list[str]]:
        """Read, deduplicate, paste once and verify the exact destination range."""
        self._require_connection()
        if self.write_blocked:
            raise SheetsSafetyError("Запись заблокирована. Обновите данные целевого листа.")
        rows = list(rows)
        self.last_verified_pairs.clear()
        if not rows:
            return []
        try:
            matrix = self._read_active_sheet_matrix_via_export(self.sheet_name)
            existing = extract_pairs(matrix) | self.verified_pairs
            clean_rows = filter_new_rows(rows, existing)
            if not clean_rows:
                self.log("Google Sheets: все строки уже существуют, вставка пропущена.")
                return []

            # Never overwrite old results when a new run starts from the same cell.
            col = self.current_column - 1
            last_occupied = max(
                (i + 1 for i, row in enumerate(matrix) if any(str(v).strip() for v in row[col:col + 3])),
                default=0,
            )
            target_row = max(self.current_row, last_occupied + 1)
            target_cell = self._cell_to_string(self.current_column, target_row)
            page = self.sheet_page
            page.bring_to_front()
            self._goto_cell(target_cell)
            # Keep untrusted text literal, and serialize TSV with real quoting.
            payload = io.StringIO()
            csv.writer(payload, delimiter="\t", lineterminator="\n").writerows(
                [("'" + value if value.startswith(("=", "+", "-", "@", "'")) else value) for value in row]
                for row in clean_rows
            )
            previous_clipboard = pyperclip.paste()
            tsv = payload.getvalue()
            try:
                pyperclip.copy(tsv)
                page.keyboard.press(f"{COPY_MOD}+V")
                # A successful keypress is not proof that Sheets saved anything.
                verified = False
                for delay in (1000, 2000, 4000):
                    page.wait_for_timeout(delay)
                    saved = self._read_active_sheet_matrix_via_export(self.sheet_name)
                    actual = [
                        (list(saved[i][col:col + 3]) + ["", "", ""])[:3]
                        if i < len(saved) else ["", "", ""]
                        for i in range(target_row - 1, target_row - 1 + len(clean_rows))
                    ]
                    if actual == clean_rows:
                        verified = True
                        break
                if not verified:
                    raise SheetsSafetyError(
                        f"Вставка в {target_cell} не подтверждена. Повторная вставка отключена; "
                        "проверьте таблицу и обновите данные листа."
                    )
            finally:
                # Do not clobber anything the user copied while the bot was working.
                if pyperclip.paste() == tsv:
                    pyperclip.copy(previous_clipboard)
            new_pairs = extract_pairs(clean_rows)
            self.verified_pairs.update(new_pairs)
            self.last_verified_pairs.update(new_pairs)
            self.current_row = target_row + len(clean_rows)
            self.log(f"Google Sheets: подтверждена запись {len(clean_rows)} строк с {target_cell}.")
            return clean_rows
        except Exception as exc:
            self.write_blocked = True
            if isinstance(exc, SheetsSafetyError):
                raise
            raise SheetsSafetyError("Ошибка проверки или записи Google Sheets. Парсер остановлен.") from exc

    # ============================================================
    # ДЕДУПЛИКАЦИЯ ПО ПАРЕ «СОЦСЕТЬ + EMAIL»
    # ============================================================

    normalize_social_url = staticmethod(normalize_social_url)
    normalize_email = staticmethod(normalize_email)

    @classmethod
    def make_profile_email_key(cls, social_url: str, email: str) -> tuple[str, str]:
        return (
            cls.normalize_social_url(social_url),
            cls.normalize_email(email),
        )

    def _read_whole_column(self, column: int) -> list[str]:
        """Надёжно читает одну колонку Google Sheets через UI и Clipboard."""
        self._require_connection()

        page = self.sheet_page
        if page is None:
            raise RuntimeError("Страница Google Sheets недоступна.")

        top_cell = self._cell_to_string(column, 1)
        self._goto_cell(top_cell)
        page.wait_for_timeout(400)

        # В Google Sheets выбор колонки — Control+Space даже на macOS.
        page.keyboard.press(f"{COLUMN_SELECT_MOD}+Space")
        page.wait_for_timeout(500)

        sentinel = "__ONSOCIAL_SHEETS_CLIPBOARD_SENTINEL__"
        try:
            pyperclip.copy(sentinel)
        except Exception as e:
            raise RuntimeError(f"Не удалось подготовить Clipboard: {e!r}")

        page.keyboard.press(f"{COPY_MOD}+C")

        raw = ""
        deadline = time.time() + 5.0
        while time.time() < deadline:
            page.wait_for_timeout(200)
            try:
                raw = pyperclip.paste() or ""
            except Exception:
                raw = ""
            if raw != sentinel:
                break

        if raw == sentinel or raw == "":
            raise RuntimeError(
                f"Google Sheets не вернул данные Clipboard для колонки "
                f"{self._column_to_letters(column)}. "
                "Проверка дублей остановлена, чтобы не допустить повторную запись."
            )

        values = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while values and not values[-1].strip():
            values.pop()

        self.log(
            f"Google Sheets: прочитана колонка {self._column_to_letters(column)} — "
            f"{len(values)} строк."
        )
        return values

    def read_existing_profile_email_pairs(self) -> set[tuple[str, str]]:
        """
        Читает из таблицы пары «ссылка на соцсеть + email».

        append_rows() пишет их в две соседние колонки:
            start_cell     -> имя
            start_cell + 1 -> social_url
            start_cell + 2 -> email

        Дедупликация теперь выполняется именно по этой паре,
        а не только по ссылке на соцсеть. Это позволяет отличить
        одного и того же инфлюенсера с новым email от полностью
        идентичной записи.
        """

        url_column = self.current_column + 1
        email_column = self.current_column + 2

        self.log(
            "Google Sheets: читаю существующие пары "
            "«соцсеть + email»..."
        )

        try:
            urls = self._read_whole_column(url_column)
            emails = self._read_whole_column(email_column)
        except Exception as e:
            self.log(
                "КРИТИЧЕСКАЯ ОШИБКА: не удалось прочитать существующие "
                f"пары из Google Sheets: {e!r}. Останавливаю обработку, "
                "чтобы не допустить записи дублей."
            )
            raise RuntimeError(
                "Не удалось безопасно прочитать Google Sheets для дедупликации. "
                "Запись остановлена, чтобы не создавать дубликаты."
            ) from e

        pairs: set[tuple[str, str]] = set()
        row_count = max(len(urls), len(emails))

        for index in range(row_count):
            social_url = urls[index].strip() if index < len(urls) else ""
            email = emails[index].strip() if index < len(emails) else ""

            # Строка без соцсети не может идентифицировать
            # профиль, поэтому её игнорируем.
            if not social_url:
                continue

            pairs.add(
                self.make_profile_email_key(
                    social_url,
                    email,
                )
            )

        self.log(
            "Google Sheets: найдено существующих пар "
            f"«соцсеть + email»: {len(pairs)}"
        )

        return pairs

    # ============================================================
    # ОБРАТНАЯ СОВМЕСТИМОСТЬ: ЧТЕНИЕ ТОЛЬКО ССЫЛОК
    # ============================================================

    def read_existing_social_urls(self) -> set[str]:
        """
        Читает колонку со ссылками на профиль (следующая колонка
        после стартовой, т.е. start_cell + 1 — там, куда
        append_rows всегда кладёт social_url) и возвращает
        множество уже существующих там значений.

        Нужно, чтобы не заносить одного и того же блогера
        в таблицу повторно — в том числе если данные туда
        попали в прошлом запуске приложения или руками.

        Работает БЕЗ Google Sheets API: переходит в верх нужной
        колонки, выделяет её целиком (Ctrl+Space), копирует
        (Ctrl+C) и читает системный буфер обмена.
        """

        self._require_connection()

        page = self.sheet_page

        url_column = self.current_column + 1

        top_cell = self._cell_to_string(
            url_column,
            1,
        )

        self.log(
            f"Google Sheets: читаю существующие ссылки "
            f"из колонки {top_cell[:-1] or top_cell}..."
        )

        try:

            self._goto_cell(top_cell)

            page.keyboard.press(f"{MOD}+Space")

            page.wait_for_timeout(300)

            page.keyboard.press(f"{MOD}+C")

            page.wait_for_timeout(300)

            raw = pyperclip.paste()

        except Exception as e:

            self.log(
                "Не удалось прочитать существующие ссылки "
                f"из таблицы: {e!r}. Дедупликация по таблице "
                "в этом запуске работать не будет."
            )

            return set()

        existing = {
            line.strip()
            for line in raw.splitlines()
            if line.strip()
        }

        self.log(
            f"Google Sheets: найдено уже существующих ссылок: "
            f"{len(existing)}"
        )

        return existing

    # ============================================================
    # GOTO CELL
    # ============================================================

    def _goto_cell(self, cell: str):
        """
        Переходит в конкретную ячейку Google Sheets.

        Основной способ:
            Ctrl+J -> Name box -> A2 -> Enter

        Это штатный способ Google Sheets.

        Дополнительно есть DOM-варианты как запасной механизм.
        """

        page = self.sheet_page

        if page is None:
            raise RuntimeError(
                "Google Sheets не открыта."
            )

        cell = cell.strip().upper()

        # --------------------------------------------------------
        # Сначала пробуем DOM Name Box.
        # --------------------------------------------------------

        selectors = [
            'input[aria-label="Name box"]',
            'input[aria-label="Name Box"]',
            'input.waffle-name-box',
            '[aria-label="Name box"]',
            '[aria-label="Name Box"]',
        ]

        for selector in selectors:
            try:
                locator = page.locator(selector)

                count = locator.count()

                for i in range(count):
                    item = locator.nth(i)

                    if not item.is_visible():
                        continue

                    try:
                        item.click()

                        page.keyboard.press(
                            f"{COPY_MOD}+A"
                        )

                        page.keyboard.type(
                            cell
                        )

                        page.keyboard.press(
                            "Enter"
                        )

                        page.wait_for_timeout(300)

                        self.log(
                            f"Google Sheets: перешёл в {cell}"
                        )

                        return

                    except Exception:
                        continue

            except Exception:
                continue

        # --------------------------------------------------------
        # Основной универсальный вариант Google Sheets:
        #
        # Ctrl+J -> Name Box
        # --------------------------------------------------------

        try:
            page.keyboard.press(
                f"{COLUMN_SELECT_MOD}+J"
            )

            page.wait_for_timeout(300)

            page.keyboard.press(
                f"{COPY_MOD}+A"
            )

            page.keyboard.type(
                cell
            )

            page.keyboard.press(
                "Enter"
            )

            page.wait_for_timeout(500)

            self.log(
                f"Google Sheets: перешёл в {cell}"
            )

            return

        except Exception as e:
            raise RuntimeError(
                f"Не удалось перейти в ячейку {cell}: {e}"
            )

    # ============================================================
    # PARSE A1
    # ============================================================

    @staticmethod
    def _parse_cell(cell: str) -> tuple[int, int]:
        """
        A1 -> (1, 1)
        B5 -> (2, 5)
        D10 -> (4, 10)
        AA25 -> (27, 25)
        """

        cell = (cell or "").strip().upper()

        match = re.fullmatch(
            r"([A-Z]{1,4})([1-9][0-9]*)",
            cell,
        )

        if not match:
            raise ValueError(
                f"Неверная ячейка: {cell}. "
                f"Пример правильного значения: A2."
            )

        letters = match.group(1)
        row = int(match.group(2))

        column = 0

        for char in letters:
            column = (
                column * 26
                + (ord(char) - ord("A") + 1)
            )

        return column, row

    # ============================================================
    # COLUMN NUMBER -> LETTERS
    # ============================================================

    @staticmethod
    def _column_to_letters(column: int) -> str:
        result = ""

        while column > 0:
            column, remainder = divmod(
                column - 1,
                26,
            )

            result = (
                chr(
                    ord("A") + remainder
                )
                + result
            )

        return result

    # ============================================================
    # CELL STRING
    # ============================================================

    @classmethod
    def _cell_to_string(
        cls,
        column: int,
        row: int,
    ) -> str:

        return (
            f"{cls._column_to_letters(column)}"
            f"{row}"
        )

    # ============================================================
    # REQUIRE CONNECTION
    # ============================================================

    def _require_connection(self):
        if (
            not self.connected
            or self.sheet_page is None
        ):
            raise RuntimeError(
                "Google Sheets не подключён. "
                "Сначала нажми «Подключить Google Sheets»."
            )
