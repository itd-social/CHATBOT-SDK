# Справочник SDK

См. [руководство](USAGE.md) для готовых примеров. Ответы методов — исходные JSON-значения ИТД; стабильность всех полей не гарантируется. `Any` означает, что SDK не преобразует ответ в отдельную модель.

## Клиент и ошибки

`Bot(token, *, base_url="https://xn--d1ah4a.com", timeout=30.0, client=None)` принимает токен без префикса `Bearer`. Используйте `with Bot(...) as bot:` или вызовите `close()`. Переданный `httpx.Client` остаётся во владении вызывающего кода. `set_token(token)` заменяет access-токен. `Bot.from_refresh_token(refresh_token, session_path=".itd-session.json")` включает автоматическое обновление и сохранение ротаций; `session_path=None` хранит их только в памяти. `refresh_count` — число успешных обновлений в этом экземпляре. Подробнее: [авторизация](AUTH.md).

`APIError` содержит `status`, `code`, `errors`, `retry_after`. Ошибки транспорта и таймауты исходят от `httpx`; `ProtocolError` означает неподходящий ответ. `ResetRequired` наследует `ProtocolError` и содержит `response`. В режиме refresh SDK может повторить запрос после HTTP 401 один раз; подробности и ограничения описаны в [авторизации](AUTH.md).

## Чаты и сообщения

| Метод | Назначение |
|---|---|
| `get_chats(limit=None, before=None)` | Страница чатов и курсор событий. |
| `get_chat(chat_id)`, `get_unread_count()` | Сведения о чате, количество непрочитанных. |
| `create_direct_chat(user_id)`, `accept_chat(chat_id)` | Создать личный чат, принять запрос. |
| `set_pinned(chat_id, pinned)`, `set_muted(chat_id, muted)`, `mark_unread(chat_id, unread=True)` | Настройки чата. |
| `delete_chat(chat_id, for_everyone=False)`, `clear_history(chat_id, for_everyone=False)` | Удаление чата или очистка истории. |
| `get_messages(chat_id, limit=None, before=None)` | Страница истории сообщений. |
| `get_messages_around(chat_id, message_id, limit=40)` | История вокруг сообщения. |
| `send_message(chat_id, text, reply_to_id=None, attachments=None, entities=None, client_message_id=None)` | Отправить сообщение. |
| `send_message_to_user(user_id, text, attachments=None, entities=None, client_message_id=None)` | Отправить сообщение пользователю. |
| `send_formatted(chat_id, content, **kwargs)` | Отправить содержимое конструктора `Text`. |
| `edit_message(chat_id, message_id, text, attachments=None, entities=None)` | Изменить сообщение. |
| `delete_messages(chat_id, message_ids, for_everyone=False)` | Удалить сообщения. |
| `forward_messages(chat_id, source_chat_id, message_ids, client_message_ids=None)` | Переслать сообщения. |
| `mark_read(chat_id, message_id)` | Отметить сообщения прочитанными до указанного. |
| `send_action(chat_id, action="typing", progress=None)` | Отправить действие; `progress` доступен для `upload_*`, от 0 до 1. |
| `get_presence(user_id)`, `get_presence_many(user_ids)` | Запросить присутствие. |
| `mark_voice_heard(chat_id, message_id)`, `get_heard_messages(chat_id, message_ids)` | Работа с отметками прослушивания. |

## Файлы

- `upload_file(source, filename=None, mime=None, metadata=None, on_progress=None)` загружает путь или `bytes` и возвращает ответ с `fileId`. Максимум клиента — 2 ГиБ, нулевой размер не допускается.
- `send_file(chat_id, source, text="", filename=None, mime=None, reply_to_id=None, client_message_id=None, metadata=None, on_progress=None)` загружает файл и отправляет его.
- `get_media(chat_id, category, before=None, limit=None)` и `get_file_links(chat_id, file_ids)` запрашивают сведения о вложениях.
- `create_file_session()` создаёт файловую сессию в cookie-контейнере клиента.
- `download_file(chat_id, file_id, destination=None)` получает ссылку и файловую сессию. Без `destination` возвращает `bytes`; при переданном пути записывает данные потоком в новый файл и возвращает `Path`. Существующий файл не перезаписывается. Допускаются ссылки `/v1/chat-files/{uuid}` на настроенном сервере.

## Реакции

