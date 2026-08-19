from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ============================================================
# НАСТРОЙКИ
# ============================================================

BROWSER_PROFILE = Path("browser_profile")

ONSOCIAL_URL = "https://app.onsocial.ai"


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def save_text(filename, text):
    path = Path(filename)
    path.write_text(text, encoding="utf-8")
    return path.resolve()


def absolute_url(page, href):
    """
    Превращает относительный URL вроде:

        /audience-data?platform=instagram&url=123

    в полный URL.
    """

    return page.evaluate(
        """
        (href) => new URL(href, location.href).href
        """,
        href
    )


def get_visible_text(locator):
    try:
        if locator.is_visible():
            return locator.inner_text().strip()
    except Exception:
        pass

    return ""


# ============================================================
# ДАМП СТРАНИЦЫ
# ============================================================

def dump_page(page):

    lines = []

    lines.append("=" * 70)
    lines.append("PAGE DEBUG DUMP")
    lines.append("=" * 70)

    lines.append("")
    lines.append(f"URL: {page.url}")

    try:
        lines.append(f"TITLE: {page.title()}")
    except Exception:
        lines.append("TITLE: <error>")

    # --------------------------------------------------------
    # BUTTONS
    # --------------------------------------------------------

    lines.append("")
    lines.append("=" * 50)
    lines.append("=== BUTTONS ===")
    lines.append("=" * 50)

    buttons = page.locator("button")

    try:
        button_count = buttons.count()
    except Exception:
        button_count = 0

    for i in range(button_count):

        button = buttons.nth(i)

        try:
            text = button.inner_text().strip()
        except Exception:
            text = ""

        try:
            enabled = button.is_enabled()
        except Exception:
            enabled = "?"

        try:
            visible = button.is_visible()
        except Exception:
            visible = "?"

        lines.append(
            f"{i}: text={text!r} "
            f"enabled={enabled} "
            f"visible={visible}"
        )

    # --------------------------------------------------------
    # LINKS
    # --------------------------------------------------------

    lines.append("")
    lines.append("=" * 50)
    lines.append("=== LINKS ===")
    lines.append("=" * 50)

    links = page.locator("a")

    try:
        link_count = links.count()
    except Exception:
        link_count = 0

    for i in range(link_count):

        link = links.nth(i)

        try:
            text = link.inner_text().strip()
        except Exception:
            text = ""

        try:
            href = link.get_attribute("href")
        except Exception:
            href = None

        lines.append(
            f"{i}: text={text!r} href={href!r}"
        )

    # --------------------------------------------------------
    # HEADINGS
    # --------------------------------------------------------

    lines.append("")
    lines.append("=" * 50)
    lines.append("=== HEADINGS ===")
    lines.append("=" * 50)

    for tag in ["h1", "h2", "h3", "h4", "h5"]:

        locator = page.locator(tag)

        try:
            count = locator.count()
        except Exception:
            count = 0

        for i in range(count):

            try:
                text = locator.nth(i).inner_text().strip()

                lines.append(
                    f"{tag}[{i}]: {text!r}"
                )

            except Exception:
                pass

    # --------------------------------------------------------
    # MAILTO
    # --------------------------------------------------------

    lines.append("")
    lines.append("=" * 50)
    lines.append("=== MAILTO LINKS ===")
    lines.append("=" * 50)

    mailto = page.locator('a[href^="mailto:"]')

    try:
        count = mailto.count()
    except Exception:
        count = 0

    for i in range(count):

        a = mailto.nth(i)

        try:
            text = a.inner_text().strip()
        except Exception:
            text = ""

        try:
            href = a.get_attribute("href")
        except Exception:
            href = None

        lines.append(
            f"{i}: text={text!r} href={href!r}"
        )

    # --------------------------------------------------------
    # TEL
    # --------------------------------------------------------

    lines.append("")
    lines.append("=" * 50)
    lines.append("=== TEL LINKS ===")
    lines.append("=" * 50)

    tel = page.locator('a[href^="tel:"]')

    try:
        count = tel.count()
    except Exception:
        count = 0

    for i in range(count):

        a = tel.nth(i)

        try:
            text = a.inner_text().strip()
        except Exception:
            text = ""

        try:
            href = a.get_attribute("href")
        except Exception:
            href = None

        lines.append(
            f"{i}: text={text!r} href={href!r}"
        )

    # --------------------------------------------------------
    # SOCIAL LINKS
    # --------------------------------------------------------

    lines.append("")
    lines.append("=" * 50)
    lines.append("=== SOCIAL LINKS ===")
    lines.append("=" * 50)

    social_domains = [
        "instagram.com",
        "tiktok.com",
        "youtube.com",
        "facebook.com",
        "twitter.com",
        "x.com",
        "linkedin.com",
    ]

    for i in range(link_count):

        a = links.nth(i)

        try:
            href = a.get_attribute("href")
        except Exception:
            continue

        if not href:
            continue

        href_lower = href.lower()

        if any(
            domain in href_lower
            for domain in social_domains
        ):

            try:
                text = a.inner_text().strip()
            except Exception:
                text = ""

            lines.append(
                f"{i}: text={text!r} href={href!r}"
            )

    # --------------------------------------------------------
    # CONTACT TEXT
    # --------------------------------------------------------

    lines.append("")
    lines.append("=" * 50)
    lines.append("=== TEXT CONTAINING CONTACT ===")
    lines.append("=" * 50)

    try:
        body_text = page.locator("body").inner_text()
    except Exception:
        body_text = ""

    for line in body_text.splitlines():

        clean = line.strip()

        if not clean:
            continue

        if "contact" in clean.lower():
            lines.append(repr(clean))

    # --------------------------------------------------------
    # BODY TEXT
    # --------------------------------------------------------

    lines.append("")
    lines.append("=" * 50)
    lines.append("=== BODY TEXT ===")
    lines.append("=" * 50)

    lines.append(body_text)

    return "\n".join(lines)


