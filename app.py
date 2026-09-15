from __future__ import annotations

import queue
import re
import shutil
import sys
import threading
import traceback
import tkinter as tk
from tkinter import ttk, messagebox

from playwright.sync_api import sync_playwright

from config import (
    BROWSER_PROFILE,
    HEADLESS,
    ONSOCIAL_START_URL,
    DEFAULT_MAX_INFLUENCERS,
    DEFAULT_MAX_UNLOCKS,
    DEFAULT_TIMEOUT_MS,
)

from parser import OnSocialParser, StopRequested
from sheets import GoogleSheetsWriter, SheetsSafetyError


# ============================================================
# ПРОВЕРКА GOOGLE CHROME
# ============================================================
#
# Playwright запускает браузер через channel="chrome", то есть
# использует уже установленный в системе Google Chrome, а не
# свой отдельный движок. Если Chrome не установлен, Playwright
# упадёт с малопонятной технической ошибкой. Проверяем заранее
# и показываем понятное сообщение.

def _system_chrome_found() -> bool:
    """
    Быстрая эвристическая проверка наличия Google Chrome.

    Не гарантирует 100% точность (например, нестандартное
    место установки), но покрывает подавляющее большинство
    случаев на macOS, Windows и Linux.
    """

    if sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app",
            str(
                __import__("pathlib").Path.home()
                / "Applications"
                / "Google Chrome.app"
            ),
        ]

    elif sys.platform.startswith("win"):
        import os

        candidates = [
            os.path.expandvars(
                r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"
            ),
            os.path.expandvars(
                r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
            ),
            os.path.expandvars(
                r"%LocalAppData%\Google\Chrome\Application\chrome.exe"
            ),
        ]

    else:
        # Linux и всё остальное.
        for name in ("google-chrome", "google-chrome-stable"):
            if shutil.which(name):
                return True

        return False

    import os

    return any(os.path.exists(path) for path in candidates)


