# ON Social Local Parser for macOS

Локальный парсер для личного использования:
ON Social (через видимый браузер) -> данные профиля -> Google Sheets.

> Хотите отдать это приложение кому-то как готовый `.app`, без
> установки Python/venv вручную? См. **BUILD.md** — там инструкция,
> как собрать macOS-приложение через GitHub Actions (свой Mac не нужен).

## Требования на компьютере, где запускается парсер

- Установленный **Google Chrome** (обычный, скачанный с
  google.com/chrome). Парсер запускает именно его через
  `channel="chrome"` в Playwright, а не отдельно скачанный браузер.
- Никакие логины/куки/сессии в комплекте не идут — папка
  `browser_profile` создаётся пустой при первом запуске, и вход в
  ON Social и Google Sheets выполняется вручную, в своём аккаунте.

## Важно

- Парсер НЕ использует ON Social API.
- Парсер НЕ пытается обходить CAPTCHA, лимиты, paywall или защиту сайта.
- Авторизация ON Social выполняется вручную в локальном профиле браузера.
- Google Sheets используется БЕЗ Google Sheets API: приложение открывает таблицу в том же браузере (где вы уже залогинены в Google), кладёт данные в системный буфер обмена и вставляет их через Ctrl+V/Cmd+V, как это сделал бы человек руками.
- `credentials.json` и `token.json` не используются — их не нужно создавать.
- Данные отправляются в Google только потому, что целевая таблица находится в Google Sheets.
- Перед массовой автоматизацией проверь, разрешает ли твой аккаунт/тариф ON Social такую автоматизацию. Их Terms of Service широко определяют scraping, поэтому отсутствие API не означает автоматическое разрешение на browser automation.

## 1. Создать окружение

```bash
cd onsocial_local_parser
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Отдельно скачивать браузер (`playwright install chromium`) не нужно —
приложение запускает уже установленный в системе Google Chrome
(`channel="chrome"`), а не свой движок. Убедитесь, что Chrome
установлен обычным способом с google.com/chrome.

## 2. Запуск

```bash
source .venv/bin/activate
python app.py
```

Вставь URL таблицы Google Sheets и имя листа, например `Sheet1`.

## 3. Первый запуск

Нажми `Открыть ON Social`.
В открывшемся браузере вручную войди в ON Social и открой страницу Influencer Identification.
После этого нажми `Проверить страницу`.

Затем можно запустить тест на 1 блогере.

## 4. Логика

1. Бот собирает доступные кнопки Analyze.
2. Нажимает Analyze у одного блогера.
3. На странице профиля ищет:
   - имя;
   - ссылку на социальную сеть по активному @username;
   - email и/или телефон в блоке Contacts.
4. Каждая контактная запись получает отдельную строку.
5. После обработки возвращается к списку.
6. Когда доступных Analyze больше нет, бот может нажать Unlock next.
7. Число Unlock ограничивается настройкой `Максимум Unlock`.
8. После каждого шага проверяется кнопка STOP.

## Безопасный режим

По умолчанию:
- 1 профиль за раз;
- задержка между действиями;
- ограничение количества блогеров;
- ограничение Unlock;
- без параллельных вкладок;
- без обхода CAPTCHA/защит;
- STOP останавливает цикл.

Если ON Social начинает показывать CAPTCHA, ошибки или необычные проверки — останови бот и не пытайся обходить их.

## Примечание о селекторах

ON Social — динамический React-подобный сайт. Без доступа к DOM конкретной версии сайта невозможно гарантировать вечные CSS-селекторы по одному скриншоту. Код использует текст/role-селекторы и эвристики, а все ключевые параметры вынесены в `config.py`.

Если сайт изменит DOM, сначала запускай `debug_page.py` и смотри, какие элементы реально присутствуют.

## macOS: автоматическая проверка запуска

GitHub Actions для macOS теперь проверяет не только архитектуру `.app`, но и фактическую загрузку приложения на ARM64/Intel runner.

Дополнительно упакованный бинарник поддерживает:

`ParserOnSocial.app/Contents/MacOS/ParserOnSocial --self-test`

Этот режим проверяет загрузку Python-модулей, запись в `~/Library/Application Support/OnSocialLocalParser`, Playwright driver, наличие Google Chrome и запуск persistent Chrome-профиля.

### Важный нюанс Gatekeeper

В CI используется ad-hoc подпись, чтобы можно было проверить целостность и запуск `.app` без сертификата Apple. Для распространения скачанного `.zip` через интернет macOS может дополнительно применить quarantine/Gatekeeper. Для полностью бесшовного двойного клика без предупреждений нужен Developer ID + notarization Apple. Это отдельный этап подписи релизов и не связан с архитектурой ARM64.


Если macOS после скачивания архива из GitHub сообщает, что программу нельзя открыть, это может быть не ошибка ARM64-сборки, а Gatekeeper/quarantine. Тест CI уже доказывает запуск на чистом ARM64 runner, но для публичного распространения нужен Developer ID + notarization Apple. До этого для локальной проверки можно открыть приложение через контекстное меню Finder → Open; не следует удалять файлы приложения или пересобирать его из-за одного только предупреждения Gatekeeper.

## Duplicate protection: preload the whole spreadsheet

Before every parser run, click **"Загрузить данные ВСЕЙ таблицы"** after connecting the spreadsheet.

The app reads all tabs through the browser/Clipboard, extracts normalized social-network URL + email pairs, and keeps only those pairs in RAM for the current run. The parser then checks each discovered profile against this in-memory set and skips any profile for which a matching pair already exists.

The cache is intentionally cleared after the run (normal completion, stop, or error). The next run requires a new preload, so the app does not keep the previous table snapshot between runs.

If even one sheet tab cannot be enumerated/read, preload fails closed and the parser is not allowed to start.
