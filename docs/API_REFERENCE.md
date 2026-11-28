# Справочник

Короткая шпаргалка по методам. Примеры и объяснения ищите в [руководстве](USAGE.md). Методы возвращают исходный JSON ИТД; стабильность всех полей не гарантирована.

## Клиент

```python
Bot(token, *, base_url="https://xn--d1ah4a.com", timeout=30.0, client=None, limiter=None,
    get_retries=2, on_protocol_error="raise")
Bot.from_refresh_token(refresh_token, session_path=".itd-session.json", ...)
Bot.from_env(**kwargs)
```

- Токен передаётся без `Bearer`.
- Закрывайте бота через `with Bot(...) as bot:` или `close()`. Переданный вами `httpx.Client` останется вашим.
- `get_retries` — сколько раз повторить GET после сбоя сети или HTTP 500/502/503/504 (`0` отключает). Запись и скачивание не повторяются: [сбои сети](USAGE.md#сбои-сети).
- `set_token(token)` заменяет access-токен, `refresh_count` показывает число обновлений.
- Подробнее про сессию: [авторизация](AUTH.md).
- `from_env` читает `ITD_*` без автоматической загрузки `.env`; явные аргументы имеют приоритет. Копии `poll_options` и `dispatcher_options` передавайте опросу и диспетчеру. Имена переменных и пример — в [настройках окружения](ENVIRONMENT.md).
- `on_protocol_error="skip"` продолжает опрос после повреждённого ответа `/updates`. Последние 32 фрагмента доступны в `protocol_errors` только в памяти; подробности и риск пропуска событий — в [разборе протокола](PROTOCOL.md).

## Ошибки

| Класс | Когда |
|---|---|
| `APIError` | Ошибка HTTP. Поля `status`, `code`, `errors`, `retry_after`, `response` (разобранный JSON или `None`) |
| `NukstaRequiredError` | Код `nuksta_required`; наследует `APIError` |
| `BlockedError` | Код `blocked`; наследует `APIError` |
| `MessagesClosedError` | Код `messages_closed`; наследует `APIError` |
| `MessageValidationError` | Нарушен лимит клиента сайта при `validate_limits=True`; наследует `ValueError`, запрос не отправлен |
| `StaleClientMessageIdError` | Сообщение сервера `client_message_id is stale or clock is incorrect`; наследует `APIError` |
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
| `send_message(chat_id, text, reply_to_id=None, attachments=None, entities=None, client_message_id=None, validate_limits=False)` | Отправить |
| `send_message_to_user(user_id, text, attachments=None, entities=None, client_message_id=None, validate_limits=False)` | Отправить пользователю |
| `send_formatted(chat_id, content, **kwargs)` | Отправить `Text` |
| `send_formatted_to_user(user_id, content, **kwargs)` | Отправить `Text` пользователю |
| `edit_message(chat_id, message_id, text, attachments=None, entities=None, validate_limits=False)` | Изменить |
| `delete_messages(chat_id, message_ids, for_everyone=False)` | Удалить |
| `forward_messages(chat_id, source_chat_id, message_ids, client_message_ids=None)` | Переслать; `client_message_ids` — `list[str]`, `dict[str, str]` или `None`, см. [руководство](USAGE.md#сообщения) |
| `mark_read(chat_id, message_id)` | Прочитано до сообщения включительно |
| `mark_voice_heard(chat_id, message_id)`, `get_heard_messages(chat_id, message_ids)` | Прослушивание голосовых |

При отправке без `client_message_id` SDK создаёт UUIDv7. Явно переданный ID обрабатывается по прежним правилам. Флаг `replayed` остаётся в словаре ответа, если его прислал сервер; см. [клиентский ID](USAGE.md#клиентский-id-и-повтор).

`validate_limits=True` включает локальную проверку текста и сущностей до HTTP; правила и границы — в [лимитах сообщения](USAGE.md#лимиты-текста-и-вложений).

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
- `websocket_events(*, stop=None)` — генератор живых событий (нужен `[websocket]`). При закрытии с кодом 4001/4003 обновляет доступную refresh-сессию и переподключается: не больше трёх раз, с паузами 1, 2 и 4 секунды. Без refresh-сессии и при других ошибках исключение передаётся вызывающему коду. Пропуски событий восстанавливайте через `get_updates` или `poll`.

`ChatType` и `EntityType` — необязательные представления типа чата и текстовой сущности. Неизвестные значения сохраняют исходное число или строку и получают имя `UNKNOWN`; ответы API остаются обычными словарями. См. [примеры](PROTOCOL.md#неизвестные-значения).

### poll

```python
poll(
    since,
    handler,
    interval=2.0,
    on_checkpoint=None,
    websocket=False,
    workers=1,
    min_fetch_interval=0.2,
    retry_initial_delay=1.0,
    retry_max_delay=60.0,
    on_error=None,
    handler_retries=3,
    stop=None,
    chat_queues=False,
    chat_queue_size=64,
    source="poll",
)
```

Обрабатывает пакет и передаёт новый курсор в `on_checkpoint`. Если пакет был непустым и сдвинул курсор, следующий запрашивается сразу, иначе бот ждёт `interval`.

- `websocket=True` — сигнал «есть новое» по WebSocket, события всё равно читаются по курсору. Запросы не чаще `min_fetch_interval`. Обрыв восстанавливается с паузой от 1 до 30 секунд.
- `source="websocket"` — данные прямо из кадров и HTTP для начальной сверки, пропусков и обрывов; `source="poll"` сохраняет прежний режим. Подробнее: [источник WS](WEBSOCKET_SOURCE.md).
- `workers > 1` — разные чаты обрабатываются параллельно, порядок внутри чата сохраняется. Общее состояние защищайте блокировками.
- `chat_queues=True` — ограниченные очереди по чатам вместо ожидания всего пакета. `chat_queue_size=64` включает выполняемое событие; сохраняется только непрерывная последовательность завершённых событий. В обычном режиме требуется `event_seq` у каждого события; источник WS допускает уведомления без номера, не продвигающие курсор. Условия остановки, повторов и заполнения — в [руководстве по очередям](CHAT_QUEUES.md).
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

### Router

```python
Router(bot=None, *, self_user_id=None, match="first", **dispatcher_options)
Router.from_bot(bot, **kwargs)
```

| Что | Описание |
|---|---|
| `message(*conditions, order=0)` | Несколько обработчиков входящих сообщений с `MessageContext` или `CommandContext` |
| `on_event(event_type, *conditions, order=0)` | Обработчики других событий с `EventContext` |
| `include_router(router)` | Вложить непривязанный `Router()` |
| `middleware(func)` | Синхронная обёртка `(next_handler, ctx)` |
| `STOP` | Прекратить маршрутизацию текущего сообщения или события |
| `on_error()`, `handle(event)`, `run_polling(since=None, *, state_path=None, handle_signals=True, **kwargs)`, `close()` | Прежняя политика ошибок, polling и завершения Dispatcher |
| `filters.event_type(value)`, `filters.chat(chat_id)`, `filters.user(user_id)` | Фильтры события, чата, пользователя |
| `filters.command(name, prefix="/")`, `filters.text(value)`, `filters.regex(pattern, flags=0)` | Локальная команда, точный текст, поиск regex |
| `filters.predicate(function)`, `Filter & Filter`, `Filter \| Filter`, `~Filter` | Свой callable и комбинации фильтров |

Режимы `first`/`all`, порядок, контексты и middleware: [Router](ROUTER.md).

`filters.sticker()` выбирает входящие сообщения со стикер-сущностями; `message_stickers(message)` возвращает список исходных словарей. Только чтение: [статус стикеров](STICKERS.md).

### FSM

| Что | Описание |
|---|---|
| `FSM(store, *, namespace)` | Состояние по `(namespace, chat_id, sender_id)` |
| `fsm.get(ctx) -> FSMRecord` | Отдельный словарь `{"state": str \| None, "data": dict}` |
| `fsm.set(ctx, state, *, data=None)`, `fsm.clear(ctx)` | Заменить запись целиком или удалить её |
| `MemoryFSMStore()`, `FileFSMStore(path)` | Потокобезопасная память или атомарный JSON-файл |
| `RedisFSMStore(client, *, prefix="itd:fsm")` | Хранилище через переданный синхронный клиент |
| `RedisFSMStore.from_url(url, *, prefix="itd:fsm", **kwargs)`, `close()` | Создать и закрыть собственный клиент через extra `redis` |
| `FSMStore`, `FSMKey`, `FSMRecord` | Протокол `get`/`set`/`clear`, ключ `(namespace, chat, user)` и TypedDict записи |
| `filters.state(fsm, value)` | Фильтр сообщения по состоянию, `None` означает отсутствие активного шага |

Порядок обработки, ограничения процессов и пример: [диалоги с состоянием](FSM.md).

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
python -m mypy --strict itd_bot
```

Методы по-прежнему возвращают обычный JSON ИТД. Аннотации описывают то, что SDK передаёт и принимает, но не гарантируют формат ответов сервера.

## Типы

Описания JSON лежат в `itd_bot.schemas` и экспортируются из `itd_bot`: `ChatPayload`, `ChatsResponse`, `MessagesResponse`, `UpdateEvent`, `MessagePayload`, `AttachmentPayload`, `StickerEntity`, `UpdatesResponse`, `ReactionsResponse`, `ReactionEntry`, `ReactionValue`, `SendResponse`, `FileLinksResponse`, `FileLink`, `UploadInitResponse`, `UploadFinalizeResponse`.

`get_chats()` возвращает `ChatsResponse`, `get_chat()` и `create_direct_chat()` — `ChatPayload`, история (`get_messages`, `get_messages_around`) — `MessagesResponse`. Поля чата и страницы взяты из чтения клиентского бандла, остальные схемы — также из кода SDK и автономных тестов. Ответы не преобразуются, дополнительные поля сохраняются.

В проектах с mypy появятся более точные подсказки; прежние аннотации обработчиков и переменных иногда потребуется уточнить. Для неподписанного в схеме поля используйте исходный словарь, например `raw = cast(dict[str, Any], bot.get_chat("CHAT_ID"))` с импортами `Any` и `cast` из `typing`. Это только подсказка анализатору. Существующие значения аргументов принимаются как раньше; `PathLike[Any]` сохраняет прежнюю широту аннотаций путей. Строгая проверка SDK включена в CI.

Пример чтения страницы чатов с аннотациями: [`examples/typed_responses.py`](../examples/typed_responses.py).

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
