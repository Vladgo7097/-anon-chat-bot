# Реализация спецификации

Контекст: `77.90.6.247`, `/opt/anon-chat-bot`, remote `deploy`.
Источник требований: [product-spec.md](product-spec.md).

## Карта существующего проекта

Есть: aiogram routers, SQLAlchemy models, PostgreSQL, Redis, Docker,
пересылка сообщений, заготовки профиля/платежей/модерации.
Расширяем эти подсистемы, сохраняя данные и Git-историю.

Нужно: repository layer, обязательный onboarding, миграции, UTC,
атомарный matching, устойчивые chat sessions/ratings/reveal consent,
идемпотентные платежи и entitlements, worker уведомлений/рассылок,
аудит администраторов, централизованные настройки и интеграционные тесты.

## Этапы

- [x] 1. Профиль, onboarding, обычные цветные кнопки, серверные ограничения.
- [x] 2–4. Redis очередь, matching, relay, единое завершение сессий.
- [x] 5–7. Consent, профиль, рейтинг, жалобы и модерация.
- [x] 8–10. Рефералы, streaks, offers, Stars, entitlements, Premium.
- [x] 11–12. Админка, аудит, settings, уведомления и рассылки.
- [x] 13. Интеграционные проверки, Docker healthchecks, deployment.

Живые пользовательские сообщения и рассылки из тестов не отправляются.
Финальная проверка с двух Telegram-аккаунтов требует участия владельца.

## Состояние проекта на 2026-09-05

### Тесты
- 39 тестов пройдены в изолированном окружении (PostgreSQL + Redis)
- 32 теста локально (SQLite, PostgreSQL-тесты пропущены)
- ruff check --select F821 — ошибок нет

### Реализованные функции
- Обязательный онбординг (пол → возраст 18–99 → правила → меню)
- Атомарный matching через PostgreSQL advisory lock + Redis queue
- Восстановление поиска после рестарта
- Идемпотентное завершение чата, рейтинги
- Взаимное согласие на раскрытие контактов (reveal_requests)
- Stars: каталог продуктов, snapshot цены, entitlements
- Профиль, фото с лимитами, удаление, настройки уведомлений
- Жалобы, блокировка повторного мэтча, модерация, аудит
- Уведомления: transactional, marketing, retry, Forbidden handling
- Рефералы, стрики, достижения, скидки, сезонные события
- Premium: подписки, автоматическое истечение, продление
- Статистика, retention, аналитика
- RelayService, JSON-логи, Docker HEALTHCHECK

### Последние изменения (этот сеанс)

**Админ: карточка пользователя с inline-кнопками (§17.7)**
- `handlers/admin.py`: inline-кнопки «Забанить», «Выдать Premium», «Написать от бота»
- FSM `AdminMessage.CONTENT` для ввода текста сообщения
- `services/admin.py`: `send_message_to_user()` — отправка через NotificationService + аудит
- `keyboards/inline.py`: `admin_user_card_kb()`

**Админ: просмотр статуса рассылок (§17.8)**
- `handlers/admin.py`: `admin:broadcasts` callback, просмотр sent/failed/block
- `services/admin.py`: `broadcast_status()` — список рассылок + детали
- `keyboards/inline.py`: `admin_broadcast_status_kb()`
- `models/operations.py`: добавлен `created_at` в `Broadcast`

**Админ-панель: обновлена клавиатура**
- Новая кнопка «📋 Рассылки (статус)» в `admin_kb()`
- `admin:back` — возврат в главное меню админки

### Изменённые файлы
- `handlers/admin.py` — inline карточка, FSM сообщений, статус рассылок
- `services/admin.py` — send_message_to_user, broadcast_status
- `keyboards/inline.py` — admin_user_card_kb, admin_broadcast_status_kb, admin_kb
- `models/operations.py` — created_at в Broadcast

### Ручная проверка
1. `/admin` → кнопка «👥 Пользователи» → ввести anon_id/telegram_id → карточка с inline-кнопками
2. Нажать «💬 Написать от бота» → ввести текст → сообщение доставлено
3. Нажать «📋 Рассылки (статус)» → список последних рассылок с метриками
4. Нажать на рассылку → детали: sent, failed, blocked

### Ограничения, требующие ручной проверки
- Отправка сообщений от бота (нужен живой аккаунт получателя)
- Рассылки (нужна реальная аудитория)
- Двойные Telegram-аккаунты для проверки взаимодействия
