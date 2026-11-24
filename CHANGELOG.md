# Changelog

Все заметные изменения проекта собираются из коммитов в формате Conventional Commits.

<!-- version list -->

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
