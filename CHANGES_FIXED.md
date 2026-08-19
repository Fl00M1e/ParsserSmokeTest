# Исправления ParserOnSocial

## 1. Запуск на macOS
- Профиль Chrome больше не пишется внутрь `.app`.
- На macOS профиль хранится в `~/Library/Application Support/OnSocialLocalParser/browser_profile`.
- GitHub Actions собирает отдельные версии для Apple Silicon (`arm64`) и Intel (`x86_64`).
- Добавлена проверка подписи bundle и инструкция первого запуска.

## 2. Кнопка Next
- Добавлен поиск кнопки `Next` / `Next page` / `Go to next page`, включая `aria-label` и `title`.
- После исчерпания `Analyze` parser сначала пытается перейти на следующую страницу.
- `Unlock next` используется только когда `Next` недоступен.
- Переход считается успешным только после фактического изменения URL или набора Analyze.

## 3. Дубликаты в Google Sheets
- Перед запуском parser читает существующие 3 рабочие колонки через интерфейс Google Sheets и системный clipboard.
- Повторный e-mail не добавляется.
- Для строк без e-mail повтор профиля не добавляется.
- Дубликаты внутри одного запуска также отбрасываются до записи.

## Важно
Архитектура macOS-архива должна соответствовать процессору Mac:
- `ParserOnSocial-mac-arm64.zip` — M1/M2/M3/M4.
- `ParserOnSocial-mac-x86_64.zip` — Intel.

## 2026-08-19 — real macOS smoke test

The macOS GitHub Actions workflow now launches the actual generated `.app` bundle on the corresponding hosted macOS runner after the architecture/signing checks.

The workflow:
- opens the `.app` via Launch Services;
- waits for the real `ParserOnSocial` process to stay alive;
- captures `ps` and recent macOS unified logs;
- copies application startup data/logs when available;
- uploads diagnostics even when the smoke test fails;
- terminates only the ParserOnSocial process before the job continues/finishes.

This tests actual application startup on the ARM64 macOS runner instead of only testing that an ARM64 executable exists.
