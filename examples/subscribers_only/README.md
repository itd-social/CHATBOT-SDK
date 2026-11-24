# subscribers_only
Команда `/secret` отвечает только подписчикам аккаунта бота (`ctx.is_subscribed()`).
Если сервер не вернул поле подписки, бот сообщает, что статус неизвестен.
Переменные окружения: `ITD_TOKEN` (access-токен).

```sh
ITD_TOKEN=... python examples/subscribers_only/subscribers_only.py
```
