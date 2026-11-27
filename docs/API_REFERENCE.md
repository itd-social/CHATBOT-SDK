# Справочник

Короткая шпаргалка по методам. Примеры и объяснения ищите в [руководстве](USAGE.md). Методы возвращают исходный JSON ИТД; стабильность всех полей не гарантирована.

## Клиент

```python
Bot(token, *, base_url="https://xn--d1ah4a.com", timeout=30.0, client=None, limiter=None,
    get_retries=2)
Bot.from_refresh_token(refresh_token, session_path=".itd-session.json", ...)
```

- Токен передаётся без `Bearer`.
- Закрывайте бота через `with Bot(...) as bot:` или `close()`. Переданный вами `httpx.Client` останется вашим.
- `get_retries` — сколько раз повторить GET после сбоя сети или HTTP 500/502/503/504 (`0` отключает). Запись и скачивание не повторяются: [сбои сети](USAGE.md#сбои-сети).
- `set_token(token)` заменяет access-токен, `refresh_count` показывает число обновлений.
- Подробнее про сессию: [авторизация](AUTH.md).

## Ошибки

| Класс | Когда |
|---|---|
| `APIError` | Ошибка HTTP. Поля `status`, `code`, `errors`, `retry_after`, `response` (разобранный JSON или `None`) |
| `NukstaRequiredError` | Код `nuksta_required`; наследует `APIError` |
| `BlockedError` | Код `blocked`; наследует `APIError` |
| `MessagesClosedError` | Код `messages_closed`; наследует `APIError` |
| `RateLimitError` | Ответ 429 или локальная блокировка |
| `UploadQuotaError` | Исчерпан бюджет загрузок (наследует `APIError`) |
| `ProtocolError` | Неожиданный ответ сервера |
| `ResetRequired` | Журнал событий потерян (наследует `ProtocolError`, есть `response`) |
| `SessionStorageError` | Не удалось сохранить или сверить сессию |

Ошибки сети и таймауты приходят от `httpx`. `ErrorAction` (`SKIP`, `RETRY`, `STOP`) описывает решение об ошибке обработчика, см. [poll](#poll).

## Чаты

| Метод | Что делает |
|---|---|
| `get_chats(limit=None, before=None)` | Страница чатов и курсор событий |
| `get_chat(chat_id)`, `get_unread_count()` | Один чат, число непрочитанных |
| `create_direct_chat(user_id)`, `accept_chat(chat_id)` | Создать личный чат, принять запрос |
| `set_pinned(chat_id, pinned)`, `set_muted(chat_id, muted)`, `mark_unread(chat_id, unread=True)` | Настройки чата |
| `delete_chat(chat_id, for_everyone=False)`, `clear_history(chat_id, for_everyone=False)` | Удалить чат, очистить историю |
| `get_presence(user_id)`, `get_presence_many(user_ids)` | Онлайн-статус |

## Сообщения

| Метод | Что делает |
|---|---|
| `get_messages(chat_id, limit=None, before=None)` | История |
| `get_messages_around(chat_id, message_id, limit=40)` | История вокруг сообщения |
| `send_message(chat_id, text, reply_to_id=None, attachments=None, entities=None, client_message_id=None)` | Отправить |
| `send_message_to_user(user_id, text, attachments=None, entities=None, client_message_id=None)` | Отправить пользователю |
| `send_formatted(chat_id, content, **kwargs)` | Отправить `Text` |
| `send_formatted_to_user(user_id, content, **kwargs)` | Отправить `Text` пользователю |
| `edit_message(chat_id, message_id, text, attachments=None, entities=None)` | Изменить |
| `delete_messages(chat_id, message_ids, for_everyone=False)` | Удалить |
| `forward_messages(chat_id, source_chat_id, message_ids, client_message_ids=None)` | Переслать; `client_message_ids` — `list[str]`, `dict[str, str]` или `None`, см. [руководство](USAGE.md#сообщения) |
| `mark_read(chat_id, message_id)` | Прочитано до сообщения включительно |
| `mark_voice_heard(chat_id, message_id)`, `get_heard_messages(chat_id, message_ids)` | Прослушивание голосовых |

## Статусы в чате

| Метод | Что делает |
|---|---|
| `send_action(chat_id, action=ChatAction.TYPING, progress=None)` | Показать один статус. `progress` от 0 до 1 только для `upload_*` |
| `chat_action(chat_id, action=ChatAction.TYPING, *, interval=3.0, progress=None)` | Контекстный менеджер: повторяет статус, пока идёт блок. `interval` не меньше 1,0 |

`ChatAction`: `TYPING`, `RECORD_VOICE`, `RECORD_VIDEO`, `UPLOAD_PHOTO`, `UPLOAD_VIDEO`, `UPLOAD_VOICE`, `UPLOAD_DOCUMENT`. Принимается и обычная непустая строка.

## Файлы

| Метод | Что делает |
|---|---|
| `upload_file(source, filename=None, mime=None, metadata=None, on_progress=None)` | Загрузить путь или `bytes`, вернуть ответ с `fileId`. До 2 ГиБ, не пустой |
| `send_file(chat_id, source, text="", filename=None, mime=None, reply_to_id=None, client_message_id=None, metadata=None, on_progress=None)` | Загрузить и отправить |
| `send_photo`, `send_video`, `send_music` (`send_audio`) | Отправить медиа с `caption`. `compress=True` для фото и видео |
| `download_file(chat_id, file_id, destination=None)` | Без `destination` вернёт `bytes`, с путём запишет в новый файл и вернёт `Path` |
| `get_media(chat_id, category, before=None, limit=None)` | Вложения чата; `category: str`, категории сайта — `"0"`…`"3"`, см. [соответствие](USAGE.md#вложения-чата-по-категориям) |
| `get_file_links(chat_id, file_ids)` | Ссылки на файлы |
| `create_file_session()` | Файловая сессия (cookie) |

В обработчиках: `ctx.reply_photo`, `ctx.reply_video`, `ctx.reply_music`, `ctx.reply_file`.

## Реакции

| Метод | Что делает |
|---|---|
| `get_reaction_catalog()`, `get_reactions(chat_id, message_id)` | Каталог и текущее состояние |
| `add_reaction(chat_id, message_id, emoji)` | Добавить к уже выбранным |
| `set_reactions(chat_id, message_id, reactions, *, expected_version, operation_id)` | Заменить весь набор |

## Пользователи

`get_user(id_or_username)` и `get_me()` возвращают `User`. Подписки: `get_follow_status(user_ids)`. У `User` есть `raw`, `banner_url`, `download_banner(path)`, `pinned_post`; у поста есть `share(chat_id, text="")`. Все поля перечислены в [руководстве](USAGE.md#профили-пользователей).

## События

### Источники

- `get_updates(since, limit=None)` — один пакет: `events`, `next_event_seq`, `reset_required`. При потере журнала бросает `ResetRequired`.
- `websocket_events(*, stop=None)` — генератор живых событий (нужен `[websocket]`). Сам не переподключается и не восстанавливает пропуски.

### poll

```python
poll(since, handler, interval=2.0, on_checkpoint=None,
     websocket=False, workers=1, min_fetch_interval=0.2,
     retry_initial_delay=1.0, retry_max_delay=60.0,
     on_error=None, handler_retries=3, stop=None,
     chat_queues=False, chat_queue_size=64)
```

Обрабатывает пакет и передаёт новый курсор в `on_checkpoint`. Если пакет был непустым и сдвинул курсор, следующий запрашивается сразу, иначе бот ждёт `interval`.

- `websocket=True` — сигнал «есть новое» по WebSocket, события всё равно читаются по курсору. Запросы не чаще `min_fetch_interval`. Обрыв восстанавливается с паузой от 1 до 30 секунд.
- `workers > 1` — разные чаты обрабатываются параллельно, порядок внутри чата сохраняется. Общее состояние защищайте блокировками.
- `chat_queues=True` — ограниченные очереди по чатам вместо ожидания всего пакета. `chat_queue_size=64` включает выполняемое событие; сохраняется только непрерывная последовательность завершённых событий. Требуется `event_seq` у каждого события. Условия остановки, повторов и заполнения — в [руководстве по очередям](CHAT_QUEUES.md).
- `retry_initial_delay`, `retry_max_delay` — паузы повтора `get_updates` после сбоя сети или 5xx (1, 2, 4… секунд, потолок 60). Остальные ошибки поднимаются.
- `on_error(exc, event) -> ErrorAction | None` — решение об ошибке обработчика. Без хука или с `None` событие пропускается (`SKIP`). `RETRY` повторяет обработчик до `handler_retries` раз и может дублировать действия. `STOP` поднимает исключение, и курсор пакета не сохраняется.
- `stop` — `threading.Event`: когда он установлен, бот дорабатывает пакет, сохраняет курсор и возвращается без исключения.
- Временный сбой отметки прочтения (`read_mode="blocking"`) не теряет событие: пакет запрашивается заново, см. [сбой отметки прочтения](USAGE.md#если-обработчик-упал).

Подробности и примеры: [если обработчик упал](USAGE.md#если-обработчик-упал), [остановка бота](USAGE.md#остановка-бота).

### Dispatcher

```python
Dispatcher(bot, *, self_user_id, username=None, auto_typing=True,
           typing_interval=4.0, typing_delay=0.5, read_mode="blocking",
           deduplicate=True, dedup_capacity=4096, dedup_store=None)
Dispatcher.from_bot(bot)
```

`read_mode="batch"` отмечает последнее входящее сообщение каждого чата один раз за пакет, до первого обработчика этого чата. Значение по умолчанию остаётся `"blocking"`. Сравнение с `background` и `off` — в [бюджете запросов](REQUEST_BUDGET.md).

| Что | Описание |
|---|---|
| `command(name)`, `message()`, `photo()`, `reaction()`, `edited()`, `deleted()` | Регистрация обработчиков с контекстом |
| `on_message` (устарел, используйте `message()`), `on_event(type)` | Прежние обработчики |
| `on_error()` | Один хук `(exc, event) -> ErrorAction \| None`; уходит в `poll` |
| `handle(event)` | Обработать одно событие |
| `run_polling(since=None, state_path=None, handle_signals=True, **kwargs)` | Запустить цикл; `kwargs` уходят в `poll`. В главном потоке SIGINT и SIGTERM дают мягкую остановку, второй SIGINT поднимает `KeyboardInterrupt` |
| `close()` | Дождаться очереди отметок; `run_polling` вызывает его сам |

`read_mode`: `"blocking"` (по умолчанию, отметка до обработчика), `"background"` (в фоне, ошибки только в лог), `"off"`.

`deduplicate=False` отключает защиту от повторных сообщений. `dedup_capacity` задаёт размер стандартного `MemoryDedupStore`; внешний `dedup_store` реализует `DedupStore` (`claim`, `complete`, `release`), см. [дедупликацию](USAGE.md#защита-от-повторной-обработки).

Подробнее про контекст и приоритеты: [руководство](USAGE.md#команды-и-обработчики).

## Форматирование

`Text(text="")` с методами `add(text, styles=(), url=None)`, `bold`, `italic`, `underline`, `strike`, `code`, `spoiler`, `link`. `build()` возвращает `(text, entities)`. `utf16_length(text)` считает длину в UTF-16. Markdown не поддерживается.

## Низкоуровневые запросы

- `request(method, path, params=None, json=None)` — к маршрутам `/api/v1/messenger`.
- `api_request(method, path, params=None, json=None, content=None, files=None, headers=None, raw=False)` — к путям `/api/` и `/v1/` вашего сервера. Одновременно можно передать только один из `json`, `content`, `files`. `raw=True` вернёт `httpx.Response`. Перенаправления отключены.

Структуры `attachments`, `entities` и `reactions` остаются сырым JSON, а за корректность экспериментальных запросов отвечаете вы. Какие маршруты вообще существуют, смотрите в [списке маршрутов](ROUTE_INVENTORY.md).

## Поддержка типов

Пакет содержит маркер `py.typed`, поэтому mypy и IDE видят аннотации SDK в вашем проекте. Проверить сам SDK у себя:

```sh
python -m pip install -e ".[dev]"
python -m mypy itd_bot
```

Методы по-прежнему возвращают обычный JSON ИТД. Аннотации описывают то, что SDK передаёт и принимает, но не гарантируют формат ответов сервера.

## Типы

Описания JSON лежат в `itd_bot.schemas` и экспортируются из `itd_bot`: `UpdateEvent`, `MessagePayload`, `AttachmentPayload`, `UpdatesResponse`, `ReactionsResponse`, `ReactionEntry`, `ReactionValue`, `SendResponse`, `FileLinksResponse`, `FileLink`, `UploadInitResponse`, `UploadFinalizeResponse`.

```python
from itd_bot import MessageContext, UpdatesResponse

def first_type(batch: UpdatesResponse) -> str | None:
    events = batch.get("events", [])
    return events[0].get("type") if events else None

def sender(ctx: MessageContext) -> str | None:
    return ctx.message.get("sender_id")
```

- Это `TypedDict` с `total=False`: ни один ключ не обещан. Они нужны только для подсказок, ничего не проверяют, а методы по-прежнему возвращают обычные `dict`.
- Поля взяты из клиентского бандла, кода SDK и тестов. Это не спецификация сервера, на живом ИТД не проверялось. Где форма неизвестна (например, `reactions` в событии), стоит `Any`.
- Обработчик, у которого событие аннотировано как `dict`, mypy теперь отклонит. Аннотируйте его `UpdateEvent`.

## Лимиты

`RateLimiter(RateLimitConfig(...))` настраивает ожидание и бюджеты; один объект можно передать нескольким ботам через `Bot(..., limiter=...)`. Подробности: [лимиты запросов](RATE_LIMITS.md).
