# anon-chat-bot — карта проекта

Читай этот файл вместо сканирования `.py`-файлов при заходе в проект — экономит токены.
Если факт отсюда разошёлся с кодом (переименование, удалённый модуль) — доверяй коду и поправь этот файл.

## Что это

Telegram-бот анонимного чата (аналог "Random Coffee" / omegle-style): случайный подбор
собеседника, анкета, премиум-подписка за Telegram Stars, реклама, реферальная программа,
жалобы/баны, админ-панель.

## Стек

- Python, **aiogram 3.31** (Router-based handlers, FSM, middleware)
- **SQLAlchemy 2.x async** + **asyncpg** (PostgreSQL) — `models/base.py`: `engine`, `async_session`, `Base`
- **Redis** — очередь поиска, online-статусы, FSM storage, антифлуд (`utils/storage.py`, `services/chat_manager.py`)
- Docker Compose: `db` (postgres:16), `redis`, `bot` — см. `docker-compose.yml`
- Свои SQL-миграции в `migrations/*.sql`, раннер `migrations/runner.py` (без Alembic)

## Точка входа и запуск

`bot.py`:
- `on_startup()` — прогоняет миграции (`migrations.runner.migrate`), создаёт таблицы,
  сидирует продукты (`services.payments.seed_products`), сверяет активные чаты
  (`ChatService.reconcile`), шлёт владельцу уведомление о старте.
- `main()` — собирает `Bot`/`Dispatcher`, регистрирует роутеры из `handlers.load_routers()`,
  вешает middleware (антифлуд, дедупликация апдейтов, альбомы, access-контроль, JSON-логирование),
  запускает фоновый `workers/maintenance.py::run` (нотификации, обслуживание) и polling.
- `on_shutdown()` — закрывает Redis и engine.

Конфиг — `config.py`: токены, лимиты, цены в Stars, читается из `.env` (`python-dotenv`).

## Слои приложения

`handlers/` (aiogram Router, тонкий слой, парсинг апдейта → вызов сервиса → ответ)
→ `services/` (бизнес-логика) → `repositories/` (доступ к БД для User/Chat) → `models/` (ORM).

### handlers/ (роутеры регистрируются в `handlers/__init__.py::load_routers`)
- `start.py` — `/start`, онбординг, правила
- `menu.py` — главное меню, отмена ввода
- `search.py` — поиск собеседника, очередь, таймауты (`search_loop`, `restore_searches`/`shutdown_searches` — persist поиска между рестартами)
- `chat.py` — активный чат: пересылка сообщений, `/next`, `/stop`, разрешение показать контакт
- `profile.py` — анкета (просмотр/редактирование полей, фото, удаление)
- `settings.py` — настройки пользователя (фильтр по полу и т.п.)
- `rating.py` — оценка собеседника после чата
- `report.py` — жалобы на собеседника
- `reveal.py` — платное раскрытие контакта/статуса (Stars)
- `premium.py` — премиум-подписка: витрина, `pre_checkout`/`successful_payment` (Telegram Stars)
- `ads.py` — отслеживание вступления в рекламный канал (`ChatMemberUpdated`)
- `admin.py` — админ-панель: статистика, воронка, экспорт, жалобы, сезонные события
- `stats.py`, `help.py` — вспомогательные

### services/ (по одному файлу на домен, часто класс `*Service` + модульные функции)
- `chat_manager.py` — низкоуровневые Redis-примитивы: online, очередь поиска, антифлуд, партнёр по чату
- `chats.py::ChatService` — создание/завершение чатов, `reconcile()` при старте
- `matching.py::MatchingService` — алгоритм подбора пары
- `relay.py::RelayService` — пересылка сообщений между собеседниками
- `profile.py::ProfileService`, `onboarding.py::OnboardingService`
- `premium.py`, `payments.py::PaymentService` — премиум-статус, Stars-платежи, продукты, `seed_products`
- `reveal.py::RevealService` — платное раскрытие контакта
- `reports.py::ReportService` — жалобы и баны
- `notifications.py::NotificationService` + `enqueue()` — очередь уведомлений/рассылок (обрабатывается воркером)
- `growth.py` — стрики, ачивки, промо-офферы (`StreakService`, `GrowthService`, `SeasonalService`)
- `ads.py` — рекламные кампании, показы, вступления по инвайт-ссылкам
- `admin.py` — `StatsService`, `AdminService`, аудит админ-действий (`audit()`)
- `settings.py::SettingsService`, `backup_alert.py` — уведомление о неудачном бэкапе
- `result.py::ServiceResult` — общий тип результата операций сервисов

