# Changelog

Все заметные изменения проекта собираются из коммитов в формате Conventional Commits.

<!-- version list -->

## [1.4.0](https://github.com/itd-social/CHATBOT-SDK/compare/v1.3.1...v1.4.0) (2026-11-27)

### ✨ Новые возможности

* **protocol:** Добавлен толерантный разбор обновлений ([#56](https://github.com/itd-social/CHATBOT-SDK/pull/56), [201fa56](https://github.com/itd-social/CHATBOT-SDK/commit/201fa56afd4031919f2e54cc2d4c6ebb9666b4b6))
* **config:** Добавлено чтение настроек из окружения ([#56](https://github.com/itd-social/CHATBOT-SDK/pull/56), [201fa56](https://github.com/itd-social/CHATBOT-SDK/commit/201fa56afd4031919f2e54cc2d4c6ebb9666b4b6))
* **messages:** Добавлены UUIDv7 и обработка replayed ([#56](https://github.com/itd-social/CHATBOT-SDK/pull/56), [201fa56](https://github.com/itd-social/CHATBOT-SDK/commit/201fa56afd4031919f2e54cc2d4c6ebb9666b4b6))
* **errors:** Добавлены исключения для ошибок доступа к сообщениям ([#56](https://github.com/itd-social/CHATBOT-SDK/pull/56), [201fa56](https://github.com/itd-social/CHATBOT-SDK/commit/201fa56afd4031919f2e54cc2d4c6ebb9666b4b6))
* **errors:** Добавлены исключения для ошибок доступа к сообщениям ([#47](https://github.com/itd-social/CHATBOT-SDK/pull/47), [324fa55](https://github.com/itd-social/CHATBOT-SDK/commit/324fa55c949251f76d56fdb0bb25409347c2d938))
* **polling:** Добавлены очереди по чатам и сохранение непрерывного курсора ([#56](https://github.com/itd-social/CHATBOT-SDK/pull/56), [201fa56](https://github.com/itd-social/CHATBOT-SDK/commit/201fa56afd4031919f2e54cc2d4c6ebb9666b4b6))

### 🐛 Исправления

* **dispatcher:** Добавлена защита от повторной обработки сообщений ([#56](https://github.com/itd-social/CHATBOT-SDK/pull/56), [201fa56](https://github.com/itd-social/CHATBOT-SDK/commit/201fa56afd4031919f2e54cc2d4c6ebb9666b4b6))
* **websocket:** Добавлено обновление сессии при закрытии 4001 и 4003 ([#56](https://github.com/itd-social/CHATBOT-SDK/pull/56), [201fa56](https://github.com/itd-social/CHATBOT-SDK/commit/201fa56afd4031919f2e54cc2d4c6ebb9666b4b6))
* **release:** Принятые изменения v1.4.0 перенесены в main ([#56](https://github.com/itd-social/CHATBOT-SDK/pull/56), [201fa56](https://github.com/itd-social/CHATBOT-SDK/commit/201fa56afd4031919f2e54cc2d4c6ebb9666b4b6))

### ⚡️ Производительность

* **dispatcher:** Добавлены пакетные отметки прочтения ([#56](https://github.com/itd-social/CHATBOT-SDK/pull/56), [201fa56](https://github.com/itd-social/CHATBOT-SDK/commit/201fa56afd4031919f2e54cc2d4c6ebb9666b4b6))

### ✅ Тесты

* **perf:** Добавлен воспроизводимый стенд производительности ([#56](https://github.com/itd-social/CHATBOT-SDK/pull/56), [201fa56](https://github.com/itd-social/CHATBOT-SDK/commit/201fa56afd4031919f2e54cc2d4c6ebb9666b4b6))


## [1.3.1](https://github.com/itd-social/CHATBOT-SDK/compare/v1.3.0...v1.3.1) (2026-11-26)

### 🐛 Исправления

* **messages:** Исправлена форма клиентских ID при пересылке ([#45](https://github.com/itd-social/CHATBOT-SDK/pull/45), [48220a8](https://github.com/itd-social/CHATBOT-SDK/commit/48220a8498c6d5b1bc2bd332893e5705b1bfb4ce))
* **messages:** Преобразованы списки ID пересылки в объект ([#45](https://github.com/itd-social/CHATBOT-SDK/pull/45), [48220a8](https://github.com/itd-social/CHATBOT-SDK/commit/48220a8498c6d5b1bc2bd332893e5705b1bfb4ce))
* **messages:** Разрешён словарь клиентских ID при пересылке ([#45](https://github.com/itd-social/CHATBOT-SDK/pull/45), [48220a8](https://github.com/itd-social/CHATBOT-SDK/commit/48220a8498c6d5b1bc2bd332893e5705b1bfb4ce))

### 📚 Документация

* **polling:** Исправлено описание долгих обработчиков ([#43](https://github.com/itd-social/CHATBOT-SDK/pull/43), [fbb333d](https://github.com/itd-social/CHATBOT-SDK/commit/fbb333d1367ff9ba79cb99e367965dab3d3159fc))
* **messenger:** Описаны подписка, ошибки и ограничения ([#44](https://github.com/itd-social/CHATBOT-SDK/pull/44), [b434448](https://github.com/itd-social/CHATBOT-SDK/commit/b43444860b7c65d7f1e4feefa90f87ec3bc7f1d7))

### 🛠 Прочее

* **project:** Указаны Beta и политика совместимости ([#46](https://github.com/itd-social/CHATBOT-SDK/pull/46), [5e07e74](https://github.com/itd-social/CHATBOT-SDK/commit/5e07e747bcd0e2483af276b06218099e0fb4b309))


## [1.3.0](https://github.com/itd-social/CHATBOT-SDK/compare/v1.2.0...v1.3.0) (2026-11-26)

### ✨ Новые возможности

* **polling:** Добавлены on_error, повторы при сбоях сети и мягкая остановка ([#13](https://github.com/itd-social/CHATBOT-SDK/pull/13), [4dd20a3](https://github.com/itd-social/CHATBOT-SDK/commit/4dd20a39eaaed5cf20137ddc5a647260781150c8))
* **types:** Добавлены py.typed и проверка mypy в CI ([#15](https://github.com/itd-social/CHATBOT-SDK/pull/15), [1c5f53d](https://github.com/itd-social/CHATBOT-SDK/commit/1c5f53df231ad5cdf2a8d37982dcbccc6119c42a))
* **types:** Добавлены описания ответов, send_formatted_to_user и предупреждение об устаревшем on_message ([#16](https://github.com/itd-social/CHATBOT-SDK/pull/16), [300c9fc](https://github.com/itd-social/CHATBOT-SDK/commit/300c9fcbb2755c55a9c8d39f689b8775292ae6a8))

### 📚 Документация

* **license:** switch to Apache License 2.0 ([#11](https://github.com/itd-social/CHATBOT-SDK/pull/11), [623e5ef](https://github.com/itd-social/CHATBOT-SDK/commit/623e5ef3a40a06835a0c8f1c37f3db634297942d))
* **release:** Новое описание разделов ченжлога и соответствие типов коммитов ([#10](https://github.com/itd-social/CHATBOT-SDK/pull/10), [b5d9c3d](https://github.com/itd-social/CHATBOT-SDK/commit/b5d9c3de39e1e808111530228daeddfaf6128bf3))
* **changelog:** Новый вид ченжлога ([#10](https://github.com/itd-social/CHATBOT-SDK/pull/10), [b5d9c3d](https://github.com/itd-social/CHATBOT-SDK/commit/b5d9c3de39e1e808111530228daeddfaf6128bf3))
* Полностью переписана документация ([#12](https://github.com/itd-social/CHATBOT-SDK/pull/12), [365d282](https://github.com/itd-social/CHATBOT-SDK/commit/365d2821c9af7d148d86f2b5fb8fefe82e256f86))
* Удалены комментарии в коде ([#10](https://github.com/itd-social/CHATBOT-SDK/pull/10), [b5d9c3d](https://github.com/itd-social/CHATBOT-SDK/commit/b5d9c3de39e1e808111530228daeddfaf6128bf3))

### ✅ Тесты

* Добавлены тесты на ветки ошибок и непокрытые методы ([#14](https://github.com/itd-social/CHATBOT-SDK/pull/14), [db204ab](https://github.com/itd-social/CHATBOT-SDK/commit/db204ab7fafbed4b8138facf3f33397bd20a98e3))

### 📦 Сборка

* **release:** Переработанный ченжлог с красивым оформлением ([#10](https://github.com/itd-social/CHATBOT-SDK/pull/10), [b5d9c3d](https://github.com/itd-social/CHATBOT-SDK/commit/b5d9c3de39e1e808111530228daeddfaf6128bf3))


## v1.2.0 (2026-11-24)

### Bug Fixes

- **rate-limits**: Снизил reaction_burst по умолчанию до 12
  ([#8](https://github.com/itd-social/CHATBOT-SDK/pull/8),
  [`9445642`](https://github.com/itd-social/CHATBOT-SDK/commit/944564278e592b66d4b82f70f5ebd521618991af))

- **rate-limits**: Убрана блокировка переписки при квоте новых собеседников
  ([#8](https://github.com/itd-social/CHATBOT-SDK/pull/8),
  [`9445642`](https://github.com/itd-social/CHATBOT-SDK/commit/944564278e592b66d4b82f70f5ebd521618991af))

### Build System

- **lint**: Закрепить ruff точной версией ([#9](https://github.com/itd-social/CHATBOT-SDK/pull/9),
  [`41a6767`](https://github.com/itd-social/CHATBOT-SDK/commit/41a67670324eb076ef73c5b2d8ffd072da01db58))

- **lint**: Расширить правила ruff (E4, E7, E9, F, I, UP, B)
  ([#9](https://github.com/itd-social/CHATBOT-SDK/pull/9),
  [`41a6767`](https://github.com/itd-social/CHATBOT-SDK/commit/41a67670324eb076ef73c5b2d8ffd072da01db58))

### Chores

- Добавить конфигурацию pre-commit с ruff и раздел в CONTRIBUTING
  ([#9](https://github.com/itd-social/CHATBOT-SDK/pull/9),
  [`41a6767`](https://github.com/itd-social/CHATBOT-SDK/commit/41a67670324eb076ef73c5b2d8ffd072da01db58))

- Линтер, pre-commit и примеры ([#9](https://github.com/itd-social/CHATBOT-SDK/pull/9),
  [`41a6767`](https://github.com/itd-social/CHATBOT-SDK/commit/41a67670324eb076ef73c5b2d8ffd072da01db58))

- **changelog**: Добавлен раздел Unreleased ([#8](https://github.com/itd-social/CHATBOT-SDK/pull/8),
  [`9445642`](https://github.com/itd-social/CHATBOT-SDK/commit/944564278e592b66d4b82f70f5ebd521618991af))

### Code Style

- Применить автоисправления ruff (I, UP, B) ([#9](https://github.com/itd-social/CHATBOT-SDK/pull/9),
  [`41a6767`](https://github.com/itd-social/CHATBOT-SDK/commit/41a67670324eb076ef73c5b2d8ffd072da01db58))

### Continuous Integration

- Вынести ruff check и format --check в отдельный job lint
  ([#9](https://github.com/itd-social/CHATBOT-SDK/pull/9),
  [`41a6767`](https://github.com/itd-social/CHATBOT-SDK/commit/41a67670324eb076ef73c5b2d8ffd072da01db58))

### Documentation

- Dynamic package badges ([#6](https://github.com/itd-social/CHATBOT-SDK/pull/6),
  [`504c880`](https://github.com/itd-social/CHATBOT-SDK/commit/504c880b5c69b8f94d27459e39df1a36080dcc09))

- **changelog**: Убрать раздел Unreleased перед релизом
  ([#9](https://github.com/itd-social/CHATBOT-SDK/pull/9),
  [`41a6767`](https://github.com/itd-social/CHATBOT-SDK/commit/41a67670324eb076ef73c5b2d8ffd072da01db58))

- **examples**: Добавить восемь примеров по темам и ссылки на них в README и USAGE
  ([#9](https://github.com/itd-social/CHATBOT-SDK/pull/9),
  [`41a6767`](https://github.com/itd-social/CHATBOT-SDK/commit/41a67670324eb076ef73c5b2d8ffd072da01db58))

- **rate-limits**: Область корзин правок и удаления для себя — (аккаунт, чат)
  ([#8](https://github.com/itd-social/CHATBOT-SDK/pull/8),
  [`9445642`](https://github.com/itd-social/CHATBOT-SDK/commit/944564278e592b66d4b82f70f5ebd521618991af))

- **rate-limits**: Описал область корзин и идентификатор аккаунта
  ([#8](https://github.com/itd-social/CHATBOT-SDK/pull/8),
  [`9445642`](https://github.com/itd-social/CHATBOT-SDK/commit/944564278e592b66d4b82f70f5ebd521618991af))

### Features

- Proactive rate limits ([#8](https://github.com/itd-social/CHATBOT-SDK/pull/8),
  [`9445642`](https://github.com/itd-social/CHATBOT-SDK/commit/944564278e592b66d4b82f70f5ebd521618991af))

- **actions**: Добавил ChatAction и контекстный менеджер chat_action
  ([#8](https://github.com/itd-social/CHATBOT-SDK/pull/8),
  [`9445642`](https://github.com/itd-social/CHATBOT-SDK/commit/944564278e592b66d4b82f70f5ebd521618991af))

- **rate-limits**: Token bucket для групп сообщений
  ([#8](https://github.com/itd-social/CHATBOT-SDK/pull/8),
  [`9445642`](https://github.com/itd-social/CHATBOT-SDK/commit/944564278e592b66d4b82f70f5ebd521618991af))

- **rate-limits**: Вести корзины правок и удаления для себя по (аккаунт, чат)
  ([#8](https://github.com/itd-social/CHATBOT-SDK/pull/8),
  [`9445642`](https://github.com/itd-social/CHATBOT-SDK/commit/944564278e592b66d4b82f70f5ebd521618991af))

- **rate-limits**: Добавлена защита запросов и проверенные правила сообщений
  ([#8](https://github.com/itd-social/CHATBOT-SDK/pull/8),
  [`9445642`](https://github.com/itd-social/CHATBOT-SDK/commit/944564278e592b66d4b82f70f5ebd521618991af))

- **rate-limits**: Разделил корзины отправки по аккаунту и чату
  ([#8](https://github.com/itd-social/CHATBOT-SDK/pull/8),
  [`9445642`](https://github.com/itd-social/CHATBOT-SDK/commit/944564278e592b66d4b82f70f5ebd521618991af))

### Performance Improvements

- **client**: Добавлено кэширование ключа аккаунта для последней строки токена
  ([#8](https://github.com/itd-social/CHATBOT-SDK/pull/8),
  [`9445642`](https://github.com/itd-social/CHATBOT-SDK/commit/944564278e592b66d4b82f70f5ebd521618991af))

### Testing

- **examples**: Проверить компиляцию, импорт без сети и токена и офлайн-демо лимитов
  ([#9](https://github.com/itd-social/CHATBOT-SDK/pull/9),
  [`41a6767`](https://github.com/itd-social/CHATBOT-SDK/commit/41a67670324eb076ef73c5b2d8ffd072da01db58))


## v1.1.0 (2026-11-21)

### Features

- **sdk**: Simplify bot development ([#5](https://github.com/itd-social/CHATBOT-SDK/pull/5),
  [`d7dabce`](https://github.com/itd-social/CHATBOT-SDK/commit/d7dabce68330e538fbb3f7c172402351bc388ba2))


## v1.0.1 (2026-11-20)

### Bug Fixes

- **dispatcher**: Вернуть блокирующую отметку прочтения по умолчанию
  ([#4](https://github.com/itd-social/CHATBOT-SDK/pull/4),
  [`c92632c`](https://github.com/itd-social/CHATBOT-SDK/commit/c92632c9a946d1d504fb8962f00a910068324c64))

- **poll**: Не опрашивать без пауз, если курсор не сдвинулся
  ([#4](https://github.com/itd-social/CHATBOT-SDK/pull/4),
  [`c92632c`](https://github.com/itd-social/CHATBOT-SDK/commit/c92632c9a946d1d504fb8962f00a910068324c64))

### Documentation

- Описать фоновую отметку как опцию и ограничения WebSocket
  ([#4](https://github.com/itd-social/CHATBOT-SDK/pull/4),
  [`c92632c`](https://github.com/itd-social/CHATBOT-SDK/commit/c92632c9a946d1d504fb8962f00a910068324c64))

### Performance Improvements

- Снижение задержки бота ([#4](https://github.com/itd-social/CHATBOT-SDK/pull/4),
  [`c92632c`](https://github.com/itd-social/CHATBOT-SDK/commit/c92632c9a946d1d504fb8962f00a910068324c64))

- Снизить задержку ответа бота ([#4](https://github.com/itd-social/CHATBOT-SDK/pull/4),
  [`c92632c`](https://github.com/itd-social/CHATBOT-SDK/commit/c92632c9a946d1d504fb8962f00a910068324c64))

- **client**: Держать соединение открытым до минуты
  ([#4](https://github.com/itd-social/CHATBOT-SDK/pull/4),
  [`c92632c`](https://github.com/itd-social/CHATBOT-SDK/commit/c92632c9a946d1d504fb8962f00a910068324c64))


## v1.0.0 (2026-11-20)

- Initial Release
