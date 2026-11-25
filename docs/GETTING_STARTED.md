# Первый бот

За пять минут соберём бота, который отвечает на `/start` и `/echo`. Вам понадобятся Python 3.10+, аккаунт бота и его `refresh_token`.

## 1. Установите SDK

Создайте папку и виртуальное окружение:

```sh
mkdir my-itd-bot
cd my-itd-bot
python -m venv .venv
```

Затем установите пакет.

Linux/macOS:

```sh
.venv/bin/python -m pip install itd-chatbot-sdk
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install itd-chatbot-sdk
```

## 2. Получите токен

Возьмите cookie `refresh_token` по [короткой инструкции](AUTH.md#как-получить-refresh_token) и передайте боту через переменную окружения.

Linux/macOS:

```sh
export ITD_REFRESH_TOKEN="ЗНАЧЕНИЕ_COOKIE"
```

PowerShell:

```powershell
$env:ITD_REFRESH_TOKEN = "ЗНАЧЕНИЕ_COOKIE"
```

cmd:

```cmd
set "ITD_REFRESH_TOKEN=ЗНАЧЕНИЕ_COOKIE"
```

Вставляйте только значение, без `refresh_token=` и пробелов. И не публикуйте его: это пароль от аккаунта бота.

## 3. Напишите бота

Сохраните код в `bot.py`:

```python
import os

from itd_bot import Bot, Dispatcher

with Bot.from_refresh_token(os.environ["ITD_REFRESH_TOKEN"]) as bot:
    router = Dispatcher.from_bot(bot)

    @router.command("start")
    def start(ctx):
        ctx.reply("Привет! Напишите /echo и текст.")

    @router.command("echo")
    def echo(ctx):
        ctx.reply(ctx.args or "Добавьте текст после команды")

    router.run_polling(state_path=".bot-state.json")
```

Бот запомнит, какие события уже обработал, в файле `.bot-state.json`. При первом запуске старые сообщения пропускаются, при следующих бот продолжит с того места, где остановился. Добавьте этот файл в `.gitignore`.

## 4. Запустите

```sh
.venv/bin/python bot.py
```

На Windows: `.\.venv\Scripts\python.exe bot.py`.

Напишите аккаунту бота `/start`, потом `/echo привет`. Остановить бота можно через `Ctrl+C`.

Хотите готовый вариант? В [`examples/refresh_test_bot.py`](../examples/refresh_test_bot.py) есть тестовый бот, который показывает, сколько раз обновился токен.

## Перед запуском на сервере

Несколько вещей, которые лучше знать заранее:

- **Одна сессия, один бот.** Не запускайте две копии на одной refresh-сессии и не открывайте её параллельно в браузере: процессы друг о друге не знают, и сессия может сломаться.
- **События могут прийти дважды.** Если бот упал после ответа, но до сохранения курсора, событие обработается повторно. Старайтесь, чтобы обработчики это переживали.
- **`ResetRequired`.** Так сервер сообщает, что не может отдать все пропущенные события. Остановитесь и сверьте состояние с чатами: SDK ничего не пропустит за вас.
- **Файл сессии.** `.itd-session.json` хранит токен в открытом виде. Не публикуйте его и закройте доступ к папке.
- **Если сессия истекла**, боту нужна новая cookie. Бесконечные повторы не помогут.

Хочется быстрее или тяжелее? В [руководстве](USAGE.md#быстрые-обновления-и-параллельная-работа) рассказано про WebSocket и `workers`.

## Что дальше

- [Сообщения, файлы и реакции](USAGE.md)
- [Справочник методов](API_REFERENCE.md)