`get_reaction_catalog()` и `get_reactions(chat_id, message_id)` возвращают каталог и состояние. `add_reaction(chat_id, message_id, emoji)` добавляет реакцию к выбранным аккаунтом, предварительно запрашивая состояние. `set_reactions(chat_id, message_id, reactions, *, expected_version, operation_id)` заменяет выбранный набор целиком. При конфликте версии сервер может вернуть 409; автоматического повтора нет.

## События и обработчики

- `get_updates(since, limit=None)` возвращает пакет событий. При `reset_required` вызывает `ResetRequired`; приложение должно восстановить своё состояние.
- `poll(since, handler, interval=2.0, on_checkpoint=None, websocket=False, workers=1, min_fetch_interval=0.2)` обрабатывает пакет событий и после полного пакета передаёт новый курсор в `on_checkpoint`. Следующий пакет запрашивается сразу, только если предыдущий был непустым и сдвинул курсор вперёд; иначе действует пауза `interval`. `websocket=True` (нужна зависимость `[websocket]`) держит фоновое WebSocket-соединение и запрашивает `/updates` после любого кадра от сервера, но не чаще одного запроса за `min_fetch_interval` секунд; события по-прежнему берутся из `/updates` с тем же курсором, поэтому дедупликация не нужна, а `interval` остаётся запасным опросом. При обрыве соединение восстанавливается с паузой от 1 до 30 секунд. На живом ИТД режим не проверялся: неизвестно, присылает ли сервер кадр на каждое новое сообщение. Если нет, поведение такое же, как у обычного опроса с интервалом `interval`. `workers > 1` обрабатывает события разных чатов параллельно в потоках, сохраняя порядок внутри чата; курсор подтверждается после завершения всех обработчиков пакета. При `workers > 1` обработчики вызываются параллельно из разных потоков: общее изменяемое состояние (словари, счётчики, соединения с БД) защищайте блокировками. Исключение обработчика прерывает цикл.
- `websocket_events(*, stop=None)` возвращает генератор событий; требуется дополнительная зависимость `[websocket]`. Если передан `threading.Event` `stop`, генератор закрывает соединение примерно через секунду после его установки. Автоматическое переподключение и восстановление пропусков в самом генераторе не реализованы.
- `Dispatcher(bot, *, self_user_id, username=None, auto_typing=True, typing_interval=4.0, typing_delay=0.5, read_mode="blocking")` регистрирует функции через `command(name)`, `on_message` и `on_event(event_type)`. `handle(event)` обрабатывает одно событие, `run_polling(since, **kwargs)` запускает цикл и передаёт `kwargs` в `poll`. По умолчанию (`read_mode="blocking"`) сначала отправляется отметка о прочтении входящего сообщения; при ошибке отметки обработчик не вызывается. `read_mode="background"` — опция: отметка ставится в очередь фонового потока до вызова обработчика, но ответ сервера не ожидается, а отметки одного чата объединяются до последнего сообщения. Оговорки: ошибка отметки только записывается в logging и не останавливает обработчик, поэтому бот может ответить, а сообщение останется непрочитанным; объединение отметок «до последнего сообщения чата» на живом ИТД не проверялось. `read_mode="off"` отключает отметки. `typing_delay` — сколько секунд обработчик может работать до показа `typing`; `0` отправляет его до запуска обработчика, как раньше. `close()` (или `with Dispatcher(...)`) дожидается отправки отметок из очереди; `run_polling` вызывает его сам.
- `CommandContext` предоставляет `bot`, `event`, `message`, `command`, `args`, `chat_id`, а также `reply(text, **kwargs)` и `reply_formatted(content, **kwargs)`.

Обработка пакетов и сохранение курсора не являются транзакцией с побочными эффектами: при сбое события могут быть повторены. Текущий typing действует только в зарегистрированных обработчиках Dispatcher.

## Форматирование

`Text(text="")` строит сообщение. Доступны `add(text, styles=(), url=None)`, `bold`, `italic`, `underline`, `strike`, `code`, `spoiler`, `link`. `build()` возвращает пару `(text, entities)`. `utf16_length(text)` считает длину в единицах UTF-16. Синтаксис Markdown не обрабатывается.

## Низкоуровневые запросы

`request(method, path, params=None, json=None)` отправляет запрос к `/api/v1/messenger`. `api_request(method, path, params=None, json=None, content=None, files=None, headers=None, raw=False)` обращается к путям `/api/` и `/v1/` настроенного сервера. Только один из `json`, `content`, `files` можно указать одновременно. `raw=True` возвращает успешный `httpx.Response`; перенаправления отключены. Вызывающий код отвечает за корректность экспериментальных запросов.
