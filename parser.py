from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

from playwright.sync_api import (
    Page,
    BrowserContext,
)

from config import (
    ACTION_DELAY_MS,
    DEFAULT_TIMEOUT_MS,
    CONTACTS_HEADING,
    CONTACTS_END_MARKERS,
    UNLOCK_BUTTON_RE,
    MIN_PROFILE_DELAY_MS,
    MAX_PROFILE_DELAY_MS,
)


# ============================================================
# EMAIL
# ============================================================

EMAIL_RE = re.compile(
    r"(?i)"
    r"(?<![\w.+-])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@"
    r"[A-Za-z0-9-]+"
    r"(?:\.[A-Za-z0-9-]+)+"
    r"(?![\w.-])"
)


@dataclass
class ProfileData:
    name: str
    social_url: str
    contacts: list[str]


class StopRequested(Exception):
    pass


class OnSocialParser:

    def __init__(
        self,
        context: BrowserContext,
        log: Callable[[str], None] = print,
        stop_checker: Optional[Callable[[], bool]] = None,
    ):
        self.context = context
        self.log = log
        self.stop_checker = stop_checker or (lambda: False)

        # ====================================================
        # ВАЖНО:
        #
        # processed_profiles защищает только от повторного клика
        # по одной и той же кнопке Analyze внутри текущей сессии.
        # Проверка «есть ли уже запись в таблице» выполняется
        # отдельно и именно по паре «соцсеть + email».
        # ====================================================

        self.processed_profiles: set[str] = set()
        self.processed_profile_email_pairs: set[tuple[str, str]] = set()

    # ========================================================
    # BASIC
    # ========================================================

    def _check_stop(self):
        if self.stop_checker():
            raise StopRequested()

    def _pause(
        self,
        page: Optional[Page] = None,
        ms: Optional[int] = None,
    ):
        self._check_stop()

        if page is None:
            page = self.current_page()

        delay = ACTION_DELAY_MS if ms is None else ms

        try:
            page.wait_for_timeout(delay)
        except Exception:
            time.sleep(delay / 1000)

    def human_delay(
        self,
        page: Page,
        min_ms: int = MIN_PROFILE_DELAY_MS,
        max_ms: int = MAX_PROFILE_DELAY_MS,
    ):
        """
        Анти-бан: случайная (не фиксированная) пауза между
        обработкой блогеров, имитирующая паузы живого человека.

        Всегда используем случайное число в диапазоне, а не
        одно и то же значение — именно ровные одинаковые
        интервалы легче всего детектятся как бот.
        """

        self._check_stop()

        delay_ms = random.randint(min_ms, max_ms)

        self.log(
            f"Пауза {delay_ms / 1000:.1f} сек. перед следующим "
            f"блогером (анти-бан)..."
        )

        try:
            page.wait_for_timeout(delay_ms)
        except Exception:
            time.sleep(delay_ms / 1000)

    def current_page(self) -> Page:
        if not self.context.pages:
            return self.context.new_page()

        return self.context.pages[-1]

    # ========================================================
    # PROFILE KEY
    # ========================================================

    def get_analyze_key(self, element) -> str:
        """
        Получает стабильный идентификатор Analyze.

        В первую очередь используем href.

        Если href отсутствует, пытаемся использовать текст
        родительского блока.

        Это нужно именно для ситуации, когда после Analyze
        кнопка остаётся на странице.
        """

        try:
            href = element.get_attribute("href")

            if href:
                href = href.strip()

                if href:
                    return f"href:{href}"

        except Exception:
            pass

        # ----------------------------------------------------
        # Fallback: текст родительского блока.
        # ----------------------------------------------------

        try:
            parent = element.locator("xpath=..")

            text = parent.inner_text().strip()

            text = re.sub(
                r"\s+",
                " ",
                text,
            )

            if text:
                return f"parent:{text[:500]}"

        except Exception:
            pass

        # ----------------------------------------------------
        # Последний fallback.
        # ----------------------------------------------------

        try:
            text = element.inner_text().strip()

            if text:
                return f"text:{text}"

        except Exception:
            pass

        return f"element:{id(element)}"

    def is_already_processed(self, element) -> bool:
        key = self.get_analyze_key(element)

        return key in self.processed_profiles

    def mark_processed_element(self, element):
        key = self.get_analyze_key(element)

        self.processed_profiles.add(key)

        self.log(
            f"Профиль помечен как обработанный: {key}"
        )

    def mark_processed_profile(
        self,
        social_url: str,
        name: str,
    ):
        """
        После открытия профиля добавляем ещё и фактический
        URL профиля.

        Это дополнительная защита от повторной обработки.
        """

        if social_url:
            key = f"profile:{social_url}"

            self.processed_profiles.add(key)

            self.log(
                f"Сохранён URL обработанного профиля: "
                f"{social_url}"
            )

        elif name:
            key = f"name:{name.lower().strip()}"

            self.processed_profiles.add(key)

    def profile_was_processed(
        self,
        social_url: str,
        name: str,
    ) -> bool:

        if social_url:
            if f"profile:{social_url}" in self.processed_profiles:
                return True

        if name:
            if (
                f"name:{name.lower().strip()}"
                in self.processed_profiles
            ):
                return True

        return False

    @staticmethod
    def normalize_social_url(value: str) -> str:
        from urllib.parse import urlsplit, urlunsplit

        value = (value or "").strip()
        if not value:
            return ""

        try:
            parts = urlsplit(value)
            if not parts.netloc:
                return value.rstrip("/").lower()

            scheme = parts.scheme.lower()
            hostname = (parts.hostname or "").lower()
            port = parts.port
            netloc = hostname

            if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
                netloc = f"{hostname}:{port}"

            path = parts.path.rstrip("/") or ""
            return urlunsplit((scheme, netloc, path, "", ""))
        except Exception:
            return value.rstrip("/").lower()

    @staticmethod
    def normalize_email(value: str) -> str:
        return re.sub(r"\s+", "", (value or "").strip()).lower()

    def profile_has_existing_email_pair(
        self,
        social_url: str,
        emails: list[str],
    ) -> bool:
        """True, если хотя бы одна реальная пара social URL + email уже есть."""
        social = self.normalize_social_url(social_url)
        if not social:
            return False

        for email in (emails or []):
            normalized_email = self.normalize_email(email)
            if not normalized_email:
                continue

            key = (social, normalized_email)
            if key in self.processed_profile_email_pairs:
                self.log(
                    f"Найдена существующая пара: {social} + {normalized_email}"
                )
                return True

        return False

    def mark_processed_email_pairs(
        self,
        social_url: str,
        emails: list[str],
    ):
        social = self.normalize_social_url(social_url)
        if not social:
            return

        count = 0
        for email in (emails or []):
            normalized_email = self.normalize_email(email)
            if not normalized_email:
                continue
            self.processed_profile_email_pairs.add(
                (social, normalized_email)
            )
            count += 1

        self.log(
            f"Сохранены комбинации «соцсеть + email»: {count}"
        )

    # ========================================================
    # ANALYZE SEARCH
    # ========================================================

    def find_analyze_buttons(self, page: Page):
        """
        Ищет Analyze среди button и link.

        ВАЖНО:
        Уже обработанные Analyze здесь сразу отбрасываются.
        """

        self._check_stop()

        result = []

        # ----------------------------------------------------
        # BUTTON
        # ----------------------------------------------------

        try:
            buttons = page.get_by_role(
                "button",
                name=re.compile(
                    r"^\s*Analyze\s*$",
                    re.I,
                ),
            )

            for i in range(buttons.count()):

                self._check_stop()

                element = buttons.nth(i)

                try:
                    if not element.is_visible():
                        continue

                    if not element.is_enabled():
                        continue

                    if self.is_already_processed(element):
                        self.log(
                            "Analyze пропущен: "
                            "этот профиль уже обрабатывался."
                        )
                        continue

                    result.append(element)

                except Exception:
                    continue

        except Exception:
            pass

        # ----------------------------------------------------
        # LINK
        # ----------------------------------------------------

        try:
            links = page.get_by_role(
                "link",
                name=re.compile(
                    r"^\s*Analyze\s*$",
                    re.I,
                ),
            )

            for i in range(links.count()):

                self._check_stop()

                element = links.nth(i)

                try:
                    if not element.is_visible():
                        continue

                    if self.is_already_processed(element):
                        self.log(
                            "Analyze link пропущен: "
                            "профиль уже обрабатывался."
                        )
                        continue

                    result.append(element)

                except Exception:
                    continue

        except Exception:
            pass

        # ----------------------------------------------------
        # FALLBACK
        # ----------------------------------------------------

        if not result:

            try:

                candidates = page.locator(
                    "a, button"
                )

                for i in range(
                    candidates.count()
                ):

                    self._check_stop()

                    element = candidates.nth(i)

                    try:

                        if not element.is_visible():
                            continue

                        text = (
                            element
                            .inner_text()
                            .strip()
                        )

                        if not re.fullmatch(
                            r"Analyze",
                            text,
                            re.I,
                        ):
                            continue

                        if self.is_already_processed(
                            element
                        ):
                            continue

                        result.append(
                            element
                        )

                    except Exception:
                        continue

            except Exception:
                pass

        self.log(
            f"Поиск Analyze: доступно новых: "
            f"{len(result)}"
        )

        return result

    # ========================================================
    # UNLOCK
    # ========================================================

    def find_unlock_button(
        self,
        page: Page,
    ):

        self._check_stop()

        try:

            buttons = page.get_by_role(
                "button",
                name=re.compile(
                    UNLOCK_BUTTON_RE,
                    re.I,
                ),
            )

            for i in range(
                buttons.count()
            ):

                button = buttons.nth(i)

                try:

                    if (
                        button.is_visible()
                        and button.is_enabled()
                    ):
                        return button

                except Exception:
                    pass

        except Exception:
            pass

        return None

    # ========================================================
    # PHYSICAL CLICK
    # ========================================================

    def _physical_click(
        self,
        page: Page,
        element,
    ):

        self._check_stop()

        try:
            element.scroll_into_view_if_needed()
        except Exception:
            pass

        page.wait_for_timeout(300)

        box = element.bounding_box()

        if not box:
            raise RuntimeError(
                "Не удалось получить координаты Analyze."
            )

        x = (
            box["x"]
            + box["width"] / 2
        )

        y = (
            box["y"]
            + box["height"] / 2
        )

        self.log(
            f"Физический клик: "
            f"x={x:.1f}, y={y:.1f}"
        )

        # Реальное движение мыши.
        page.mouse.move(
            x,
            y,
            steps=10,
        )

        page.wait_for_timeout(150)

        self._check_stop()

        # Реальное нажатие.
        page.mouse.down()

        page.wait_for_timeout(120)

        self._check_stop()

        # Реальное отпускание.
        page.mouse.up()

        page.wait_for_timeout(400)

    # ========================================================
    # CLICK ANALYZE
    # ========================================================

    def click_analyze_and_get_page(
        self,
        list_page: Page,
        element,
    ) -> Page:

        self._check_stop()

        # Запоминаем идентификатор ДО клика.
        analyze_key = self.get_analyze_key(
            element
        )

        self.log(
            f"Выбран Analyze: {analyze_key}"
        )

        # Сразу считаем эту кнопку использованной.
        #
        # Это КЛЮЧЕВОЙ момент:
        # даже если после возврата ON Social оставит
        # старый Analyze, parser его больше не возьмёт.
        self.processed_profiles.add(
            analyze_key
        )

        before_pages = list(
            self.context.pages
        )

        before_page_set = set(
            before_pages
        )

        before_url = list_page.url

        before_title = list_page.title()

        # ----------------------------------------------------
        # ФИЗИЧЕСКИЙ КЛИК
        # ----------------------------------------------------

        self.log(
            "Нажимаю Analyze физической мышью..."
        )

        self._physical_click(
            list_page,
            element,
        )

        self.log(
            "Analyze нажат. "
            "Жду страницу профиля..."
        )

        # ----------------------------------------------------
        # Ожидание
        # ----------------------------------------------------

        deadline = (
            time.time()
            + DEFAULT_TIMEOUT_MS / 1000
        )

        while time.time() < deadline:

            self._check_stop()

            # -----------------------------------------------
            # Новая вкладка
            # -----------------------------------------------

            for page in self.context.pages:

                if page not in before_page_set:

                    self.log(
                        "Открыта новая вкладка профиля."
                    )

                    try:
                        page.wait_for_load_state(
                            "domcontentloaded",
                            timeout=5000,
                        )
                    except Exception:
                        pass

                    page.wait_for_timeout(
                        1200
                    )

                    return page

            # -----------------------------------------------
            # Та же вкладка изменила URL
            # -----------------------------------------------

            try:

                if (
                    list_page.url
                    != before_url
                ):

                    self.log(
                        "Текущая вкладка перешла "
                        "на профиль."
                    )

                    list_page.wait_for_timeout(
                        1200
                    )

                    return list_page

            except Exception:
                pass

            list_page.wait_for_timeout(
                250
            )

        raise RuntimeError(
            "Analyze был физически нажат, "
            "но профиль не открылся "
            "за отведённое время."
        )

    # ========================================================
    # PROFILE EXTRACTION
    # ========================================================

    def extract_profile(
        self,
        page: Page,
    ) -> ProfileData:

        self._check_stop()

        page.bring_to_front()

        try:
            page.wait_for_load_state(
                "domcontentloaded",
                timeout=DEFAULT_TIMEOUT_MS,
            )
        except Exception:
            pass

        # Ждём React.
        page.wait_for_timeout(
            1800
        )

        body_text = (
            page.locator("body")
            .inner_text(
                timeout=DEFAULT_TIMEOUT_MS
            )
        )

        # ----------------------------------------------------
        # SOCIAL URL
        # ----------------------------------------------------

        username = None
        social_url = ""

        links = page.locator("a")

        for i in range(
            links.count()
        ):

            self._check_stop()

            a = links.nth(i)

            try:

                if not a.is_visible():
                    continue

                text = (
                    a.inner_text()
                    .strip()
                )

                href = (
                    a.get_attribute(
                        "href"
                    )
                    or ""
                )

                if re.fullmatch(
                    r"@[A-Za-z0-9._-]{2,}",
                    text,
                ):

                    username = text
                    social_url = href

                    break

            except Exception:
                continue

        # ----------------------------------------------------
        # NAME
        # ----------------------------------------------------

        name = self._extract_name(
            page,
            username,
        )

        # ----------------------------------------------------
        # EMAILS
        # ----------------------------------------------------

        emails = (
            self._extract_emails_from_contacts(
                body_text
            )
        )

        self.log(
            f"Профиль получен: "
            f"name={name!r}; "
            f"email={len(emails)}"
        )

        if emails:

            for email in emails:

                self.log(
                    f"EMAIL: {email}"
                )

        else:

            self.log(
                "EMAIL: не найден"
            )

        return ProfileData(
            name=name,
            social_url=social_url,
            contacts=emails,
        )

    # ========================================================
    # NAME
    # ========================================================

    def _extract_name(
        self,
        page: Page,
        username: Optional[str],
    ) -> str:

        candidates = []

        for tag in (
            "h1",
            "h2",
            "h3",
        ):

            loc = page.locator(
                tag
            )

            try:
                count = min(
                    loc.count(),
                    30,
                )
            except Exception:
                count = 0

            for i in range(
                count
            ):

                try:

                    text = (
                        loc.nth(i)
                        .inner_text()
                        .strip()
                    )

                    if not text:
                        continue

                    if text in {
                        "Influencer",
                        "Audience",
                        "Posts",
                        "Contacts",
                        "Report",
                        "View More",
                    }:
                        continue

                    if len(text) > 120:
                        continue

                    if "\n" in text:
                        continue

                    candidates.append(
                        text
                    )

                except Exception:
                    pass

        for candidate in candidates:

            if candidate:
                return candidate

        # Fallback.
        if username:

            try:

                link = page.get_by_role(
                    "link",
                    name=username,
                    exact=True,
                )

                if link.count():

                    parent = (
                        link.first
                        .locator(
                            "xpath=.."
                        )
                    )

                    text = (
                        parent
                        .inner_text()
                        .strip()
                    )

                    lines = [
                        x.strip()
                        for x in text.splitlines()
                        if x.strip()
                    ]

                    for line in lines:

                        if line == username:
                            continue

                        if len(line) <= 120:
                            return line

            except Exception:
                pass

        return ""

    # ========================================================
    # EMAILS
    # ========================================================

    def _extract_emails_from_contacts(
        self,
        body_text: str,
    ) -> list[str]:

        if not body_text:
            return []

        lower = body_text.lower()

        heading = CONTACTS_HEADING.lower()

        index = lower.find(
            heading
        )

        # Если Contacts найден —
        # работаем внутри блока Contacts.
        if index >= 0:

            chunk = body_text[
                index + len(
                    CONTACTS_HEADING
                ):
            ]

            lower_chunk = chunk.lower()

            end_positions = []

            for marker in (
                CONTACTS_END_MARKERS
            ):

                position = (
                    lower_chunk.find(
                        marker.lower()
                    )
                )

                if position >= 0:
                    end_positions.append(
                        position
                    )

            if end_positions:

                chunk = chunk[
                    :min(
                        end_positions
                    )
                ]

        else:

            self.log(
                "Contacts не найден. "
                "Ищу email по странице."
            )

            chunk = body_text

        # ----------------------------------------------------
        # ИЩЕМ ВСЕ EMAIL.
        #
        # Не первый.
        # Не один.
        # ВСЕ.
        # ----------------------------------------------------

        found = []

        for match in EMAIL_RE.findall(
            chunk
        ):

            email = (
                match
                .strip()
                .strip(
                    ".,;:!?()[]{}<>\"'`"
                )
                .lower()
            )

            if not email:
                continue

            if email not in found:

                found.append(
                    email
                )

        return found

    # ========================================================
    # GO BACK
    # ========================================================

    def go_back_to_list(
        self,
        profile_page: Page,
        list_page: Page,
    ):

        self._check_stop()

        # Новая вкладка.
        if profile_page is not list_page:

            try:

                profile_page.close()

                self.log(
                    "Профильная вкладка закрыта."
                )

            except Exception:
                pass

            list_page.bring_to_front()

            list_page.wait_for_timeout(
                1200
            )

            return

        # Та же вкладка.
        self.log(
            "Возвращаюсь к списку..."
        )

        try:

            list_page.go_back(
                wait_until="domcontentloaded",
                timeout=DEFAULT_TIMEOUT_MS,
            )

        except Exception as e:

            self.log(
                f"go_back: {e!r}"
            )

        list_page.wait_for_timeout(
            1500
        )

        self.log(
            f"Список: {list_page.url}"
        )

    # ========================================================
    # ПОДГРУЗКА ЕЩЁ ОТКРЫТЫХ ПРОФИЛЕЙ (СКРОЛЛ / NEXT)
    # ========================================================

    def reveal_more_open_profiles(
        self,
        page: Page,
    ) -> bool:
        """
        Когда Analyze на экране закончились, это ещё не значит,
        что закончились все УЖЕ ОТКРЫТЫЕ (не заблокированные)
        блогеры — их список может быть постраничным или
        подгружаться по скроллу (infinite scroll).

        До этого бот в такой ситуации сразу переходил к
        Unlock next (это про ПЛАТНОЕ открытие НОВЫХ, ещё
        заблокированных профилей — другой механизм).

        Эта функция пытается:
        1) найти и нажать явную кнопку "Next" / "next page" /
           "»" / "Показать ещё" пагинации списка;
        2) если такой кнопки нет — проскроллить список вниз,
           чтобы сработал infinite scroll.

        Возвращает True, если что-то предприняла (клик или
        скролл) — тогда стоит попробовать find_analyze_buttons
        ещё раз. Ничего не гарантирует: сама проверка "стало
        ли больше кнопок" — на стороне вызывающего кода.
        """

        self._check_stop()

        # ----------------------------------------------------
        # 1) Явная кнопка пагинации.
        # ----------------------------------------------------

        pagination_re = re.compile(
            r"^\s*(next|next page|show more|load more|"
            r"показать ещё|показать еще|далее|"
            r"»|›|>)\s*$",
            re.I,
        )

        try:
            candidates = page.locator(
                "button, a, [role='button']"
            )

            count = candidates.count()

            for i in range(count):

                self._check_stop()

                element = candidates.nth(i)

                try:

                    if not element.is_visible():
                        continue

                    text = (
                        element.inner_text() or ""
                    ).strip()

                    aria = (
                        element.get_attribute("aria-label")
                        or ""
                    ).strip()

                    if not (
                        pagination_re.fullmatch(text)
                        or pagination_re.fullmatch(aria)
                    ):
                        continue

                    if hasattr(element, "is_enabled"):
                        if not element.is_enabled():
                            continue

                    element.scroll_into_view_if_needed()

                    element.click(
                        timeout=DEFAULT_TIMEOUT_MS,
                    )

                    page.wait_for_timeout(1200)

                    self.log(
                        "Нажата кнопка пагинации списка "
                        f"({text or aria!r})."
                    )

                    return True

                except Exception:
                    continue

        except Exception:
            pass

        # ----------------------------------------------------
        # 2) Infinite scroll — просто скроллим вниз.
        # ----------------------------------------------------

        try:

            before_height = page.evaluate(
                "document.body.scrollHeight"
            )

            page.mouse.wheel(0, 2400)

            page.wait_for_timeout(1200)

            after_height = page.evaluate(
                "document.body.scrollHeight"
            )

            if after_height != before_height:

                self.log(
                    "Список проскроллен, "
                    "подгрузился дополнительный контент."
                )

                return True

            # Пробуем ещё раз чуть подождать —
            # некоторые сайты подгружают с задержкой.

            page.wait_for_timeout(1000)

            after_height_2 = page.evaluate(
                "document.body.scrollHeight"
            )

            if after_height_2 != before_height:

                self.log(
                    "Список проскроллен (с задержкой), "
                    "подгрузился дополнительный контент."
                )

                return True

        except Exception:
            pass

        return False

    # ========================================================
    # UNLOCK
    # ========================================================

    def unlock_next(
        self,
        page: Page,
    ) -> bool:

        self._check_stop()

        button = (
            self.find_unlock_button(
                page
            )
        )

        if not button:
            return False

        try:

            button.scroll_into_view_if_needed()

            page.wait_for_timeout(
                300
            )

            box = button.bounding_box()

            if not box:
                return False

            x = (
                box["x"]
                + box["width"] / 2
            )

            y = (
                box["y"]
                + box["height"] / 2
            )

            self.log(
                f"Физический клик Unlock: "
                f"x={x:.1f}, y={y:.1f}"
            )

            page.mouse.move(
                x,
                y,
                steps=8,
            )

            page.wait_for_timeout(
                150
            )

            page.mouse.down()

            page.wait_for_timeout(
                100
            )

            page.mouse.up()

            page.wait_for_timeout(
                1800
            )

            return True

        except Exception as e:

            self.log(
                f"Ошибка Unlock: {e!r}"
            )

            return False