### repositories/
- `users.py`, `chats.py` — CRUD/запросы к `User`/`Chat` для сервисов и хендлеров

### models/ (SQLAlchemy ORM, `Base` из `models/base.py`)
- `user.py::User`
- `chat.py`: `Chat`, `ChatMessage`, `ActiveParticipant`, `Rating`, `Block`, `RevealRequest`
- `payment.py`: `Payment`, `PremiumSubscription`, `Product`, `Entitlement`
- `operations.py`: `BotSetting`, `AdminAudit`, `AnalyticsEvent`, `Referral`, `Streak`, `Achievement`,
  `Offer`, `Notification`, `Broadcast`, `AdCampaign`, `AdImpression`, `AdJoin`
- `report.py::Report`, `ban.py::Ban`

### workers/
- `maintenance.py::run(bot)` — фоновый цикл: рассылка уведомлений из очереди, обслуживание (запускается из `bot.py`)

### utils/
- `middleware.py` — `AntiFloodMiddleware`, `AccessMiddleware`
- `deduplication.py::UpdateDeduplication`, `albums.py::AlbumMiddleware`
- `storage.py::AvailableRedisStorage` — FSM storage с фолбэком
- `telegram.py::RetryTelegramRequests` — ретраи с учётом `retry_after`
- `logging.py` — JSON-логирование, `UpdateLoggingMiddleware`
- `db.py`, `time.py::utcnow`, `healthcheck.py`

### keyboards/
- `inline.py`, `reply.py` — клавиатуры; `texts.py` — тексты кнопок/сообщений

### migrations/
- Пронумерованные `.sql`-файлы (000…012), применяются `runner.py::migrate()` из `on_startup()`.
  Alembic не используется. Новую миграцию — новым файлом с следующим номером.

### tests/
- pytest, по одному файлу на область: онбординг, платежи, поиск, рассылки, приватность,
  callback-роутинг, миграции, стресс и др. Реальный Telegram не дёргается.

## Деплой

Локальный репозиторий — источник истины. Пуш идёт на `deploy` remote (bare-репо на сервере),
`deploy/post-receive` хук делает `checkout -f` в `/opt/anon-chat-bot` и пересобирает/перезапускает
контейнер `bot` через `docker compose`. **Правки на сервере мимо git теряются при следующем пуше**
(хук сохраняет патч в `.deploy-rescue/`, но не восстанавливает автоматически).

```
git push deploy <branch>
```

Прод-стек — `docker-compose.yml`: `db` (postgres:16-alpine), `redis` (7-alpine, maxmemory 128mb/LRU),
`bot` (собирается из `Dockerfile`, читает `.env`). `POSTGRES_PASSWORD` — только через `.env`, не коммитится.

## Документы в репозитории (справочные, не código)
- `docs/product-spec.md`, `docs/implementation.md` — спецификация/описание реализации
- `AUDIT_MAP.md`, `AUDIT_PASS_REPORT.md`, `DEFECTS.md`, `TECHNICAL_AUDIT_20260907.md` — истории аудитов; читай при работе над конкретным дефектом, не при каждом заходе
- `BACKUPS.md` — бэкап БД (`scripts/backup_database.sh`, `scripts/install_backup_service.sh`)
- `design/` — HTML-мокапы экранов (не используются в рантайме бота)