class App:
    """
    ON Social Local Parser.

    Google Sheets:
        БЕЗ Google Sheets API.

    Работа с таблицей:
        обычный пользовательский браузер +
        Playwright +
        системный Clipboard +
        Ctrl+V.

    Playwright sync API используется только внутри
    отдельного worker-потока.
    """

    def __init__(self, root):
        self.root = root

        self.root.title("ON Social Local Parser")
        self.root.geometry("820x700")
        self.root.minsize(760, 620)

        # ========================================================
        # СОСТОЯНИЕ
        # ========================================================

        self.stop_event = threading.Event()

        self.browser_ready = False
        self.sheets_ready = False
        self.worker_busy = False

        # Постоянный в рамках жизненного цикла приложения кэш дублей.
        # Загружается автоматически после подключения Google Sheets,
        # пополняется новыми записанными комбинациями и полностью
        # очищается только при закрытии приложения.
        self.table_cache_loaded = False
        self.table_cache_pairs: set[tuple[str, str]] = set()

        # Эти объекты принадлежат Playwright worker thread.
        self.pw = None
        self.context = None
        self.list_page = None
        self.sheets = None

        # ========================================================
        # ОЧЕРЕДЬ PLAYWRIGHT
        # ========================================================

        self.command_queue = queue.Queue()

        self.worker_shutdown = threading.Event()

        self.worker_thread = threading.Thread(
            target=self._playwright_worker,
            daemon=True,
            name="PlaywrightWorker",
        )

        self.worker_thread.start()

        # ========================================================
        # UI
        # ========================================================

        self._build_ui()

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self._on_close,
        )

    # ============================================================
    # UI
    # ============================================================

    def _build_ui(self):
        frm = ttk.Frame(
            self.root,
            padding=14,
        )

        frm.pack(
            fill="both",
            expand=True,
        )

        # ========================================================
        # HEADER
        # ========================================================

        ttk.Label(
            frm,
            text="ON Social Local Parser",
            font=("TkDefaultFont", 18, "bold"),
        ).pack(
            anchor="w",
            pady=(0, 12),
        )

        ttk.Label(
            frm,
            text="ON Social → Google Sheets | без API",
        ).pack(
            anchor="w",
            pady=(0, 8),
        )

        # ========================================================
        # GOOGLE SHEETS
        # ========================================================

        box = ttk.LabelFrame(
            frm,
            text="Google Sheets — локальная работа без API",
            padding=10,
        )

        box.pack(
            fill="x",
            pady=6,
        )

        # --------------------------------------------------------
        # URL
        # --------------------------------------------------------

        ttk.Label(
            box,
            text="URL Google Sheets:",
        ).grid(
            row=0,
            column=0,
            sticky="w",
        )

        self.sheet_var = tk.StringVar()

        self.sheet_entry = ttk.Entry(
            box,
            textvariable=self.sheet_var,
            width=75,
        )

        self.sheet_entry.grid(
            row=1,
            column=0,
            sticky="ew",
            pady=4,
        )

        # --------------------------------------------------------
        # Кнопки URL
        # --------------------------------------------------------

        ttk.Button(
            box,
            text="Вставить из буфера",
            command=self._paste_sheet_url,
        ).grid(
            row=1,
            column=1,
            padx=(8, 0),
            pady=4,
        )

        ttk.Button(
            box,
            text="Очистить",
            command=self._clear_sheet_url,
        ).grid(
            row=1,
            column=2,
            padx=(8, 0),
            pady=4,
        )

        # --------------------------------------------------------
        # Контекстное меню
        # --------------------------------------------------------

        self.sheet_menu = tk.Menu(
            self.root,
            tearoff=False,
        )

        self.sheet_menu.add_command(
            label="Вставить",
            command=self._paste_sheet_url,
        )

        self.sheet_menu.add_command(
            label="Копировать",
            command=self._copy_sheet_url,
        )

        self.sheet_menu.add_command(
            label="Вырезать",
            command=self._cut_sheet_url,
        )

        self.sheet_menu.add_separator()

        self.sheet_menu.add_command(
            label="Выделить всё",
            command=self._select_all_sheet_url,
        )

        self.sheet_entry.bind(
            "<Button-3>",
            self._show_sheet_context_menu,
        )

        self.sheet_entry.bind(
            "<Control-v>",
            self._paste_sheet_url,
        )

        self.sheet_entry.bind(
            "<Control-V>",
            self._paste_sheet_url,
        )

        self.sheet_entry.bind(
            "<Control-a>",
            self._select_all_sheet_url,
        )

        self.sheet_entry.bind(
            "<Control-A>",
            self._select_all_sheet_url,
        )

        self.sheet_entry.bind(
            "<Control-c>",
            self._copy_sheet_url,
        )

        self.sheet_entry.bind(
            "<Control-C>",
            self._copy_sheet_url,
        )

        self.sheet_entry.bind(
            "<Control-x>",
            self._cut_sheet_url,
        )

        self.sheet_entry.bind(
            "<Control-X>",
            self._cut_sheet_url,
        )

        self.sheet_entry.bind(
            "<Shift-Insert>",
            self._paste_sheet_url,
        )

        # --------------------------------------------------------
        # ИМЯ ЛИСТА
        # --------------------------------------------------------

        ttk.Label(
            box,
            text="Имя листа:",
        ).grid(
            row=2,
            column=0,
            sticky="w",
            pady=(6, 0),
        )

        self.tab_var = tk.StringVar(
            value="Sheet1",
        )

        ttk.Entry(
            box,
            textvariable=self.tab_var,
            width=30,
        ).grid(
            row=3,
            column=0,
            sticky="w",
        )

        # --------------------------------------------------------
        # НАЧАЛЬНАЯ ЯЧЕЙКА
        # --------------------------------------------------------

        ttk.Label(
            box,
            text="Начинать запись с ячейки:",
        ).grid(
            row=4,
            column=0,
            sticky="w",
            pady=(8, 0),
        )

        self.start_cell_var = tk.StringVar(
            value="A2",
        )

        self.start_cell_entry = ttk.Entry(
            box,
            textvariable=self.start_cell_var,
            width=15,
        )

        self.start_cell_entry.grid(
            row=5,
            column=0,
            sticky="w",
            pady=4,
        )

        ttk.Label(
            box,
            text="Например: A2, B5, D10, AA20",
        ).grid(
            row=5,
            column=1,
            columnspan=2,
            sticky="w",
            padx=(8, 0),
        )

        # --------------------------------------------------------
        # ПОДСКАЗКА
        # --------------------------------------------------------

        ttk.Label(
            box,
            text=(
                "Запись начнётся не выше этой ячейки и ниже занятых строк. "
                "Следующие строки — ниже."
            ),
        ).grid(
            row=6,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(2, 4),
        )

        # --------------------------------------------------------
        # CONNECT
        # --------------------------------------------------------

        self.connect_button = ttk.Button(
            box,
            text="Подключить Google Sheets",
            command=self.connect_sheets,
        )

        self.connect_button.grid(
            row=7,
            column=0,
            sticky="w",
            pady=8,
        )

        self.load_table_button = ttk.Button(
            box,
            text="Обновить данные целевого листа",
            command=self.load_table_cache,
        )

        self.load_table_button.grid(
            row=7,
            column=1,
            sticky="w",
            padx=(8, 0),
            pady=8,
        )

        self.table_cache_status_var = tk.StringVar(
            value="После подключения Google Sheets приложение автоматически загрузит данные только целевого листа и комбинации в память."
        )

        ttk.Label(
            box,
            textvariable=self.table_cache_status_var,
            wraplength=700,
        ).grid(
            row=8,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(2, 2),
        )

        box.columnconfigure(
            0,
            weight=1,
        )

        # ========================================================
        # ПАРАМЕТРЫ
        # ========================================================

        box2 = ttk.LabelFrame(
            frm,
            text="Параметры запуска",
            padding=10,
        )

        box2.pack(
            fill="x",
            pady=6,
        )

        ttk.Label(
            box2,
            text="Максимум блогеров:",
        ).grid(
            row=0,
            column=0,
            sticky="w",
        )

        self.max_inf_var = tk.IntVar(
            value=DEFAULT_MAX_INFLUENCERS,
        )

        ttk.Spinbox(
            box2,
            from_=1,
            to=10000,
            textvariable=self.max_inf_var,
            width=10,
        ).grid(
            row=0,
            column=1,
            sticky="w",
            padx=8,
        )

        ttk.Label(
            box2,
            text="Максимум Unlock next:",
        ).grid(
            row=1,
            column=0,
            sticky="w",
        )

        self.max_unlock_var = tk.IntVar(
            value=DEFAULT_MAX_UNLOCKS,
        )

        ttk.Spinbox(
            box2,
            from_=0,
            to=1000,
            textvariable=self.max_unlock_var,
            width=10,
        ).grid(
            row=1,
            column=1,
            sticky="w",
            padx=8,
        )

        # ========================================================
        # КНОПКИ
        # ========================================================

        controls = ttk.Frame(frm)

        controls.pack(
            fill="x",
            pady=10,
        )

        self.open_button = ttk.Button(
            controls,
            text="1. Открыть ON Social",
            command=self.open_browser,
        )

        self.open_button.pack(
            side="left",
            padx=(0, 6),
        )

        self.guide_button = ttk.Button(
            controls,
            text="2. Как пользоваться",
            command=self.show_guide,
        )

        self.guide_button.pack(
            side="left",
            padx=6,
        )

        self.start_button = ttk.Button(
            controls,
            text="▶ НАЧАТЬ",
            command=self.start,
        )

        self.start_button.pack(
            side="left",
            padx=6,
        )

        self.stop_button = ttk.Button(
            controls,
            text="■ STOP",
            command=self.stop,
        )

        self.stop_button.pack(
            side="left",
            padx=6,
        )

        # ========================================================
        # СТАТУС
        # ========================================================

        status = ttk.LabelFrame(
            frm,
            text="Журнал работы",
            padding=10,
        )

        status.pack(
            fill="both",
            expand=True,
            pady=6,
        )

        self.log_box = tk.Text(
            status,
            height=20,
            wrap="word",
            font=("Consolas", 9),
        )

        self.log_box.pack(
            fill="both",
            expand=True,
        )

        self.log("Готово.")
        self.log("1. Открой ON Social и войди в аккаунт вручную.")
        self.log("2. Открой Influencer Identification.")
        self.log("3. Подключи Google Sheets и укажи лист, в который бот будет записывать блогеров.")
        self.log("4. Бот сам загрузит данные целевого листа в память дедупликации.")
        self.log("5. Укажи начальную ячейку, например A2.")
        self.log("6. Нажми «НАЧАТЬ».")
        self.log("Во время работы бот ищет Analyze по мере прокрутки списка; заранее прокручивать весь список вручную не нужно.")
        self.log("Кэш дедупликации живёт до закрытия приложения и пополняется после успешной записи новых комбинаций.")

    # ============================================================
    # LOG
    # ============================================================

    def log(self, text):
        def _write():
            try:
                self.log_box.insert(
                    "end",
                    str(text) + "\n",
                )

                self.log_box.see(
                    "end",
                )

            except Exception:
                pass

        try:
            self.root.after(
                0,
                _write,
            )
        except Exception:
            pass

    # ============================================================
    # GOOGLE SHEETS FIELD
    # ============================================================

    def _paste_sheet_url(self, event=None):
        try:
            clipboard = self.root.clipboard_get()

            if clipboard is None:
                return "break"

            clipboard = str(clipboard).strip()

            if not clipboard:
                return "break"

            self.sheet_entry.focus_set()

            self.sheet_entry.delete(
                0,
                tk.END,
            )

            self.sheet_entry.insert(
                0,
                clipboard,
            )

            self.log(
                "URL Google Sheets вставлен из буфера."
            )

        except tk.TclError:
            messagebox.showwarning(
                "Буфер обмена",
                "Буфер обмена пуст или его содержимое нельзя прочитать.",
            )

        return "break"

    def _copy_sheet_url(self, event=None):
        try:
            self.sheet_entry.event_generate(
                "<<Copy>>",
            )
        except Exception:
            pass

        return "break"

    def _cut_sheet_url(self, event=None):
        try:
            self.sheet_entry.event_generate(
                "<<Cut>>",
            )
        except Exception:
            pass

        return "break"

    def _select_all_sheet_url(self, event=None):
        self.sheet_entry.focus_set()

        self.sheet_entry.select_range(
            0,
            tk.END,
        )

        self.sheet_entry.icursor(
            tk.END,
        )

        return "break"

    def _clear_sheet_url(self):
        self.sheet_var.set("")

        self.sheet_entry.focus_set()

        self.log(
            "Поле URL Google Sheets очищено."
        )

    def _show_sheet_context_menu(self, event):
        try:
            self.sheet_entry.focus_set()

            self.sheet_menu.tk_popup(
                event.x_root,
                event.y_root,
            )

        finally:
            self.sheet_menu.grab_release()

    # ============================================================
    # PLAYWRIGHT WORKER
    # ============================================================

    def _playwright_worker(self):
        """
        Единственное место, где создаётся sync_playwright.

        Все операции Playwright выполняются здесь.
        """

        try:
            self.log(
                "Playwright worker запущен."
            )

            while not self.worker_shutdown.is_set():

                try:
                    command, args = self.command_queue.get(
                        timeout=0.2,
                    )

                except queue.Empty:
                    continue

                if command == "__shutdown__":
                    break

                try:

                    if command == "open_browser":
                        self._worker_open_browser()

                    elif command == "check_page":
                        self._worker_check_page()

                    elif command == "connect_sheets":
                        self._worker_connect_sheets(
                            args,
                        )

                    elif command == "load_table_cache":
                        self._worker_load_table_cache()

                    elif command == "start_parser":
                        self._worker_start_parser(
                            args,
                        )

                    else:
                        self.log(
                            f"Неизвестная команда worker: {command}"
                        )

                except Exception as e:

                    self.log(
                        f"Ошибка worker-команды {command}: {e!r}"
                    )

                    self.log(
                        traceback.format_exc()
                    )

                finally:
                    self.command_queue.task_done()

        except Exception as e:

            self.log(
                "КРИТИЧЕСКАЯ ОШИБКА Playwright worker: "
                + repr(e)
            )

            self.log(
                traceback.format_exc()
            )

        finally:

            self._worker_close_browser()

            self.log(
                "Playwright worker завершён."
            )

    # ============================================================
    # OPEN BROWSER — WORKER
    # ============================================================

    def _worker_open_browser(self):

        if self.context:

            self.log(
                "Браузер уже открыт."
            )

            return

        if not _system_chrome_found():

            self.log(
                "Не найден установленный Google Chrome."
            )

            self.log(
                "Скачай и установи Chrome: "
                "https://www.google.com/chrome/"
            )

            self._set_worker_busy(False)

            return

        self.log(
            "Запускаю Chrome..."
        )

        BROWSER_PROFILE.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.pw = sync_playwright().start()

        self.context = (
            self.pw.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_PROFILE),
                headless=HEADLESS,
                channel="chrome",
                viewport={
                    "width": 1440,
                    "height": 1000,
                },
                # --------------------------------------------------
                # Google блокирует вход в аккаунт со словами
                # "небезопасный браузер", если видит признаки
                # автоматизации. Playwright по умолчанию сам
                # добавляет "--enable-automation" (показывает
                # плашку "Chrome управляется автоматическим ПО")
                # и выставляет navigator.webdriver = true — это
                # и палит Google. Убираем оба признака.
                # --------------------------------------------------
                args=[
                    "--disable-blink-features=AutomationControlled",
                ],
                ignore_default_args=[
                    "--enable-automation",
                ],
            )
        )

        if self.context.pages:

            self.list_page = self.context.pages[0]

        else:

            self.list_page = self.context.new_page()

        self.list_page.goto(
            ONSOCIAL_START_URL,
            wait_until="domcontentloaded",
            timeout=DEFAULT_TIMEOUT_MS,
        )

        self.browser_ready = True

        self.log(
            "Chrome открыт."
        )

        self.log(
            "Войди в ON Social вручную."
        )

        self.log(
            "После входа открой Influencer Identification."
        )

        self._set_worker_busy(False)

    def show_guide(self):

        message = (
            "ПОШАГОВЫЙ ГАЙД\n\n"
            "1. Нажми «Открыть ON Social».\n"
            "2. Войди в свой аккаунт ON Social и открой Influencer Identification.\n"
            "3. Вставь ссылку на Google Sheets.\n"
            "4. Выбери лист, в который бот должен записывать новых блогеров.\n"
            "5. Подключи таблицу и дождись сообщения, что данные целевого листа загружены в память.\n"
            "6. Укажи начальную ячейку для записи (например, A2).\n"
            "7. Нажми «НАЧАТЬ».\n\n"
            "Что делает бот автоматически:\n"
            "• открывает зелёные и серые Analyze в текущем списке;\n"
            "• если Analyze закончились, аккуратно подгружает следующую часть списка;\n"
            "• не прокручивает весь список заранее;\n"
            "• перед записью заново читает целевой лист и проверяет дубли;\n"
            "• дубликаты не записывает;\n"
            "• подтверждает запись чтением таблицы и сохраняет ключи в памяти;\n"
            "• после Analyze обновляет страницу и снова подгружает список по мере необходимости.\n\n"
            "Важно: предварительно вручную прокручивать список до конца не нужно.\n"
            "Кэш дедупликации очищается только при полном закрытии приложения."
        )

        messagebox.showinfo(
            "Как пользоваться ParserOnSocial",
            message,
        )

    # ============================================================
    # CHECK PAGE — WORKER
    # ============================================================

    def _worker_check_page(self):

        if not self.context or not self.list_page:

            self.log(
                "Сначала нажми «1. Открыть ON Social»."
            )

            return

        self.log(
            "Проверяю текущую страницу..."
        )

        self.list_page.bring_to_front()

        title = self.list_page.title()

        url = self.list_page.url

        self.log(
            f"Страница: {title}"
        )

        self.log(
            f"URL: {url}"
        )

        parser = OnSocialParser(
            self.context,
            log=self.log,
        )

        buttons = parser.find_analyze_buttons(
            self.list_page,
        )

        unlock = parser.find_unlock_button(
            self.list_page,
        )

        self.log(
            f"Доступных Analyze: {len(buttons)}"
        )

        self.log(
            "Unlock next: найден"
            if unlock
            else
            "Unlock next: не найден"
        )

        if not buttons:

            self.log(
                "Analyze не найдены. "
                "Убедись, что открыта страница "
                "Influencer Identification со списком блогеров."
            )

    # ============================================================
    # CONNECT SHEETS — WORKER
    # ============================================================

    def _worker_connect_sheets(self, args):

        spreadsheet_id = args["spreadsheet_id"]
        sheet_name = args["sheet_name"]

        if not self.context:

            raise RuntimeError(
                "Сначала нажми «1. Открыть ON Social»."
            )

        self.log(
            "Подключаю Google Sheets через браузер..."
        )

        self.sheets = GoogleSheetsWriter(
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            browser_context=self.context,
            log=self.log,
        )

        self.sheets.connect()

        # --------------------------------------------------------
        # НЕ вызываем ensure_header().
        #
        # Пользователь сам определяет начальную ячейку.
        # Ничего в таблице при подключении не перезаписываем.
        # --------------------------------------------------------

        self.sheets_ready = True
        self.table_cache_pairs.clear()
        self.table_cache_loaded = False
        self.root.after(
            0,
            lambda: self.table_cache_status_var.set(
                "Таблица подключена. Автоматически загружаю данные только целевого листа и комбинации в память..."
            ),
        )

        self.log(
            "Google Sheets подключена."
        )
        self.log(
            "Автоматически загружаю данные ТОЛЬКО ЦЕЛЕВОГО ЛИСТА во временную память дедупликации..."
        )

        # Автоматическая первоначальная загрузка после подключения таблицы.
        # Повторное нажатие отдельной кнопки остаётся доступным для ручного обновления.
        self.root.after(200, self.load_table_cache)

        self.log(
            f"Лист: {sheet_name}"
        )

        self.log(
            "Google Sheets API НЕ используется."
        )

        if self.list_page:

            self.list_page.bring_to_front()

            self.log(
                "Возвращаюсь в ON Social."
            )

    # ============================================================
    # LOAD FULL TABLE — WORKER
    # ============================================================

    def _worker_load_table_cache(self):
        if not self.context:
            raise RuntimeError("Сначала нажми «1. Открыть ON Social».")
        if not self.sheets:
            raise RuntimeError("Сначала подключи Google Sheets.")

        self.table_cache_pairs.clear()
        self.table_cache_loaded = False
        self.worker_busy = True
        self.root.after(
            0,
            lambda: self.table_cache_status_var.set(
                "Загрузка целевого листа... запуск парсера заблокирован."
            ),
        )

        try:
            pairs = self.sheets.load_target_sheet_profile_email_pairs()
            self.table_cache_pairs = {
                (
                    OnSocialParser.normalize_social_url(social),
                    OnSocialParser.normalize_email(email),
                )
                for social, email in pairs
                if social
            }
            self.table_cache_loaded = True

            count = len(self.table_cache_pairs)
            self.root.after(
                0,
                lambda c=count: self.table_cache_status_var.set(
                    f"Память дедупликации загружена: {c:,} комбинаций. "
                    "Теперь можно нажимать «НАЧАТЬ»."
                ),
            )
            self.log(
                "✅ КЭШ ТАБЛИЦЫ УСПЕШНО ЗАГРУЖЕН В ПАМЯТЬ"
            )
            self.log(
                f"Собрано и сохранено для сравнения: {count:,} уникальных комбинаций «ссылка + email»."
            )
            self.log(
                "Эти данные живут до закрытия приложения и пополняются каждой новой успешно записанной комбинацией."
            )
            self.worker_busy = False
        except Exception:
            self.table_cache_pairs.clear()
            self.table_cache_loaded = False
            self.worker_busy = False
            self.root.after(
                0,
                lambda: self.table_cache_status_var.set(
                    "Загрузка НЕ выполнена. Обновите данные целевого листа."
                ),
            )
            raise

    # ============================================================
    # START PARSER — WORKER
    # ============================================================

    def _worker_start_parser(self, args):

        max_influencers = args["max_influencers"]
        max_unlocks = args["max_unlocks"]
        start_cell = args["start_cell"]

        if not self.context or not self.list_page:

            raise RuntimeError(
                "Браузер ON Social не открыт."
            )

        if not self.sheets:

            raise RuntimeError(
                "Google Sheets не подключена."
            )

        self.stop_event.clear()

        # ------------------------------------------------------
        # СНАЧАЛА фиксируем начальную ячейку.
        # Это важно, потому что от её колонки зависит, где в
        # таблице лежат social_url и email, которые мы читаем
        # для дедупликации.
        # ------------------------------------------------------

        self.sheets.set_start_cell(start_cell)

        parser = OnSocialParser(
            self.context,
            log=self.log,
            stop_checker=self.stop_event.is_set,
        )

        # ------------------------------------------------------
        # ПЕРВИЧНОГО ПРОГРЕВА ВЕСЬ СПИСОК БОЛЬШЕ НЕТ.
        # ------------------------------------------------------
        # ВАЖНО: полная прокрутка списка заранее могла тратить лимит/токены
        # впустую и уводила страницу в самый низ. Теперь бот работает
        # пошагово: ищет Analyze в текущем DOM, обрабатывает найденные
        # кнопки, а только когда их не осталось — делает ОДИН ограниченный
        # шаг подгрузки следующей части списка и снова ищет Analyze.
        self.log(
            "Старт без предварительной прокрутки всего списка: "
            "Analyze будут искаться и обрабатываться по мере подгрузки списка."
        )

        # ------------------------------------------------------
        # ДЕДУПЛИКАЦИЯ: предварительный кэш целевого листа.
        # Writer дополнительно перечитывает лист перед каждой вставкой.
        # ------------------------------------------------------

        if not self.table_cache_loaded:
            raise RuntimeError(
                "Перед запуском нужно обновить данные целевого листа. "
                "Без предварительно загруженного кэша запуск запрещён."
            )

        parser.processed_profile_email_pairs.update(
            self.table_cache_pairs
        )

        self.log(
            f"Использую предварительно загруженный кэш: "
            f"{len(self.table_cache_pairs)} уникальных пар «соцсеть + email»."
        )

        processed = 0
        unlocks = 0

        # Сколько раз подряд не удалось подгрузить новые
        # открытые профили (пагинация/скролл) — защита от
        # бесконечного цикла, если сайт реально их исчерпал.
        reveal_attempts = 0
        MAX_REVEAL_ATTEMPTS = 10

        try:

            self.list_page.bring_to_front()

            self.log(
                "========================================"
            )

            self.log(
                "СТАРТ ПАРСЕРА"
            )

            self.log(
                f"Максимум блогеров: {max_influencers}"
            )

            self.log(
                f"Максимум Unlock: {max_unlocks}"
            )

            self.log(
                f"Начальная ячейка: {start_cell}"
            )

            self.log(
                "========================================"
            )

            # ====================================================
            # НАЧАЛЬНАЯ ЯЧЕЙКА УЖЕ УСТАНОВЛЕНА ДО ЧТЕНИЯ
            # ТАБЛИЦЫ ДЛЯ ДЕДУПЛИКАЦИИ.
            # ====================================================

            self.log(
                f"Запись начнётся не выше {start_cell}, ниже существующих данных"
            )

            # ====================================================
            # ОСНОВНОЙ ЦИКЛ
            # ====================================================

            while processed < max_influencers:

                if self.stop_event.is_set():

                    raise StopRequested()

                # ------------------------------------------------
                # Каждый раз получаем свежие кнопки.
                # ------------------------------------------------

                buttons = parser.find_analyze_buttons(
                    self.list_page,
                )

                # ------------------------------------------------
                # Analyze закончились.
                # ------------------------------------------------

                if not buttons:

                    # ----------------------------------------
                    # Сначала пробуем подгрузить ЕЩЁ УЖЕ
                    # ОТКРЫТЫХ блогеров (пагинация/скролл),
                    # прежде чем тратить платный Unlock.
                    # ----------------------------------------

                    if reveal_attempts < MAX_REVEAL_ATTEMPTS:

                        reveal_attempts += 1
                        if parser.reveal_more_open_profiles(
                            self.list_page,
                        ):
                            continue

                        self.log(
                            f"Analyze пока не найдены; шаг подгрузки {reveal_attempts}/{MAX_REVEAL_ATTEMPTS} не дал движения. "
                            "Повторяю поиск Analyze, прежде чем использовать Unlock."
                        )
                        continue

                    if unlocks >= max_unlocks:

                        self.log(
                            "Доступных Analyze нет "
                            "и лимит Unlock достигнут."
                        )

                        break

                    if parser.unlock_next(
                        self.list_page,
                    ):

                        unlocks += 1

                        reveal_attempts = 0

                        self.log(
                            f"Unlock использован: "
                            f"{unlocks}/{max_unlocks}"
                        )

                        continue

                    self.log(
                        "Нет доступных Analyze "
                        "и кнопки Unlock next."
                    )

                    break

                # ------------------------------------------------
                # Новая партия кнопок появилась — сбрасываем
                # счётчик неудачных попыток подгрузки.
                # ------------------------------------------------

                reveal_attempts = 0

                # ------------------------------------------------
                # Берём первую кнопку.
                # ------------------------------------------------

                button = buttons[0]

                profile_page = None

                try:

                    self.log(
                        f"[{processed + 1}/{max_influencers}] "
                        f"Открываю Analyze..."
                    )

                    profile_page = (
                        parser.click_analyze_and_get_page(
                            self.list_page,
                            button,
                        )
                    )

                    # --------------------------------------------
                    # Помечаем именно эту кнопку/Analyze как
                    # обработанную СРАЗУ после клика — раньше
                    # это нигде не вызывалось, из-за чего бот
                    # мог снова и снова находить и открывать
                    # тот же самый профиль, если он оставался
                    # в DOM после возврата к списку.
                    # --------------------------------------------

                    # The report key was captured before navigation; do not resolve
                    # a list locator again after it has navigated to a profile.

                    # --------------------------------------------
                    # Извлечение профиля
                    # --------------------------------------------

                    data = parser.extract_profile(
                        profile_page,
                    )

                    self.log(
                        f"Получено: "
                        f"имя={data.name!r}, "
                        f"ссылка={data.social_url!r}, "
                        f"email={len(data.contacts)}"
                    )

                    rows = [
                        [data.name, data.social_url, contact]
                        for contact in (data.contacts or [""])
                    ]
                    # The writer checks a fresh sheet snapshot, including empty
                    # contacts, and returns only rows whose insertion was verified.
                    written_rows = self.sheets.append_rows(rows)
                    self.table_cache_pairs = set(self.sheets.verified_pairs)
                    parser.processed_profile_email_pairs = set(self.table_cache_pairs)
                    if not written_rows:
                        self.log("Профиль пропущен: новых строк для таблицы нет.")
                        continue
                    next_cell = self.sheets._cell_to_string(
                        self.sheets.current_column, self.sheets.current_row
                    )
                    self.root.after(0, lambda cell=next_cell: self.start_cell_var.set(cell))
                    self.log(f"Подтверждено новых строк: {len(written_rows)}.")

                    processed += 1

                    self.log(
                        f"Блогер обработан: "
                        f"{processed}/{max_influencers}"
                    )

                except (StopRequested, SheetsSafetyError):

                    raise

                except Exception as e:

                    self.log(
                        "Ошибка профиля: "
                        + repr(e)
                    )

                    self.log(
                        traceback.format_exc()
                    )

                finally:

                    # --------------------------------------------
                    # Возвращаемся к списку
                    # --------------------------------------------

                    if profile_page:

                        try:

                            parser.go_back_to_list(
                                profile_page,
                                self.list_page,
                            )

                        except Exception as e:

                            self.log(
                                "Не удалось корректно "
                                "вернуться к списку: "
                                + repr(e)
                            )

                    # --------------------------------------------
                    # ПОСЛЕ КАЖДОГО ANALYZE ОБЯЗАТЕЛЬНО ОБНОВЛЯЕМ
                    # ГЛАВНУЮ СТРАНИЦУ ON SOCIAL.
                    #
                    # Это нужно для получения свежего DOM и актуального
                    # состояния списка после открытия/закрытия профиля.
                    # processed_profiles и memory-cache при этом сохраняются,
                    # поэтому обновление не сбрасывает дедупликацию.
                    # --------------------------------------------

                    try:

                        reloaded = parser.reload_list_page(
                            self.list_page,
                        )

                        if not reloaded:
                            self.log(
                                "⚠ Главную страницу OnSocial не удалось "
                                "обновить после Analyze. Продолжаю с текущим DOM, "
                                "но следующий поиск Analyze будет выполнен заново."
                            )

                    except StopRequested:

                        raise

                    except Exception as e:

                        self.log(
                            "Ошибка обновления главной страницы OnSocial "
                            "после Analyze: "
                            + repr(e)
                        )

                    # ----------------------------------------
                    # АНТИ-БАН: случайная пауза перед
                    # следующим блогером, чтобы не долбить
                    # сайт с одинаковыми интервалами.
                    # ----------------------------------------

                    if processed < max_influencers:

                        parser.human_delay(
                            self.list_page,
                        )

            self.log(
                "========================================"
            )

            self.log(
                f"ГОТОВО. "
                f"Обработано блогеров: {processed}; "
                f"Unlock: {unlocks}"
            )

            self.log(
                "========================================"
            )

        except StopRequested:

            self.log(
                "========================================"
            )

            self.log(
                f"ОСТАНОВЛЕНО. "
                f"Обработано блогеров: {processed}"
            )

            self.log(
                "========================================"
            )

        except Exception as e:

            if isinstance(e, SheetsSafetyError):
                self.table_cache_loaded = False
            self.log(
                "КРИТИЧЕСКАЯ ОШИБКА ПАРСЕРА: "
                + repr(e)
            )

            self.log(
                traceback.format_exc()
            )

        finally:

            # --------------------------------------------------
            # КЭШ НЕ ОЧИЩАЕМ ПОСЛЕ ОДНОГО ПРОГОНА.
            # Он живёт до закрытия приложения и уже содержит
            # комбинации, которые бот добавил в таблицу.
            # --------------------------------------------------
            current_cache_count = len(self.table_cache_pairs)
            self.root.after(
                0,
                lambda c=current_cache_count: self.table_cache_status_var.set(
                    (f"Кэш дедупликации: {c:,} ключей. Проверка выполняется перед каждой записью."
                     if self.table_cache_loaded else
                     "Запись остановлена. Проверьте таблицу и обновите данные целевого листа.")
                ),
            )

            self.log(
                f"Парсер завершён. Кэш НЕ удалён: в памяти осталось {current_cache_count:,} уникальных комбинаций."
            )

            self.stop_event.clear()

            self._set_worker_busy(
                False,
            )

    # ============================================================
    # CLOSE BROWSER — WORKER
    # ============================================================

    def _worker_close_browser(self):

        try:

            if self.context:

                try:
                    self.context.close()

                except Exception:
                    pass

                self.context = None

            if self.pw:

                try:
                    self.pw.stop()

                except Exception:
                    pass

                self.pw = None

        except Exception:
            pass

    # ============================================================
    # COMMAND QUEUE
    # ============================================================

    def _queue_command(
        self,
        command,
        args=None,
    ):

        if args is None:
            args = {}

        self.command_queue.put(
            (
                command,
                args,
            )
        )

    # ============================================================
    # OPEN BROWSER
    # ============================================================

    def open_browser(self):

        if self.browser_ready:

            self.log(
                "Браузер уже открыт."
            )

            return

        self._queue_command(
            "open_browser",
        )

    # ============================================================
    # CHECK PAGE
    # ============================================================

    def check_page(self):

        if not self.browser_ready:

            messagebox.showwarning(
                "Браузер",
                "Сначала нажми «1. Открыть ON Social».",
            )

            return

        self._queue_command(
            "check_page",
        )

    # ============================================================
    # CONNECT SHEETS
    # ============================================================

    def connect_sheets(self):

        value = self.sheet_var.get().strip()

        if not value:

            messagebox.showwarning(
                "Google Sheets",
                "Вставь URL Google Sheets.",
            )

            return

        if not self.browser_ready:

            messagebox.showwarning(
                "Браузер",
                "Сначала нажми «1. Открыть ON Social».",
            )

            return

        spreadsheet_id = (
            GoogleSheetsWriter.extract_spreadsheet_id(
                value,
            )
        )

        if not spreadsheet_id:

            messagebox.showerror(
                "Google Sheets",
                "Не удалось определить Spreadsheet ID.",
            )

            return

        sheet_name = (
            self.tab_var.get().strip()
            or "Sheet1"
        )

        self.connect_button.config(
            state="disabled",
        )

        self._queue_command(
            "connect_sheets",
            {
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
            },
        )

        self.log(
            "Команда подключения Google Sheets отправлена."
        )

        self.root.after(
            1500,
            self._restore_connect_button,
        )

    def _restore_connect_button(self):

        try:

            self.connect_button.config(
                state="normal",
            )

        except Exception:
            pass

    # ============================================================
    # LOAD FULL TABLE CACHE
    # ============================================================

    def load_table_cache(self):
        if not self.browser_ready:
            messagebox.showwarning(
                "Браузер",
                "Сначала нажми «1. Открыть ON Social».",
            )
            return

        if not self.sheets_ready:
            messagebox.showwarning(
                "Google Sheets",
                "Сначала нажми «Подключить Google Sheets».",
            )
            return

        if self.worker_busy:
            messagebox.showwarning(
                "Парсер работает",
                "Нельзя менять кэш таблицы во время обработки.",
            )
            return

        self.log("Запрашиваю загрузку целевого листа перед стартом...")
        self._queue_command("load_table_cache")

    # ============================================================
    # START
    # ============================================================

    def start(self):

        if not self.browser_ready:

            messagebox.showwarning(
                "Браузер",
                "Сначала нажми «1. Открыть ON Social».",
            )

            return

        if not self.sheets_ready:

            messagebox.showwarning(
                "Google Sheets",
                "Сначала подключи Google Sheets.",
            )

            return

        if not self.table_cache_loaded:
            messagebox.showwarning(
                "Данные таблицы не загружены",
                "Подожди, пока после подключения Google Sheets завершится автоматическая загрузка целевого листа в память.\n\n"
                "Кэш загружается один раз при подключении таблицы, затем пополняется новыми записанными комбинациями.\n"
                "Он будет полностью удалён только при закрытии приложения.",
            )
            return

        if self.worker_busy:

            messagebox.showwarning(
                "Парсер уже работает",
                "Текущий запуск ещё не завершён.",
            )

            return

        # --------------------------------------------------------
        # Лимиты
        # --------------------------------------------------------

        try:

            max_influencers = int(
                self.max_inf_var.get()
            )

            max_unlocks = int(
                self.max_unlock_var.get()
            )

        except Exception:

            messagebox.showerror(
                "Параметры",
                "Проверь значения лимитов.",
            )

            return

        # --------------------------------------------------------
        # НАЧАЛЬНАЯ ЯЧЕЙКА
        #
        # Она вводится ПОЛЬЗОВАТЕЛЕМ В ПРИЛОЖЕНИИ.
        # --------------------------------------------------------

        start_cell = (
            self.start_cell_var.get()
            .strip()
            .upper()
        )

        if not start_cell:

            messagebox.showerror(
                "Начальная ячейка",
                "Укажи начальную ячейку, например A2.",
            )

            return

        # --------------------------------------------------------
        # Проверка A1-формата.
        # --------------------------------------------------------

        if not re.fullmatch(
            r"[A-Z]{1,4}[1-9][0-9]*",
            start_cell,
        ):

            messagebox.showerror(
                "Начальная ячейка",
                "Неверный формат.\n\n"
                "Примеры:\n"
                "A2\n"
                "B5\n"
                "D10\n"
                "AA20",
            )

            return

        # --------------------------------------------------------
        # Проверяем, что строка > 0.
        # --------------------------------------------------------

        match = re.fullmatch(
            r"[A-Z]{1,4}([1-9][0-9]*)",
            start_cell,
        )

        if not match:

            messagebox.showerror(
                "Начальная ячейка",
                "Неверная начальная ячейка.",
            )

            return

        # --------------------------------------------------------
        # Запускаем.
        # --------------------------------------------------------

        self.worker_busy = True

        self.stop_event.clear()

        self.log(
            "----------------------------------------"
        )

        self.log(
            f"Начальная ячейка выбрана: {start_cell}"
        )

        self.log(
            "Запись начнётся с этой ячейки или ниже существующих данных."
        )

        self.log(
            "----------------------------------------"
        )

        self._queue_command(
            "start_parser",
            {
                "max_influencers": max_influencers,
                "max_unlocks": max_unlocks,
                "start_cell": start_cell,
            },
        )

        self.log(
            "Запуск парсера поставлен в очередь."
        )

    # ============================================================
    # STOP
    # ============================================================

    def stop(self):

        self.stop_event.set()

        self.log(
            "STOP запрошен."
        )

        self.log(
            "Текущая операция будет завершена, "
            "после чего цикл остановится."
        )

    # ============================================================
    # BUSY STATE
    # ============================================================

    def _set_worker_busy(self, value):

        def _update():

            self.worker_busy = value

        try:

            self.root.after(
                0,
                _update,
            )

        except Exception:

            self.worker_busy = value

    # ============================================================
    # CLOSE APPLICATION
    # ============================================================

    def _on_close(self):

        if self.worker_busy:

            answer = messagebox.askyesno(
                "Закрыть программу",
                "Парсер сейчас работает.\n\n"
                "Остановить его и закрыть программу?",
            )

            if not answer:
                return

            self.stop_event.set()

        # Только даём worker завершиться; кэш удаляем в _finish_close,
        # когда worker уже гарантированно остановлен.
        self.worker_shutdown.set()

        self.command_queue.put(
            (
                "__shutdown__",
                {},
            )
        )

        self.root.after(
            200,
            self._finish_close,
        )

    def _finish_close(self):

        try:

            if self.worker_thread.is_alive():

                self.root.after(
                    200,
                    self._finish_close,
                )

                return

        except Exception:
            pass

        # Полностью очищаем временную память только сейчас, когда
        # worker уже завершён и больше не использует кэш.
        cleared_count = len(self.table_cache_pairs)
        self.table_cache_pairs.clear()
        self.table_cache_loaded = False
        self.log(
            f"ПРИЛОЖЕНИЕ ЗАКРЫТО: удалены данные дедупликации из памяти ({cleared_count:,} комбинаций)."
        )

        try:
            self.root.destroy()
        except Exception:
            pass


# ================================================================
# MAIN
# ================================================================

def main():

    root = tk.Tk()

    App(root)

    root.mainloop()


if __name__ == "__main__":
    main()