# ============================================================
# ПОИСК ANALYZE
# ============================================================

def find_analyze_links(page):

    print()
    print("========================================")
    print(" ПОИСК ANALYZE")
    print("========================================")

    # --------------------------------------------------------
    # Даём приложению время отрисовать список.
    # --------------------------------------------------------

    print()
    print("Жду загрузку динамического содержимого...")

    page.wait_for_timeout(5000)

    # --------------------------------------------------------
    # Показываем интересующие ссылки.
    # --------------------------------------------------------

    print()
    print("Проверяю ссылки на странице...")

    all_links = page.locator("a")

    try:
        total = all_links.count()
    except Exception:
        total = 0

    print(f"Всего ссылок: {total}")

    candidates = []

    for i in range(total):

        a = all_links.nth(i)

        try:
            text = a.inner_text().strip()
        except Exception:
            text = ""

        try:
            href = a.get_attribute("href")
        except Exception:
            href = None

        if (
            text.lower() == "analyze"
            or (
                href
                and "audience-data" in href
            )
        ):

            print(
                f"[{i}] "
                f"text={text!r} "
                f"href={href!r}"
            )

    # --------------------------------------------------------
    # Ищем реальные Analyze-ссылки.
    #
    # ВАЖНО:
    #
    # Нам нужны только элементы:
    #
    # <a href="/audience-data?...url=...">
    #
    # Поэтому элементы Analyze без href
    # автоматически игнорируются.
    # --------------------------------------------------------

    for i in range(total):

        a = all_links.nth(i)

        try:
            text = a.inner_text().strip()
        except Exception:
            continue

        try:
            href = a.get_attribute("href")
        except Exception:
            continue

        if not href:
            continue

        if text.lower() != "analyze":
            continue

        if "audience-data" not in href:
            continue

        if "url=" not in href:
            continue

        candidates.append(
            {
                "index": i,
                "text": text,
                "href": href,
            }
        )

    print()
    print(
        f"Найдено доступных Analyze: "
        f"{len(candidates)}"
    )

    for item in candidates:

        print(
            f"  [{item['index']}] "
            f"{item['href']}"
        )

    return candidates


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 50)
    print(" ON SOCIAL LOCAL DEBUG TOOL")
    print("=" * 50)
    print()

    # --------------------------------------------------------
    # Создаём папку профиля Chrome.
    # --------------------------------------------------------

    BROWSER_PROFILE.mkdir(
        parents=True,
        exist_ok=True
    )

    with sync_playwright() as p:

        print("Запускаю Chrome...")

        context = p.chromium.launch_persistent_context(

            user_data_dir=str(
                BROWSER_PROFILE
            ),

            headless=False,

            channel="chrome",

            viewport={
                "width": 1440,
                "height": 1000
            },

            # Небольшая задержка запуска.
            slow_mo=50,
        )

        # ----------------------------------------------------
        # Берём существующую вкладку или создаём новую.
        # ----------------------------------------------------

        if context.pages:

            page = context.pages[-1]

        else:

            page = context.new_page()

        # ----------------------------------------------------
        # Открываем ON Social.
        # ----------------------------------------------------

        print()
        print("Открываю ON Social...")

        try:

            page.goto(
                ONSOCIAL_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

        except PlaywrightTimeoutError:

            print(
                "Предупреждение: страница "
                "не успела полностью загрузиться."
            )

        page.wait_for_timeout(3000)

        # ----------------------------------------------------
        # Инструкция пользователю.
        # ----------------------------------------------------

        print()
        print("=" * 50)
        print(" ЧТО НУЖНО СДЕЛАТЬ")
        print("=" * 50)
        print()

        print("1. Если нужно — войди в ON Social.")
        print()
        print("2. Открой:")
        print("   Influencer Identification")
        print()
        print("3. Выставь нужные фильтры.")
        print()
        print("4. Дождись появления списка блогеров.")
        print()
        print("5. НЕ нажимай Analyze.")
        print()
        print("6. НЕ нажимай Unlock next.")
        print()
        print("7. Вернись в это окно PowerShell.")
        print()
        print("8. Нажми ENTER.")
        print()

        input("> ")

        # ----------------------------------------------------
        # После ручной настройки страницы получаем
        # актуальную вкладку.
        # ----------------------------------------------------

        if not context.pages:

            print(
                "Ошибка: вкладка браузера закрыта."
            )

            return

        page = context.pages[-1]

        print()
        print("Текущая страница:")
        print(page.url)

        print()
        print("Заголовок:")
        print(page.title())

        # ----------------------------------------------------
        # Ищем Analyze.
        # ----------------------------------------------------

        analyze_links = find_analyze_links(page)

        # ----------------------------------------------------
        # Если ничего не нашли — сохраняем диагностику.
        # ----------------------------------------------------

        if not analyze_links:

            print()
            print("=" * 50)
            print(" ANALYZE НЕ НАЙДЕН")
            print("=" * 50)
            print()

            print(
                "Сохраняю HTML текущей страницы..."
            )

            html = page.content()

            html_path = save_text(
                "debug_current_page.html",
                html
            )

            print(
                f"HTML:\n{html_path}"
            )

            print()
            print(
                "Сохраняю видимый текст страницы..."
            )

            try:
                body_text = page.locator(
                    "body"
                ).inner_text()
            except Exception:
                body_text = ""

            text_path = save_text(
                "debug_current_page.txt",
                body_text
            )

            print(
                f"TEXT:\n{text_path}"
            )

            print()
            print(
                "Пришли мне содержимое "
                "debug_current_page.txt."
            )

            print()
            input(
                "ENTER для закрытия браузера..."
            )

            context.close()

            return

        # ----------------------------------------------------
        # Берём первый доступный Analyze.
        # ----------------------------------------------------

        selected = analyze_links[0]

        href = selected["href"]

        print()
        print("=" * 50)
        print(" ВЫБРАН БЛОГЕР")
        print("=" * 50)

        print()
        print(
            f"Analyze href: {href}"
        )

        # ----------------------------------------------------
        # Получаем абсолютный URL.
        # ----------------------------------------------------

        target_url = absolute_url(
            page,
            href
        )

        print()
        print(
            "Открываю страницу анализа:"
        )

        print(target_url)

        # ----------------------------------------------------
        # Переходим на страницу профиля.
        # ----------------------------------------------------

        try:

            page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=60000
            )

        except PlaywrightTimeoutError:

            print()
            print(
                "Предупреждение: переход "
                "не завершился за 60 секунд."
            )

        # ----------------------------------------------------
        # Ждём динамический контент.
        # ----------------------------------------------------

        print()
        print(
            "Жду загрузку данных профиля..."
        )

        page.wait_for_timeout(5000)

        # ----------------------------------------------------
        # Выводим результат.
        # ----------------------------------------------------

        print()
        print("=" * 50)
        print(" СТРАНИЦА ПРОФИЛЯ")
        print("=" * 50)

        print()
        print(
            f"URL: {page.url}"
        )

        print()
        print(
            f"TITLE: {page.title()}"
        )

        # ----------------------------------------------------
        # Сохраняем дамп.
        # ----------------------------------------------------

        result = dump_page(page)

        output_path = save_text(
            "debug_profile_dump.txt",
            result
        )

        print()
        print("=" * 50)
        print(" ГОТОВО")
        print("=" * 50)

        print()
        print(
            "Дамп страницы профиля:"
        )

        print(output_path)

        print()
        print(
            "Теперь открой файл:"
        )

        print(
            "debug_profile_dump.txt"
        )

        print()
        print(
            "И пришли мне его содержимое."
        )

        print()
        print(
            "Никаких Unlock next программа "
            "не нажимала."
        )

        print()
        input(
            "ENTER для закрытия браузера..."
        )

        context.close()


# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":
    main()