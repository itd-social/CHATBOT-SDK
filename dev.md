# Разработка ITD ChatBot SDK

## Подготовка окружения

Нужны Git и Python 3.10+. Клонируйте [репозиторий](https://github.com/itd-social/CHATBOT-SDK) и выполните из его корня:

```sh
python -m venv .venv
```

Активируйте окружение: в PowerShell — `.venv\Scripts\Activate.ps1`, в cmd — `.venv\Scripts\activate.bat`, в Linux/macOS — `source .venv/bin/activate`. Затем:

```sh
python -m pip install -e ".[dev,release]"
```

Если активация недоступна, вызывайте Python окружения напрямую, например `.venv\Scripts\python.exe -m pytest -q` в Windows.

## Структура проекта

```text
itd_bot/          HTTP-клиент, авторизация, диспетчер и форматирование
examples/         рабочие примеры ботов
examples/refresh_test_bot.py  длительная проверка сессии
tests/           локальные тесты с подменой транспорта
docs/            руководство, справочник и анализ веб-клиента
.github/          CI, релиз, Dependabot и изображения
pyproject.toml    зависимости, инструменты и версия пакета
```

## Проверки перед PR

```sh
python -m ruff check itd_bot examples tests
python -m ruff format --check itd_bot examples tests
python -m pytest -q
python -m build
```

Автоформатирование: `python -m ruff format itd_bot examples tests`. Для конкретного теста используйте `python -m pytest tests/test_refresh_session.py -q`. Тесты запускаются без подключения к ИТД. Для новой серверной операции используйте `httpx.MockTransport` и проверяйте URL, тело, ошибки и состояние после сбоя, а не только успешный ответ.

## Чеклист изменения SDK

- [ ] Поведение подтверждено бандлом или проверкой с указанием её границ.
- [ ] Публичная сигнатура и имена полей описаны; совместимость учтена.
- [ ] При необходимости проверены 401, 429, таймаут, ротация cookie и повторная доставка.
- [ ] Отметка прочтения ставится в очередь (или, в `read_mode="blocking"`, отправляется) до обработчика; курсор сохраняется после успешного пакета.
- [ ] Нет токенов, реальных идентификаторов пользователей и содержимого частных сообщений.
- [ ] Добавлены полезные тесты и обновлены документация с примером.
- [ ] Ruff, pytest и сборка проходят локально и в CI.

Про ветки и Conventional Commits см. [GIT_WORKFLOW.md](GIT_WORKFLOW.md), про версии — [RELEASING.md](RELEASING.md).
