# Changelog

Все заметные изменения проекта собираются из коммитов в формате Conventional Commits.

## Unreleased

### Features

- **rate-limits**: корзины токенов отправки, пересылки и реакций ведутся по паре (аккаунт, чат), отправка пользователю и правки с удалением для себя имеют свои корзины; `reaction_burst=12`; предохранитель IP 80 запросов за 60 секунд остаётся общим.
- **actions**: `ChatAction` и `Bot.chat_action()` для повторяющихся статусов действий с обновляемым `progress`; `Bot.send_action()` принимает `ChatAction` и проверяет `progress` до запроса.

### Breaking changes

- Собственным подклассам `RateLimiter` нужно принять `account=` в `acquire()` и `observe()`: `Bot` теперь всегда передаёт ключ аккаунта.

<!-- version list -->

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
