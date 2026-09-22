from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from dedup import EMAIL_RE, normalize_social_url, normalize_email
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


# Current ON Social actions.
ANALYZE_LABEL_RE = re.compile(
    r"^\s*(?:analy[sz]e|view)\s*$",
    re.I,
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
        if delay <= 0:
            return

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
        if max_ms <= 0:
            return

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

    def _analyze_href(self, element) -> str:
        """Use only a report link actually present in the DOM."""
        href = element.evaluate("""el => {
            const link = el.closest('a[href]') || el.querySelector('a[href]');
            return link ? link.getAttribute('href') : '';
        }""") or ""
        if not href:
            return ""
        absolute = urljoin(element.page.url, href)
        target = urlsplit(absolute)
        origin = urlsplit(element.page.url)
        if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
            return ""
        if not re.search(r"/audience-data(?:/|$)", target.path):
            return ""
        return absolute

    def get_analyze_key(self, element) -> str:
        href = self._analyze_href(element)
        if href:
            parts = urlsplit(href)
            query = parse_qsl(parts.query, keep_blank_values=True)
            social = next((normalize_social_url(v) for k, v in query if k == "url"), "")
            if social:
                return f"report:{social}"
            return "href:" + urlunsplit((parts.scheme, parts.netloc, parts.path,
                                         urlencode(sorted(query)), ""))
        # Card identity must survive the change from green Analyze to grey View.
        identity = element.evaluate(r"""el => {
            const row = el.closest('[data-profile-id], [data-influencer-id], tr, [role="row"]');
            if (row) {
                const id = row.getAttribute('data-profile-id') || row.getAttribute('data-influencer-id');
                if (id) return 'id:' + id;
                return 'row:' + row.innerText;
            }
            // Buttons are often wrapped in a div containing only the action.
            // Walk up to the card's actual identity instead of returning "parent:".
            for (let card = el.parentElement; card && card !== el.ownerDocument.body; card = card.parentElement) {
                const link = Array.from(card.querySelectorAll('a[href]')).find(a =>
                    /^@/.test(a.innerText.trim()));
                if (link) return 'social:' + link.href;
                const text = card.innerText.replace(/\b(?:analy[sz]e|view)\b/gi, '').trim();
                if (text) return 'card:' + text;
            }
            return '';
        }""") or ""
        # Keep stable IDs and URLs unchanged.
        if identity.startswith(("card:", "row:")):
            identity = re.sub(
                r"\b(?:analy[sz]e|view)\b",
                "",
                identity,
                flags=re.I,
            )
        return re.sub(r"\s+", " ", identity).strip()

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

    normalize_social_url = staticmethod(normalize_social_url)
    normalize_email = staticmethod(normalize_email)

    def get_existing_email_pairs(
        self,
        social_url: str,
        emails: list[str],
    ) -> tuple[set[tuple[str, str]], set[str]]:
        """
        Возвращает (существующие пары, новые email) для одного social URL.

        Метод используется как дополнительный защитный слой перед записью:
        в Google Sheets можно передавать только те email, чьи пары ещё
        отсутствуют в оперативном cache.
        """
        social = self.normalize_social_url(social_url)
        existing: set[tuple[str, str]] = set()
        new_emails: set[str] = set()

        if not social:
            return existing, new_emails

        for raw_email in (emails or []):
            email = self.normalize_email(raw_email)
            if not email:
                continue

            key = (social, email)
            if key in self.processed_profile_email_pairs:
                existing.add(key)
            else:
                new_emails.add(email)

        return existing, new_emails

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
        """Find fresh and previously opened reports regardless of button color."""
        self._check_stop()
        result, seen = [], set()
        candidates = page.locator("a, button, [role='button'], [role='link']")
        for i in range(candidates.count()):
            self._check_stop()
            element = candidates.nth(i)
            if not element.is_visible():
                continue
            text = (element.inner_text() or "").strip()
            aria = element.get_attribute("aria-label") or ""
            href = self._analyze_href(element)
            if not href and not (
                ANALYZE_LABEL_RE.fullmatch(text)
                or ANALYZE_LABEL_RE.fullmatch(aria)
            ):
                continue
            # Disabled grey controls can still contain a link to an existing report.
            if not element.is_enabled() and not href:
                self.log("Analyze недоступен: у неактивной кнопки нет ссылки на отчёт.")
                continue
            key = self.get_analyze_key(element)
            if not key:
                self.log("Analyze пропущен: не удалось определить профиль карточки.")
                continue
            if key in seen or key in self.processed_profiles:
                continue
            seen.add(key)
            result.append(element)
        self.log(f"Поиск Analyze: доступно {len(result)} зелёных/серых элементов.")
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

        href = self._analyze_href(element)
        if href or not element.is_enabled():
            if not href:
                raise RuntimeError("У неактивного Analyze нет ссылки на отчёт.")
            profile_page = self.context.new_page()
            try:
                profile_page.goto(href, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
                return profile_page
            except Exception:
                profile_page.close()
                raise

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

    def _capture_scroll_state(self, page: Page):
        """Сохраняет положение окна и внутренних scroll-контейнеров списка."""
        try:
            return page.evaluate("""() => {
                const items = [];
                for (const el of document.querySelectorAll('*')) {
                    const cs = getComputedStyle(el);
                    const scrollable =
                        (cs.overflowY === 'auto' || cs.overflowY === 'scroll' ||
                         cs.overflow === 'auto' || cs.overflow === 'scroll') &&
                        el.scrollHeight > el.clientHeight + 20;
                    if (!scrollable || el.scrollTop <= 0) continue;
                    items.push({
                        id: el.id || '',
                        className: typeof el.className === 'string' ? el.className : '',
                        scrollTop: el.scrollTop,
                        clientHeight: el.clientHeight,
                        scrollHeight: el.scrollHeight,
                    });
                }
                items.sort((a, b) => b.scrollTop - a.scrollTop);
                return {
                    windowY: window.scrollY || 0,
                    windowX: window.scrollX || 0,
                    containers: items.slice(0, 8),
                };
            }""")
        except Exception:
            return {"windowY": 0, "windowX": 0, "containers": []}

    def _restore_scroll_state(self, page: Page, state) -> bool:
        """Восстанавливает scrollTop после reload, включая внутренний контейнер списка."""
        try:
            return bool(page.evaluate(r"""(state) => {
                let restoredAny = false;
                for (const item of (state?.containers || [])) {
                    let el = null;
                    if (item.id) el = document.getElementById(item.id);
                    if (!el && item.className) {
                        const classes = item.className.split(/\s+/).filter(Boolean).slice(0, 6);
                        if (classes.length) {
                            try {
                                const selector = '.' + classes.map(c => CSS.escape(c)).join('.');
                                for (const candidate of document.querySelectorAll(selector)) {
                                    if (candidate.scrollHeight > candidate.clientHeight + 20) {
                                        el = candidate;
                                        break;
                                    }
                                }
                            } catch (_) {}
                        }
                    }
                    if (el) {
                        el.scrollTop = Math.min(
                            item.scrollTop || 0,
                            Math.max(0, el.scrollHeight - el.clientHeight)
                        );
                        restoredAny = true;
                    }
                }
                if (typeof state?.windowY === 'number') {
                    window.scrollTo(state.windowX || 0, state.windowY || 0);
                }
                return restoredAny || typeof state?.windowY === 'number';
            }""", state))
        except Exception:
            return False

    def _progressively_restore_scroll(self, page: Page, state, max_steps: int = 40) -> int:
        """Пошагово прокручивает список к прежней позиции, чтобы сработал lazy/infinite scroll."""
        targets = [
            {
                "id": item.get("id", ""),
                "className": item.get("className", ""),
                "target": float(item.get("scrollTop", 0) or 0),
            }
            for item in (state or {}).get("containers", [])
            if float(item.get("scrollTop", 0) or 0) > 0
        ]
        if not targets:
            return 0

        def step():
            return page.evaluate("""(targets) => {
                function findContainer(item) {
                    if (item.id) {
                        const byId = document.getElementById(item.id);
                        if (byId) return byId;
                    }
                    if (item.className) {
                        const classes = item.className.split(/\\s+/).filter(Boolean).slice(0, 6);
                        if (classes.length) {
                            try {
                                const selector = '.' + classes.map(c => CSS.escape(c)).join('.');
                                for (const candidate of document.querySelectorAll(selector)) {
                                    if (candidate.scrollHeight > candidate.clientHeight + 20) return candidate;
                                }
                            } catch (_) {}
                        }
                    }
                    return null;
                }

                const result = [];
                for (const item of targets) {
                    const el = findContainer(item);
                    if (!el) {
                        result.push(false);
                        continue;
                    }
                    const current = el.scrollTop || 0;
                    const maxTop = Math.max(0, el.scrollHeight - el.clientHeight);
                    const target = Math.min(item.target || 0, maxTop);
                    const next = Math.min(target, current + 800);
                    el.scrollTop = next;
                    el.dispatchEvent(new Event('scroll', {bubbles: true}));
                    result.push(Math.abs(next - target) < 5);
                }
                return result;
            }""", targets)

        completed = 0
        for _ in range(max_steps):
            self._check_stop()
            result = step()
            if result and all(result):
                completed += 1
                break
            completed += 1
            page.wait_for_timeout(250)
        return completed

    def reload_list_page(self, list_page: Page, attempts: int = 2) -> bool:
        """
        Обновляет список после Analyze, но сохраняет текущую позицию.
        OnSocial часто использует внутренний scroll-контейнер/виртуализацию,
        поэтому простого window.scrollY недостаточно.
        """
        self._check_stop()
        last_error = None
        scroll_state = self._capture_scroll_state(list_page)
        self.log(
            "Сохранил положение списка перед reload: "
            f"windowY={scroll_state.get('windowY', 0)}, "
            f"scroll-контейнеров={len(scroll_state.get('containers', []))}."
        )

        for attempt in range(1, attempts + 1):
            try:
                list_page.bring_to_front()
                self.log(
                    f"Обновляю главную страницу OnSocial после Analyze "
                    f"(попытка {attempt}/{attempts})..."
                )
                list_page.reload(
                    wait_until="domcontentloaded",
                    timeout=DEFAULT_TIMEOUT_MS,
                )
                try:
                    list_page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                list_page.wait_for_timeout(1800)
                restored = self._restore_scroll_state(list_page, scroll_state)
                steps = self._progressively_restore_scroll(list_page, scroll_state)
                for _ in range(4):
                    list_page.wait_for_timeout(500)

                self.log(
                    "✅ Главная страница OnSocial обновлена. "
                    f"Позиция списка восстановлена: {'ДА' if restored else 'НЕТ'}, "
                    f"пошаговое восстановление выполнено за {steps} шаг(ов). "
                    f"URL: {list_page.url}"
                )
                return True
            except Exception as exc:
                last_error = exc
                self.log(
                    f"Не удалось обновить главную страницу OnSocial "
                    f"(попытка {attempt}/{attempts}): {exc!r}"
                )
                if attempt < attempts:
                    try:
                        list_page.wait_for_timeout(1000)
                    except Exception:
                        time.sleep(1)

        self.log(
            "❌ Не удалось обновить главную страницу OnSocial "
            f"после {attempts} попыток: {last_error!r}"
        )
        return False

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

    def load_initial_full_list(self, page: Page, max_steps: int = 80, stable_rounds: int = 4) -> bool:
        """
        При первом запуске автоматически раскрывает весь доступный список.

        On Social использует lazy/infinite scroll и/или внутренние scroll-контейнеры,
        поэтому одного поиска Analyze в текущем viewport недостаточно. Мы
        постепенно прокручиваем все подходящие контейнеры и окно, пока высота
        содержимого и/или положение прокрутки перестают меняться несколько раз подряд.
        Это не нажимает Unlock и не открывает новые платные профили — только
        загружает уже доступный список в DOM.
        """
        self._check_stop()
        self.log("Первичный запуск: автоматически подгружаю весь доступный список OnSocial...")

        stable = 0
        moved_total = False
        last_signature = None

        def one_step():
            return page.evaluate("""() => {
                const all = [document.scrollingElement, document.documentElement, document.body, ...document.querySelectorAll('*')];
                const seen = new Set();
                const targets = [];
                for (const el of all) {
                    if (!el || seen.has(el)) continue;
                    seen.add(el);
                    try {
                        const sh = el.scrollHeight || 0;
                        const ch = el.clientHeight || 0;
                        if (sh > ch + 200) targets.push(el);
                    } catch (_) {}
                }

                let moved = false;
                let bottomCount = 0;
                const signature = [];

                for (const el of targets) {
                    try {
                        const maxTop = Math.max(0, (el.scrollHeight || 0) - (el.clientHeight || 0));
                        const before = el.scrollTop || 0;
                        const next = Math.min(maxTop, before + Math.max(700, Math.floor((el.clientHeight || 900) * 0.9)));
                        if (next > before + 2) {
                            el.scrollTop = next;
                            el.dispatchEvent(new Event('scroll', {bubbles: true}));
                            moved = true;
                        }
                        if (Math.abs(next - maxTop) < 5) bottomCount++;
                        signature.push([Math.round(el.scrollTop || 0), Math.round(el.scrollHeight || 0), Math.round(el.clientHeight || 0)]);
                    } catch (_) {}
                }

                try {
                    const se = document.scrollingElement || document.documentElement || document.body;
                    const maxTop = Math.max(0, se.scrollHeight - se.clientHeight);
                    const before = se.scrollTop || 0;
                    const next = Math.min(maxTop, before + Math.max(900, Math.floor(se.clientHeight * 0.9)));
                    if (next > before + 2) {
                        window.scrollTo(0, next);
                        moved = true;
                    }
                    if (Math.abs(next - maxTop) < 5) bottomCount++;
                    signature.push(['window', Math.round(se.scrollTop || 0), Math.round(se.scrollHeight || 0), Math.round(se.clientHeight || 0)]);
                } catch (_) {}

                return { moved, bottomCount, count: targets.length, signature };
            }""")

        for step in range(1, max_steps + 1):
            self._check_stop()
            state = one_step()
            sig = repr(state.get("signature", []))
            if state.get("moved"):
                moved_total = True
                stable = 0
                self.log(
                    f"Первичный список: шаг {step}/{max_steps}, "
                    f"прокручено контейнеров: {state.get('count', 0)}, "
                    f"контейнеров у низа: {state.get('bottomCount', 0)}."
                )
            else:
                if sig == last_signature:
                    stable += 1
                else:
                    stable = 1
            last_signature = sig

            page.wait_for_timeout(450)
            if stable >= stable_rounds:
                break

        # Возвращаемся к началу: после полного прогрева списка бот начнёт
        # обработку сверху, а уже загруженные карточки остаются доступными.
        try:
            page.evaluate("""() => {
                const all = [document.scrollingElement, document.documentElement, document.body, ...document.querySelectorAll('*')];
                for (const el of all) {
                    try { if (el && el.scrollHeight > el.clientHeight + 200) el.scrollTop = 0; } catch (_) {}
                }
                try { window.scrollTo(0, 0); } catch (_) {}
            }""")
        except Exception:
            pass
        page.wait_for_timeout(800)
        self.log(
            "✅ Первичная загрузка списка завершена: "
            f"список прогрет={'ДА' if moved_total else 'НЕТ/уже загружен'}. "
            "Бот начнёт поиск Analyze с верхней позиции."
        )
        return moved_total

    def reveal_more_open_profiles(
        self,
        page: Page,
    ) -> bool:
        """
        Делает ОДИН небольшой шаг прокрутки/пагинации, а затем
        управление сразу возвращается в основной цикл, который
        заново ищет Analyze.

        ВАЖНО: OnSocial использует внутренние scroll-контейнеры.
        Поэтому page.mouse.wheel() по окну недостаточно: мы ищем
        реальные прокручиваемые контейнеры и прокручиваем наиболее
        вероятный контейнер списка.
        """
        self._check_stop()

        # 1) Явная пагинация / Show more.
        pagination_re = re.compile(
            r"^\s*(next|next page|show more|load more|"
            r"показать ещё|показать еще|далее|»|›|>)\s*$",
            re.I,
        )
        try:
            candidates = page.locator("button, a, [role='button']")
            for i in range(candidates.count()):
                self._check_stop()
                el = candidates.nth(i)
                try:
                    if not el.is_visible() or not el.is_enabled():
                        continue
                    text = (el.inner_text() or "").strip()
                    aria = (el.get_attribute("aria-label") or "").strip()
                    if not (pagination_re.fullmatch(text) or pagination_re.fullmatch(aria)):
                        continue
                    el.scroll_into_view_if_needed()
                    el.click(timeout=DEFAULT_TIMEOUT_MS)
                    page.wait_for_timeout(1000)
                    self.log(f"Найдена и нажата пагинация списка: {text or aria!r}.")
                    return True
                except Exception:
                    continue
        except Exception:
            pass

        # 2) Основной путь: прокрутить внутренний контейнер списка.
        try:
            result = page.evaluate(r"""() => {
                const all = Array.from(document.querySelectorAll('*'));
                const candidates = [];

                for (const el of all) {
                    try {
                        const r = el.getBoundingClientRect();
                        const cs = getComputedStyle(el);
                        const sh = el.scrollHeight || 0;
                        const ch = el.clientHeight || 0;
                        const sw = el.scrollWidth || 0;
                        const cw = el.clientWidth || 0;
                        const oy = cs.overflowY || '';
                        const isYScroll = sh > ch + 120 && (oy === 'auto' || oy === 'scroll' || oy === 'overlay');
                        const isWindowLike = el === document.scrollingElement || el === document.documentElement || el === document.body;
                        if (!isYScroll && !isWindowLike) continue;
                        if (r.width < 250 || r.height < 180) continue;
                        if (r.bottom < 0 || r.top > window.innerHeight) continue;
                        if (cs.display === 'none' || cs.visibility === 'hidden') continue;

                        const buttons = el.querySelectorAll('button, a').length;
                        candidates.push({el, r, sh, ch, sw, cw, oy, buttons, top: el.scrollTop || 0});
                    } catch (_) {}
                }

                // Предпочитаем большой видимый вертикальный контейнер с элементами.
                candidates.sort((a, b) => {
                    const score = x =>
                        (x.buttons > 0 ? 500000 : 0) +
                        x.r.width * x.r.height +
                        (x.sh - x.ch) * 2;
                    return score(b) - score(a);
                });

                let moved = false;
                let chosen = null;
                const STEP = 900;

                for (const item of candidates.slice(0, 8)) {
                    try {
                        const maxTop = Math.max(0, item.sh - item.ch);
                        const before = Number(item.el.scrollTop || 0);
                        if (before >= maxTop - 3) continue;

                        const next = Math.min(maxTop, before + STEP);
                        item.el.scrollTop = next;
                        item.el.dispatchEvent(new Event('scroll', {bubbles: true}));

                        if (Number(item.el.scrollTop || 0) > before + 2) {
                            moved = true;
                            chosen = {
                                before,
                                after: Number(item.el.scrollTop || 0),
                                maxTop,
                                width: Math.round(item.r.width),
                                height: Math.round(item.r.height),
                                buttons: item.buttons
                            };
                            break;
                        }
                    } catch (_) {}
                }

                // Fallback: прокручиваем окно небольшим шагом.
                if (!moved) {
                    try {
                        const se = document.scrollingElement || document.documentElement || document.body;
                        const before = Number(se.scrollTop || 0);
                        const maxTop = Math.max(0, se.scrollHeight - se.clientHeight);
                        if (before < maxTop - 3) {
                            const next = Math.min(maxTop, before + STEP);
                            se.scrollTop = next;
                            window.scrollTo(0, next);
                            if (Number(se.scrollTop || 0) > before + 2) {
                                moved = true;
                                chosen = {before, after: Number(se.scrollTop || 0), maxTop, window: true};
                            }
                        }
                    } catch (_) {}
                }

                return {
                    moved,
                    chosen,
                    candidates: candidates.length
                };
            }""")

            if result and result.get("moved"):
                chosen = result.get("chosen") or {}
                self.log(
                    "Сделан один шаг прокрутки списка: "
                    f"{chosen.get('before')} -> {chosen.get('after')} "
                    f"(max={chosen.get('maxTop')}). "
                    "Теперь сразу повторно ищу Analyze."
                )
                page.wait_for_timeout(900)
                return True

            self.log(
                "Прокрутить список ещё на один шаг не удалось: "
                "все найденные контейнеры уже внизу или прокрутка не изменилась."
            )
        except Exception as exc:
            self.log(f"Ошибка при пошаговой прокрутке списка: {exc!r}")

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
