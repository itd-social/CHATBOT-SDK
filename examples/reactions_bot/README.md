# reactions_bot
Команда `/like` ставит реакцию на сообщение; чужие реакции печатаются в консоль.
Набор доступных эмодзи определяет сервер (`bot.get_reaction_catalog()`).
Переменные окружения: `ITD_TOKEN` (access-токен).

```sh
ITD_TOKEN=... python examples/reactions_bot/reactions_bot.py
```
