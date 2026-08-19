from __future__ import annotations

import re
import sys
import time
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

    def _read_active_sheet_matrix(self) -> list[list[str]]:
        """
        Copies the complete contiguous table/data region of the active tab.
        Google Sheets handles the selection; the data is read only from the
        system Clipboard and immediately parsed into normalized pairs.
        """
        self._require_connection()
        page = self.sheet_page
        if page is None:
            raise RuntimeError("Страница Google Sheets недоступна.")

        self._goto_cell("A1")
        page.wait_for_timeout(250)

        sentinel = "__ONSOCIAL_FULL_SHEET_SENTINEL__"
        pyperclip.copy(sentinel)

        # Google Sheets: first Ctrl+A selects the current data region; a second
        # Ctrl+A expands the selection to the whole used sheet/document region.
        page.keyboard.press(f"{COPY_MOD}+A")
        page.wait_for_timeout(180)
        page.keyboard.press(f"{COPY_MOD}+A")
        page.wait_for_timeout(220)
        page.keyboard.press(f"{COPY_MOD}+C")

        raw = ""
        deadline = time.time() + 8.0
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
                "Google Sheets не вернул содержимое активной вкладки в Clipboard."
            )

        lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while lines and not lines[-1].strip():
            lines.pop()

        matrix = [line.split("\t") for line in lines]
        return matrix

    @staticmethod
    def _extract_pairs_from_matrix(matrix: list[list[str]]) -> set[tuple[str, str]]:
        """
        Extract real social+email pairs row-by-row.

        A pair is created from values that occur on the SAME spreadsheet row.
        This prevents a URL from one influencer row from being combined with an
        email belonging to another row. When a row contains one URL and several
        emails, all emails are paired with that URL. When it contains several
        URLs and one email, that email is paired with all URLs. When there are
        multiple URLs and multiple emails, nearest-column matching is used and
        only genuinely row-local pairs are kept.
        """
        email_re = re.compile(
            r"(?i)(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
            r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w.-])"
        )
        social_hosts = {
            "instagram.com", "tiktok.com", "youtube.com", "youtu.be",
            "facebook.com", "fb.com", "x.com", "twitter.com",
            "linkedin.com", "pinterest.com", "twitch.tv", "threads.net",
            "snapchat.com", "vk.com", "vimeo.com", "telegram.me",
            "t.me",
        }
        from urllib.parse import urlsplit
        pairs: set[tuple[str, str]] = set()

        def normalize_url(value: str) -> str:
            value = str(value or "").strip()
            if not value:
                return ""
            try:
                if not re.match(r"^https?://", value, re.I):
                    value = "https://" + value
                parts = urlsplit(value)
                host = (parts.hostname or "").lower().strip()
                if host.startswith("www."):
                    host = host[4:]
                if not host:
                    return ""
                port = parts.port
                netloc = host if not port or port in (80, 443) else f"{host}:{port}"
                path = parts.path.rstrip("/") or ""
                return f"https://{netloc}{path}"
            except Exception:
                return ""

        def looks_social(value: str) -> bool:
            try:
                candidate = value.strip()
                if not re.match(r"^https?://", candidate, re.I):
                    candidate = "https://" + candidate
                host = (urlsplit(candidate).hostname or "").lower()
                if host.startswith("www."):
                    host = host[4:]
                return host in social_hosts or any(host.endswith("." + h) for h in social_hosts)
            except Exception:
                return False

        for row in matrix:
            row_cells = [str(c or "").strip() for c in row]
            url_cells: list[tuple[int, str]] = []
            email_cells: list[tuple[int, str]] = []

            for col_idx, cell in enumerate(row_cells):
                for email in email_re.findall(cell):
                    email_cells.append((col_idx, email.lower()))
                if looks_social(cell):
                    u = normalize_url(cell)
                    if u:
                        url_cells.append((col_idx, u))
                # Support a cell containing both URL and email or a label around a URL.
                if not looks_social(cell):
                    for match in re.findall(r"https?://[^\s<>]+", cell, re.I):
                        candidate = match.rstrip(",.;)\"'")
                        if looks_social(candidate):
                            u = normalize_url(candidate)
                            if u:
                                url_cells.append((col_idx, u))

            # De-duplicate values while keeping their column positions.
            seen_urls = set()
            urls = [(c, u) for c, u in url_cells if not (u in seen_urls or seen_urls.add(u))]
            seen_emails = set()
            emails = [(c, e) for c, e in email_cells if not (e in seen_emails or seen_emails.add(e))]
            if not urls or not emails:
                continue

            if len(urls) == 1:
                social = urls[0][1]
                for _, email in emails:
                    pairs.add((social, email))
            elif len(emails) == 1:
                email = emails[0][1]
                for _, social in urls:
                    pairs.add((social, email))
            else:
                # Multiple URLs/emails in one row: pair nearest columns. This avoids
                # producing every possible cross-product and therefore avoids false
                # duplicate matches across several influencers represented on one row.
                remaining = list(emails)
                for url_col, social in urls:
                    nearest = min(remaining, key=lambda item: abs(item[0] - url_col))
                    pairs.add((social, nearest[1]))
                    remaining.remove(nearest)

        return pairs

    def load_all_tabs_profile_email_pairs(self) -> set[tuple[str, str]]:
        """
        Loads social+email combinations from every sheet tab before parsing.
        No data is written to the spreadsheet. The returned set is intended to
        live only for the current parser run.
        """
        self._require_connection()
        original_name = self.sheet_name
        self.log("========================================")
        self.log("ЗАГРУЗКА ДАННЫХ ВСЕЙ ТАБЛИЦЫ")
        self.log("Читаю все вкладки Google Sheets в память...")

        tabs = self.list_sheet_tabs()
        self.log(f"Найдено вкладок: {len(tabs)}")

        all_pairs: set[tuple[str, str]] = set()
        successful = 0
        total_pairs_found = 0
        total_rows_read = 0
        try:
            for index, tab_name in enumerate(tabs, start=1):
                self.log(f"[{index}/{len(tabs)}] НАЧАЛО ЧТЕНИЯ ВКЛАДКИ: «{tab_name}»")
                self._activate_sheet_tab_by_name(tab_name)
                matrix = self._read_active_sheet_matrix()
                rows_count = len(matrix)
                total_rows_read += rows_count
                self.log(
                    f"[{index}/{len(tabs)}] Вкладка «{tab_name}»: прочитано строк/рядов: {rows_count:,}"
                )
                pairs = self._extract_pairs_from_matrix(matrix)
                found_count = len(pairs)
                total_pairs_found += found_count
                before = len(all_pairs)
                all_pairs.update(pairs)
                added_unique = len(all_pairs) - before
                successful += 1
                self.log(
                    f"[{index}/{len(tabs)}] Вкладка «{tab_name}»: найдено {found_count:,} пар «соцсеть + email»; "
                    f"новых уникальных после объединения: {added_unique:,}; всего в памяти: {len(all_pairs):,}"
                )

            if successful != len(tabs):
                raise RuntimeError(
                    f"Прочитано только {successful} из {len(tabs)} вкладок."
                )

            duplicates_inside_tabs = max(0, total_pairs_found - len(all_pairs))
            self.log("----------------------------------------")
            self.log("✅ ВСЕ ДАННЫЕ ИЗ ТАБЛИЦЫ СОБРАНЫ")
            self.log(f"Вкладок обработано: {successful}/{len(tabs)}")
            self.log(f"Всего строк/рядов прочитано: {total_rows_read:,}")
            self.log(f"Всего найдено пар «ссылка + email» до объединения: {total_pairs_found:,}")
            self.log(f"Уникальных комбинаций загружено в память: {len(all_pairs):,}")
            self.log(f"Повторяющихся комбинаций отброшено при объединении: {duplicates_inside_tabs:,}")
            self.log("Парсер можно запускать — сравнение будет выполняться только с этой памятью.")
            self.log("----------------------------------------")
            return all_pairs
        finally:
            # Always restore the working sheet; if restoring fails, fail closed.
            try:
                self._activate_sheet_tab_by_name(original_name)
            except Exception as restore_error:
                self.log(f"Не удалось вернуть рабочую вкладку «{original_name}»: {restore_error!r}")
                raise

    # ============================================================
    # WRITE ROWS
    # ============================================================

    def append_rows(self, rows: Iterable[list[str]]):
        """
        Записывает строки начиная с текущей ячейки.

        Например, если start_cell = A2:

            rows[0] -> A2:C2
            rows[1] -> A3:C3
            rows[2] -> A4:C4

        После записи текущая строка автоматически увеличивается.

        Если start_cell = D10:

            rows[0] -> D10:F10
            rows[1] -> D11:F11
            rows[2] -> D12:F12
        """

        self._require_connection()

        rows = list(rows)

        if not rows:
            self.log(
                "Google Sheets: нечего записывать."
            )
            return

        # --------------------------------------------------------
        # Нормализуем строки.
        # Всегда 3 колонки:
        #
        # Имя | Ссылка | Контакт
        # --------------------------------------------------------

        clean_rows = []

        for row in rows:
            row = list(row)

            if len(row) < 3:
                row += [""] * (3 - len(row))

            clean_rows.append(
                [
                    str(row[0] or ""),
                    str(row[1] or ""),
                    str(row[2] or ""),
                ]
            )

        # --------------------------------------------------------
        # Формируем TSV.
        #
        # Google Sheets при Ctrl+V сам раскладывает:
        #
        # \t -> колонки
        # \n -> строки
        # --------------------------------------------------------

        tsv = "\n".join(
            "\t".join(row)
            for row in clean_rows
        )

        # --------------------------------------------------------
        # Текущий адрес.
        # --------------------------------------------------------

        target_cell = self._cell_to_string(
            self.current_column,
            self.current_row,
        )

        self.log(
            f"Google Sheets: записываю {len(clean_rows)} строк "
            f"начиная с {target_cell}"
        )

        # --------------------------------------------------------
        # Переходим ИМЕННО в target_cell.
        #
        # Никакого Ctrl+Home.
        # Никакого Ctrl+Down.
        # Никакого поиска последней строки.
        # --------------------------------------------------------

        self._goto_cell(target_cell)

        page = self.sheet_page

        if page is None:
            raise RuntimeError(
                "Страница Google Sheets недоступна."
            )

        # --------------------------------------------------------
        # Кладём данные в системный Clipboard.
        # --------------------------------------------------------

        pyperclip.copy(tsv)

        page.wait_for_timeout(200)

        # --------------------------------------------------------
        # Вставляем.
        # --------------------------------------------------------

        page.keyboard.press(f"{COPY_MOD}+V")

        # --------------------------------------------------------
        # Даём Sheets обработать вставку.
        # --------------------------------------------------------

        wait_ms = max(
            1000,
            min(
                6000,
                len(clean_rows) * 150,
            ),
        )

        page.wait_for_timeout(wait_ms)

        # --------------------------------------------------------
        # ВАЖНО:
        #
        # Увеличиваем строку ТОЛЬКО после успешного Ctrl+V.
        # --------------------------------------------------------

        self.current_row += len(clean_rows)

        next_cell = self._cell_to_string(
            self.current_column,
            self.current_row,
        )

        self.log(
            f"Google Sheets: записано строк: {len(clean_rows)}"
        )

        self.log(
            f"Google Sheets: следующая запись начнётся с {next_cell}"
        )

    # ============================================================
    # ДЕДУПЛИКАЦИЯ ПО ПАРЕ «СОЦСЕТЬ + EMAIL»
    # ============================================================

    @staticmethod
    def normalize_social_url(value: str) -> str:
        """Нормализует URL соцсети для надёжного сравнения."""
        from urllib.parse import urlsplit, urlunsplit

        value = (value or "").strip()
        if not value:
            return ""

        try:
            parts = urlsplit(value)
            if not parts.netloc:
                return value.rstrip("/").lower()

            hostname = (parts.hostname or "").lower().strip()
            if hostname.startswith("www."):
                hostname = hostname[4:]

            port = parts.port
            netloc = hostname
            if port and port not in (80, 443):
                netloc = f"{hostname}:{port}"

            path = parts.path.rstrip("/") or ""

            # Схему приводим к https, query и fragment игнорируем.
            return urlunsplit(("https", netloc, path, "", ""))
        except Exception:
            return value.rstrip("/").lower()

    @staticmethod
    def normalize_email(value: str) -> str:
        return re.sub(r"\s+", "", (value or "").strip()).lower()

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
