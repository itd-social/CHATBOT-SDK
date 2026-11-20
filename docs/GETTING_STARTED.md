# Первый бот

В этом примере бот отвечает на `/start` и `/echo`, сохраняет курсор и автоматически обновляет access-токен. Нужны Python 3.10+, аккаунт бота и его `refresh_token`.

## 1. Установите SDK

Создайте папку для бота, затем создайте в ней окружение и установите пакет.

Windows PowerShell:

```powershell
mkdir my-itd-bot
cd my-itd-bot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install itd-chatbot-sdk
```

Linux/macOS:

```sh
mkdir my-itd-bot
cd my-itd-bot
python -m venv .venv
.venv/bin/python -m pip install itd-chatbot-sdk
```

## 2. Получите токен

Получите значение cookie `refresh_token`, как показано в [инструкции со скриншотом](AUTH.md#как-получить-refresh_token). Закройте окно инкогнито после копирования и не используйте ту же сессию одновременно в браузере и боте.

Задайте значение в терминале. В PowerShell:

```powershell
$env:ITD_REFRESH_TOKEN = "ЗНАЧЕНИЕ_COOKIE"
```

В командной строке Windows (`cmd`):

```cmd
set "ITD_REFRESH_TOKEN=ЗНАЧЕНИЕ_COOKIE"
```

В Linux/macOS:

```sh
export ITD_REFRESH_TOKEN="ЗНАЧЕНИЕ_COOKIE"
```

Не копируйте имя `refresh_token=` и пробелы. Не отправляйте значение в публичный репозиторий.

## 3. Создайте бота

Сохраните код в `bot.py` вне отслеживаемых файлов или убедитесь, что в нём нет секретов:

```python
import os
from pathlib import Path

from itd_bot import Bot, Dispatcher

cursor_file = Path(".bot-cursor")

with Bot.from_refresh_token(os.environ["ITD_REFRESH_TOKEN"]) as bot:
    profile = bot.api_request("GET", "/api/users/me")
    router = Dispatcher(bot, self_user_id=profile["id"])

    @router.command("start")
    def start(ctx):
        ctx.reply("Привет! Напишите /echo и текст.")

    @router.command("echo")
    def echo(ctx):
        ctx.reply(ctx.args or "Добавьте текст после команды")

    def save_cursor(value):
        temporary = cursor_file.with_name(cursor_file.name + ".tmp")
        temporary.write_text(str(value), encoding="utf-8")
        os.replace(temporary, cursor_file)

    if cursor_file.exists():
        cursor = int(cursor_file.read_text(encoding="utf-8"))
    else:
        cursor = bot.get_chats(limit=1)["event_seq"]

    router.run_polling(since=cursor, on_checkpoint=save_cursor)
```

При первом запуске берётся текущий `event_seq`: прежние события не обрабатываются. При следующих запусках используется сохранённый курсор. Добавьте `.bot-cursor` и `.bot-cursor.tmp` в `.gitignore` вашего проекта, если не хотите отслеживать локальное состояние.

## 4. Запустите и проверьте

В том же терминале:

```powershell
.\.venv\Scripts\python.exe bot.py
```

На Linux/macOS используйте `.venv/bin/python bot.py`. Напишите аккаунту бота `/start`, затем `/echo привет`. Остановите программу сочетанием `Ctrl+C`.

Доступен и готовый [тестовый бот](../examples/refresh_test_bot.py): он сам определяет ID аккаунта и показывает счётчик обновлений access-токена. Команда запуска и особенности часовой проверки описаны в [авторизации](AUTH.md#бот-для-проверки-на-час).

## Что учесть перед развёртыванием

- `.itd-session.json` содержит refresh-токен в открытом виде. Не публикуйте файл и настройте права доступа к каталогу.
- Один объект SDK синхронизирует свои потоки, но отдельные процессы не координируются. Запускайте одну копию на одну refresh-сессию.
- Курсор сохраняется после всего пакета. Если процесс завершится после ответа, но до сохранения, событие может прийти снова. Делайте побочные эффекты идемпотентными.
- `ResetRequired` означает, что сервер больше не может выдать полный журнал с выбранного курсора. Остановите обработку и восстановите состояние из чатов и истории; не пропускайте событие автоматически.
- Для минимальной задержки установите `itd-chatbot-sdk[websocket]` и запускайте `router.run_polling(since=cursor, on_checkpoint=save_cursor, websocket=True)`. На живом ИТД режим не проверялся: неизвестно, присылает ли сервер кадр на каждое новое сообщение. Если нет, поведение такое же, как у обычного опроса с интервалом `interval`. Если обработчики долгие (внешние API, нейросети), добавьте `workers=4`, чтобы медленный чат не задерживал остальные. При `workers > 1` обработчики вызываются параллельно из разных потоков: общее изменяемое состояние (словари, счётчики, соединения с БД) защищайте блокировками.
- Отметка о прочтении по умолчанию отправляется до обработчика, и при её ошибке обработчик не вызывается. `Dispatcher(..., read_mode="background")` убирает этот запрос из пути ответа, но ошибка отметки тогда только пишется в лог, а объединение отметок до последнего сообщения чата на живом ИТД не проверялось.
- `typing` отправляется, если зарегистрированный обработчик `Dispatcher` работает дольше 0,5 секунды (`typing_delay`). Если индикатор не виден, проверьте настройки видимости онлайн-статуса аккаунта.
- Сессия может истечь или быть отозвана. Ошибка авторизации требует новой cookie, а не бесконечных повторов.

Дальше: [сообщения и файлы](USAGE.md), [реакции и события](USAGE.md#автоматический-typing-и-реакции), [справочник методов](API_REFERENCE.md).
