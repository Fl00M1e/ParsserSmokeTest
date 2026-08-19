from __future__ import annotations

import re
import sys
import time
from typing import Iterable, Optional

import pyperclip
from playwright.sync_api import BrowserContext, Page


# ============================================================
# КРОСС-ПЛАТФОРМЕННЫЕ ХОТКЕИ
# ============================================================
#
# На Windows/Linux системная клавиша-модификатор для
# копирования/вставки/выделения — Control.
# На macOS браузер (в т.ч. управляемый Playwright) ожидает
# Cmd (в терминах Playwright это "Meta"), Control там ничего
# не делает.
#
# MOD задаётся один раз при импорте модуля и используется
# везде вместо жёстко зашитого "Control".

MOD = "Meta" if sys.platform == "darwin" else "Control"


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

        page.keyboard.press(f"{MOD}+V")

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
    # ЧТЕНИЕ УЖЕ СУЩЕСТВУЮЩИХ ССЫЛОК (ДЛЯ ДЕДУПЛИКАЦИИ)
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
                            f"{MOD}+A"
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
                f"{MOD}+J"
            )

            page.wait_for_timeout(300)

            page.keyboard.press(
                f"{MOD}+A"
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