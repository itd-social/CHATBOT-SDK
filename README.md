![ITD ChatBot SDK](.github/assets/banner.png)

<p align="center">
  <a href="https://pypi.org/project/itd-chatbot-sdk/"><img src="https://img.shields.io/pypi/pyversions/itd-chatbot-sdk" alt="Поддерживаемые версии Python"></a>
  <a href="https://pypi.org/project/itd-chatbot-sdk/"><img src="https://img.shields.io/pypi/v/itd-chatbot-sdk" alt="Версия пакета в PyPI"></a>
  <a href="https://github.com/itd-social/CHATBOT-SDK/blob/main/LICENSE"><img src="https://img.shields.io/github/license/itd-social/CHATBOT-SDK" alt="Лицензия проекта"></a>
</p>

<p align="center">
Python SDK для создания чат-ботов в мессенджере ИТД.
</p>

## Что умеет SDK

- **Команды и обработчики.** `/start`, `/echo`, обычные сообщения, фото, реакции, правки и удаления.
- **Файлы.** Фото, видео, музыка и любые документы до 2 ГиБ. Фото и видео можно сжимать.
- **Красивый текст.** Жирный, курсив, ссылки и спойлеры без Markdown.
- **Долгая работа без хлопот.** Токен обновляется сам, курсор событий сохраняется, лимиты запросов соблюдаются.

> SDK написан по клиентскому бандлу ИТД и работает от имени обычного аккаунта. Это неофициальный проект: права, лимиты и формат событий определяет сервер. Что мы проверили на живом ИТД, а что нет, честно сказано в [документации](docs/USAGE.md).

## Статус и гарантии

SDK находится в **Beta**. Он неофициальный и построен по клиентскому бандлу: API ИТД может измениться без предупреждения. Совместимость с сервером зависит от этих изменений; ограничения проверки указаны в документации.

- **Версии 1.x сохраняют совместимость публичного API SDK:** имена, пути импорта и формы результатов. Исправления ошибок могут менять ошибочное поведение; такие изменения описываем в документации и changelog.
- **Минорные релизы** добавляют возможности, включаемые явно, и исправления. Прежние настройки по умолчанию сохраняются, кроме явно описанных исправлений ошибок. Мелкие исправления и документацию по возможности объединяем в **патч-релизы**.
- **Устаревание** объявляем через `DeprecationWarning` с указанием замены. Предупреждение сохраняется минимум два минорных релиза после объявления; в 1.x устаревший API не удаляем.

## Установка

Нужен Python 3.10 или новее (поддерживаемые версии видно по значку выше).

```sh
python -m pip install itd-chatbot-sdk
```

Дополнительные возможности ставятся отдельно:

```sh
python -m pip install "itd-chatbot-sdk[websocket]"  # быстрые обновления
python -m pip install "itd-chatbot-sdk[images]"     # сжатие фото
```

## Быстрый старт

Для отправки сообщений аккаунту бота нужна подписка **«ИТД НУКСТА»**. Без неё SDK получает ошибку `APIError` с кодом `nuksta_required`: [как обработать](docs/USAGE.md#коды-ошибок-сервера).

Вам понадобится `refresh_token` аккаунта бота. [Как его получить?](docs/AUTH.md#как-получить-refresh_token)

```python
import os

from itd_bot import Bot, Dispatcher

with Bot.from_refresh_token(os.environ["ITD_REFRESH_TOKEN"]) as bot:
    router = Dispatcher.from_bot(bot)

    @router.command("start")
    def start(ctx):
        ctx.reply("Привет! Я бот на ITD ChatBot SDK.")

    router.run_polling(state_path=".bot-state.json")
```

Запустите файл и напишите боту `/start`. Бот запомнит, где остановился, в файле `.bot-state.json` (при первом запуске старые сообщения пропускаются).

> Файл сессии `.itd-session.json` содержит секрет в открытом виде. Не добавляйте его в Git. Подробности: [авторизация](docs/AUTH.md).

Разбор по шагам: [первый бот](docs/GETTING_STARTED.md).

## Документация

| Хочу... | Читать |
|---|---|
| Запустить первого бота | [Первый бот](docs/GETTING_STARTED.md) |
| Получить токен и не потерять сессию | [Авторизация](docs/AUTH.md) |
| Прочитать настройки из окружения или .env | [Настройки окружения](docs/ENVIRONMENT.md) |
| Отправлять сообщения, файлы, реакции | [Руководство](docs/USAGE.md) |
| Использовать фильтры, middleware и несколько обработчиков | [Router](docs/ROUTER.md) |
| Написать диалог из нескольких шагов | [Состояния FSM](docs/FSM.md) |
| Найти нужный метод | [Справочник API](docs/API_REFERENCE.md) |
| Понять, почему бот иногда ждёт | [Лимиты запросов](docs/RATE_LIMITS.md) |
| Посмотреть примеры | [Папка examples](examples/) |
| Узнать, что изменилось | [История изменений](CHANGELOG.md) |
| Опубликовать SDK в PyPI | [Публикация](docs/PUBLISHING.md) |
| Изучить маршруты ИТД | [Список маршрутов](docs/ROUTE_INVENTORY.md) |

## Примеры

Каждый пример лежит в своей папке с коротким README. Токены задаются переменными окружения, значений по умолчанию нет.

| Пример | О чём |
|---|---|
| [rate_limit_demo](examples/rate_limit_demo/README.md) | Корзины токенов и паузы после 429, без сети |
| [formatting_bot](examples/formatting_bot/README.md) | Форматирование через `Text` |
| [countdown_bot](examples/countdown_bot/README.md) | Отправка, правка и удаление своего сообщения |
| [reactions_bot](examples/reactions_bot/README.md) | Реакции и событие `reaction` |
| [media_saver](examples/media_saver/README.md) | Сохранение вложений |
| [forward_bot](examples/forward_bot/README.md) | Пересылка в служебный чат |
| [subscribers_only](examples/subscribers_only/README.md) | Команда только для подписчиков |
| [profile_lookup](examples/profile_lookup/README.md) | Чтение профиля |

Рядом лежат простые `examples/echo.py`, `commands.py` и `chat_actions.py`.

## Участие в разработке

Хотите помочь? Начните с [CONTRIBUTING.md](CONTRIBUTING.md). Быстрая проверка перед PR:

```sh
python -m pip install -e ".[dev]"
python -m ruff check itd_bot examples tests
python -m ruff format --check itd_bot examples tests
python -m pytest -q
python -m build
```

Тесты идут без настоящих токенов и без подключения к ИТД. Остальное:

- процесс и чеклист: [dev.md](dev.md);
- ветки и коммиты: [GIT_WORKFLOW.md](GIT_WORKFLOW.md);
- выпуск версий: [RELEASING.md](RELEASING.md);
- как писать документацию: [docs/WRITING_DOCS.md](docs/WRITING_DOCS.md).

## Лицензия

Проект распространяется по [Apache License 2.0](LICENSE).
