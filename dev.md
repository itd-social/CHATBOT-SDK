# Разработка

Как подготовить окружение, проверить изменения и открыть PR.

## Подготовка

Нужны Git и Python 3.10+. Клонируйте [репозиторий](https://github.com/itd-social/CHATBOT-SDK) и из его корня выполните:

```sh
python -m venv .venv
```

Активируйте окружение:

| Оболочка | Команда |
|---|---|
| Linux/macOS | `source .venv/bin/activate` |
| PowerShell | `.venv\Scripts\Activate.ps1` |
| cmd | `.venv\Scripts\activate.bat` |

Затем установите зависимости:

```sh
python -m pip install -e ".[dev,release]"
```

Не получается активировать окружение? Вызывайте Python напрямую, например `.venv\Scripts\python.exe -m pytest -q`.

## Где что лежит

```text
itd_bot/          клиент, авторизация, диспетчер, форматирование
examples/         рабочие примеры ботов
tests/            локальные тесты с подменой транспорта
docs/             руководство, справочник и разбор веб-клиента
.github/          CI, релиз, Dependabot, картинки
pyproject.toml    зависимости, инструменты, версия
```

## Проверки перед PR

```sh
python -m ruff check itd_bot examples tests benchmarks tools
python -m ruff format --check itd_bot examples tests benchmarks tools
python -m pytest -q
python -m build
```

- Автоформатирование: `python -m ruff format itd_bot examples tests benchmarks tools`.
- Один тест: `python -m pytest tests/test_refresh_session.py -q`.
- Хуки Ruff перед коммитом: [CONTRIBUTING.md](CONTRIBUTING.md#pre-commit).

Тесты не ходят в ИТД. Для новой серверной операции используйте `httpx.MockTransport` и проверяйте URL, тело запроса, ошибки и состояние после сбоя, а не только удачный ответ.

Замеры запускаются отдельно: `python -m pytest -m perf -q -s`. Методика, сравнение с v1.3.1 и результаты — в [производительности](docs/PERFORMANCE.md).

## Чеклист изменения SDK

- [ ] Поведение подтверждено бандлом или проверкой, и понятно, где границы этой проверки.
- [ ] Публичная сигнатура описана, совместимость сохранена.
- [ ] Проверены 401, 429, таймаут, ротация cookie и повторная доставка (где это относится к изменению).
- [ ] Входящие сообщения отмечаются прочитанными до обработчика, курсор сохраняется после успешного пакета.
- [ ] Нет токенов, реальных ID пользователей и личных сообщений.
- [ ] Есть полезные тесты.
- [ ] Документация и пример обновлены по [правилам](docs/WRITING_DOCS.md).
- [ ] Ruff, pytest и сборка проходят локально и в CI.

## Дальше

- [Проверка новой сборки сайта и опись API](docs/API_COVERAGE.md)
- Ветки и коммиты: [GIT_WORKFLOW.md](GIT_WORKFLOW.md)
- Выпуск версии: [RELEASING.md](RELEASING.md)
