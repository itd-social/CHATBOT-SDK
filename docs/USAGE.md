# Руководство

Здесь собраны все повседневные задачи: ответить на команду, отправить файл, поставить реакцию. В примерах `bot` — это открытый `Bot`, а `router` — `Dispatcher`, созданный так же, как в [первом боте](GETTING_STARTED.md).

- [Команды и обработчики](#команды-и-обработчики)
- [Сообщения](#сообщения)
- [Форматирование текста](#форматирование-текста)
- [Файлы](#файлы)
- [Реакции и typing](#реакции-и-typing)
- [Профили пользователей](#профили-пользователей)
- [События и надёжность](#события-и-надёжность)
- [Ошибки](#ошибки)
- [Примеры](#примеры)

## Что нужно знать сразу

- SDK — клиент пользовательского API ИТД, а не официальный Bot API. Бот действует от имени своего аккаунта.
- Методы возвращают обычные словари и списки JSON с исходными именами полей. Исключение — профили (`User`).
- Адрес по умолчанию: `https://xn--d1ah4a.com`. Другой задаётся через `Bot(token, base_url="https://...")`.
- Каждый запрос идёт через [защиту от лимитов](RATE_LIMITS.md), поэтому бот иногда немного ждёт. Это нормально.

## Команды и обработчики

```python
from itd_bot import Dispatcher

router = Dispatcher.from_bot(bot)

@router.command("start")
def start(ctx):
    ctx.reply("Привет! Напишите /help")

@router.command("echo")
def echo(ctx):
    ctx.reply(ctx.args or "Добавьте текст после команды")

@router.message()
def any_message(ctx):
    print(ctx.sender_id, ctx.chat_id, ctx.text)

@router.photo()
def on_photo(ctx):
    for photo in ctx.photo:
        print(photo.filename, photo.mime, photo.size)

router.run_polling(state_path=".bot-state.json")
```

`Dispatcher.from_bot(bot)` сам узнаёт ID бота, чтобы он не отвечал самому себе.

**Какой обработчик сработает.** Сначала команды, потом `photo()` (для сообщений с фото), потом `message()` для всего остального. Суффикс `@username` в команде учитывается, только если совпадает с именем бота.

**Что есть в контексте `ctx`:**

| Что | Зачем |
|---|---|
| `ctx.text`, `ctx.args`, `ctx.chat_id`, `ctx.sender_id` | Текст, аргументы команды, чат и отправитель |
| `ctx.attachments`, `ctx.photo` | Все вложения и только картинки (`attachment.download(path)` скачивает файл) |
| `ctx.reply(...)` | Ответить; возвращает словарь |
| `ctx.reply_message(...)` | Ответить и получить объект с `edit()` и `delete()` |
| `ctx.reply_photo / reply_video / reply_music / reply_file` | Ответить файлом |
| `ctx.react`, `ctx.remove_reaction`, `ctx.delete_message`, `ctx.forward_to` | Действия с сообщением |
| `ctx.get_sender()` | Профиль отправителя |
| `ctx.is_subscribed()` | `True`, `False` или `None`, если сервер не сказал |
| `ctx.message`, `ctx.event` | Исходные словари |

Для реакций, правок и удалений есть `@router.reaction()`, `@router.edited()` и `@router.deleted()`. В контексте у них `chat_id`, `message_id`, `reactions` и `event`; при массовом удалении ID лежат в `ctx.event["message_ids"]`.

> **Хорошо знать.** Команды живут только внутри SDK, на сервере ИТД они нигде не регистрируются. Старые `@router.on_message` (с аргументами `(event, message)`) и `on_event` тоже работают, но `on_message` нельзя использовать вместе с `message()`.

Готовый пример: [`examples/commands.py`](../examples/commands.py).

### Прочтение сообщений

Перед вызовом обработчика бот отмечает входящее сообщение прочитанным, и если отметка не удалась, обработчик не запускается. Это поведение можно изменить:

```python
Dispatcher(bot, self_user_id="...", read_mode="background")  # не ждать ответа сервера
Dispatcher(bot, self_user_id="...", read_mode="off")         # не отмечать вовсе
```

В режиме `background` ошибка отметки только попадёт в лог, а обработчик всё равно выполнится.

## Сообщения

```python
bot.send_message("CHAT_ID", "Привет!")
bot.send_message("CHAT_ID", "Ответ", reply_to_id="MESSAGE_ID")
bot.send_message_to_user("USER_ID", "Привет, личное сообщение")

bot.edit_message("CHAT_ID", "MESSAGE_ID", "Новый текст")
bot.delete_messages("CHAT_ID", ["MESSAGE_ID"], for_everyone=False)
bot.forward_messages("CHAT_ID", "SOURCE_CHAT_ID", ["MESSAGE_ID"])
```

Историю читают `get_messages` и `get_messages_around`, список чатов — `get_chats`. Полный перечень методов — в [справочнике](API_REFERENCE.md).

> **Хорошо знать.** Если запрос на отправку оборвался по сети, SDK его не повторяет: сообщение могло уйти, а могло и нет. Что делать дальше, решает ваш бот. Как сервер обрабатывает `client_message_id`, мы не знаем, так что гарантией от дублей это считать нельзя.

## Форматирование текста

`Text` собирает текст со стилями. Markdown и экранирование не нужны, а эмодзи считаются правильно.

```python
from itd_bot import Text

content = (Text("Привет 👋 ")
           .bold("жирный")
           .add(" и ")
           .italic("курсив")
           .add(". ")
           .link("Открыть сайт", "https://example.com"))

bot.send_formatted("CHAT_ID", content)
```

Доступны `bold`, `italic`, `underline`, `strike`, `code`, `spoiler` и `link`. Стили можно сочетать: `Text().add("текст", styles=("bold", "italic"))`. Внутри ответа в обработчике есть `ctx.reply_formatted(content)`.

Для ручной отправки: `text, entities = content.build()`, затем передайте оба значения в `send_message`.

Предел — 50 элементов форматирования в одном тексте. Работу стилей на сервере мы проверяли локальными тестами, а не живыми сообщениями.

## Файлы

### Отправка

```python
bot.send_file("CHAT_ID", "report.pdf", text="Отчёт")
bot.send_photo("CHAT_ID", "photo.png", caption="Фото")
bot.send_video("CHAT_ID", "video.mp4", caption="Видео")
bot.send_music("CHAT_ID", "track.mp3", caption="Музыка")  # есть и send_audio
```

Подойдёт любой тип файла, который примет сервер. Файл не должен быть пустым и больше 2 ГиБ. Данные уходят частями по 5 МиБ, так что память не страдает. Ход загрузки можно отслеживать через `on_progress(sent, total)`.

Если нужно загрузить файл заранее и отправить позже:

```python
result = bot.upload_file("report.pdf")
bot.send_message("CHAT_ID", "Отчёт", attachments=[result["fileId"]])
```

### Фото, видео и музыка

По умолчанию отправляются исходные байты. Хотите сжать? Установите расширение и добавьте `compress=True`:

```sh
python -m pip install "itd-chatbot-sdk[images]"
```

```python
bot.send_photo("CHAT_ID", "photo.png", caption="Фото", compress=True)
bot.send_video("CHAT_ID", "video.mov", compress=True)
```

- **Фото** превращается в JPEG: длинная сторона до 1920 пикселей, качество 70, прозрачность заменяется белым фоном. GIF не трогается.
- **Видео** перекодируется в MP4 (H.264 и AAC, CRF 28) через `ffmpeg`, он должен быть в `PATH`. Это может занять время, а размер иногда даже вырастает. Временный файл удаляется после отправки.

Миниатюры и thumbhash SDK не создаёт.

### Скачивание

```python
bot.download_file("CHAT_ID", "FILE_ID", destination="received.bin")
```

Файл записывается потоком и никогда не перезаписывает существующий. Без `destination` вернутся байты. Принимаются только ссылки вида `/v1/chat-files/{uuid}` на вашем сервере. В обработчике проще: `attachment.download("путь")`.

> **Безопасность.** Имя файла приходит от пользователя. Не используйте его как локальный путь без проверки.

### Эксперимент: своё имя и MIME

`send_file` и `upload_file` позволяют отдельно задать `filename` и `mime`:

```python
bot.send_file("CHAT_ID", "music.mp3", filename="photo.png", mime="video/mp4")
```

SDK отправит байты как есть, ничего не конвертируя. Сервер может отклонить такой файл, а приложения покажут его по-разному. Это эксперимент, на результат не рассчитывайте.

> **Если загрузка сорвалась,** на сервере может остаться недогруженный файл. Автоматическая очистка не подтверждена. Если пришла ошибка [`UploadQuotaError`](RATE_LIMITS.md#лимит-загрузок), сначала разберитесь с незавершёнными загрузками.

## Реакции и typing

### Реакции

```python
bot.add_reaction("CHAT_ID", "MESSAGE_ID", "👍")

@router.reaction()
def changed(ctx):
    print(ctx.chat_id, ctx.message_id, ctx.event.get("reactions"))
```

`add_reaction` добавляет эмодзи к уже выбранным. Если нужно заменить весь набор, используйте `set_reactions` (с `expected_version` и уникальным `operation_id`). При конфликте версий сервер может ответить 409, повтора нет.

События приходят и от собственных реакций бота, поэтому следите, чтобы обработчик не запускал сам себя по кругу.

### Typing

Диспетчер **сам** показывает «печатает…», если обработчик работает дольше 0,5 секунды, и обновляет статус каждые 4 секунды. Быстрые ответы идут без лишних запросов.

```python
Dispatcher(bot, self_user_id="...", auto_typing=False)  # выключить
Dispatcher(bot, self_user_id="...", typing_delay=0)     # показывать сразу
```

Для своих долгих задач есть контекстный менеджер:

```python
from itd_bot import ChatAction

with bot.chat_action(chat_id):  # typing, пока идёт блок
    answer = slow_work()

with bot.chat_action(chat_id, ChatAction.UPLOAD_VIDEO, progress=0.0) as action:
    for step in range(1, 11):
        do_part(step)
        action.progress = step / 10
```

Доступные статусы: `TYPING`, `RECORD_VOICE`, `RECORD_VIDEO`, `UPLOAD_PHOTO`, `UPLOAD_VIDEO`, `UPLOAD_VOICE`, `UPLOAD_DOCUMENT`. `progress` (число от 0 до 1) работает только для `upload_*`, иначе будет `ValueError`. Если фоновая отправка статуса не удалась, повторов нет, а ошибка сохранится в `action.error`.

> **Не видно typing?** Проверьте, не скрыт ли у аккаунта бота статус «онлайн»: из-за этого индикатор может не показываться.

Примеры: [`examples/chat_actions.py`](../examples/chat_actions.py), а в тестовом боте есть `/wait 15` и `/react`.

## Профили пользователей

```python
user = bot.get_user("@example")       # по ID или нику
me = bot.get_me()                      # профиль самого бота

@router.command("profile")
def profile(ctx):
    user = ctx.get_sender()
    ctx.reply(f"Привет, {user.display_name or user.username or 'друг'}!")
```

У `User` есть `id`, `username`, `display_name`, `avatar`, `banner`, `bio`, `is_verified`, `is_private`, `followers_count`, `following_count`, `posts_count`, `is_following`, `is_followed_by`, `can_message`, `online`, `last_seen` и другие поля. Исходный ответ сервера всегда доступен в `user.raw`.

Чтобы получить баннер или закреплённый пост:

```python
user.banner_url                       # адрес или None
user.download_banner("banner.png")    # скачать (не перезаписывает файл)

post = user.pinned_post               # отдельный запрос, может быть None
if post is not None:
    post.share("CHAT_ID", text="Закреплённый пост")
```

Подписки: `user.is_followed_by` показывает, подписан ли пользователь на бота, а `bot.get_follow_status([user.id])` — наоборот.

> **Хорошо знать.** Кэша нет: каждый вызов делает запрос. Необязательные поля могут быть `None`, и `None` не значит `False`. Доступность данных зависит от приватности пользователя.

## События и надёжность

Обычно `run_polling` делает всё нужное, но полезно понимать, как он устроен.

- Бот периодически спрашивает у сервера новые события (`get_updates`) и двигает **курсор**.
- Курсор сохраняется только после того, как обработан **весь** пакет событий. Поэтому при сбое события могут прийти повторно.
- Первый запуск берёт текущий курсор и пропускает прошлое. Если старые события важны, сначала сверьте историю.
- Курсор ИТД — не Telegram `update_id`, и гарантий «ровно один раз» нет.

```python
router.run_polling(state_path=".bot-state.json")               # курсор в файле
router.run_polling(since=cursor, on_checkpoint=save_cursor)    # или хранить самому
```

Курсор привязан к аккаунту и серверу. Не используйте один файл в нескольких процессах. Вызов без `since` и `state_path` вернёт `ValueError`, а `since` вместе с уже существующим файлом состояния тоже запрещён: выберите что-то одно.

### Быстрые обновления и параллельная работа

```python
router.run_polling(state_path=".bot-state.json", websocket=True, workers=4)
```

- **`websocket=True`** (нужен `itd-chatbot-sdk[websocket]`) использует WebSocket как сигнал «появилось новое»: бот реагирует быстрее, но события всё равно берутся по курсору. Если WebSocket замолчит, запасной опрос по `interval` продолжит работу. На живом ИТД режим не проверялся.
- **`workers=4`** обрабатывает разные чаты параллельно, порядок внутри чата сохраняется. Так долгие обработчики (внешние API, нейросети) не тормозят остальных. Общие данные (словари, счётчики, соединения с БД) тогда нужно защищать блокировками.

Вручную события можно читать через `get_updates` и `websocket_events()`. В этом случае typing и отметки прочтения вы делаете сами, а два источника лучше не смешивать без дедупликации.

## Ошибки

```python
from itd_bot import APIError, ResetRequired

try:
    router.run_polling(state_path=".bot-state.json")
except ResetRequired:
    print("Сервер потерял часть событий: сверьте состояние с чатами")
except APIError as error:
    print(f"ИТД вернул HTTP {error.status}, код {error.code}")
```

| Ошибка | Что значит и что делать |
|---|---|
| `APIError` | Ответ сервера с ошибкой: смотрите `status`, `code`, `errors`, `retry_after` |
| `RateLimitError` | Ответ 429: подождите `error.retry_after_seconds`, см. [лимиты](RATE_LIMITS.md) |
| `UploadQuotaError` | Закончился бюджет загрузок |
| `ResetRequired` | Сервер не может выдать все события с вашего курсора. Восстановите состояние и только потом выберите новый курсор. Просто сдвинуть курсор вперёд нельзя: часть событий потеряется |
| `ProtocolError` | Ответ сервера не похож на ожидаемый |
| Ошибки `httpx` | Сеть или таймаут. Если это была отправка, не повторяйте её вслепую |

Для долгой работы настройте наблюдение за процессом и перезапуск после разбора ошибки.

## Примеры

Каждый пример лежит в своей папке с README. Значений переменных окружения по умолчанию нет.

| Пример | О чём |
|---|---|
| [rate_limit_demo](../examples/rate_limit_demo/README.md) | Как работают корзины токенов и паузы после 429, без сети |
| [formatting_bot](../examples/formatting_bot/README.md) | Форматирование через `Text` |
| [countdown_bot](../examples/countdown_bot/README.md) | Отправка, правка и удаление своего сообщения |
| [reactions_bot](../examples/reactions_bot/README.md) | Реакции и событие `reaction` |
| [media_saver](../examples/media_saver/README.md) | Сохранение вложений |
| [forward_bot](../examples/forward_bot/README.md) | Пересылка в служебный чат |
| [subscribers_only](../examples/subscribers_only/README.md) | Команда только для подписчиков |
| [profile_lookup](../examples/profile_lookup/README.md) | Чтение профиля |

Ещё есть простые `examples/echo.py`, `commands.py` и `chat_actions.py`. Примеры проверяются в `tests/test_examples.py` без сети и токена, живой сервер ими не проверялся.

### Тестовый бот

[`examples/test_bot.py`](../examples/test_bot.py) — интерактивный бот для тестового аккаунта. Он читает все сообщения, но выполняет команды только от пользователя `ITD_TESTER_ID`. Команда `/help` покажет всё, что он умеет, в том числе `/file`, `/download FILE_ID` и `/api METHOD /api/путь [JSON]`.

> **Осторожно.** `/api` может изменять данные от имени бота. Давайте доступ только доверенному тестировщику и используйте тестовый аккаунт.

```sh
export ITD_TOKEN="ТОКЕН_БЕЗ_BEARER"
export ITD_SELF_USER_ID="ID_АККАУНТА_БОТА"
export ITD_TESTER_ID="ID_ТЕСТИРОВЩИКА"
python examples/test_bot.py
```

Нужные ID видны в `get_chats()` (`other_user_id` для личного чата). Курсор бот хранит в `.itd-test-bot-cursor`; для другого аккаунта задайте свой `ITD_CURSOR_FILE`. Короткий токен истекает, при 401 нужен новый.

## Проверка SDK

```sh
python -m pip install -e ".[test]"
python -m pytest
```

Автотесты используют локальный транспорт. Вручную на тестовом аккаунте проверены текстовые сообщения, загрузка одного файла, обновления токена и WebSocket ping/pong. Остальное, включая вложения и разные форматы, ещё ждёт проверки на живом сервере.
