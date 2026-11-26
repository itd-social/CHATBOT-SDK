# forward_bot
Пересылает входящие сообщения в служебный чат (`ctx.forward_to`).
Переменные окружения: `ITD_TOKEN` (access-токен), `ITD_CHAT` (ID чата-получателя).
Свои ID: `bot.forward_messages(..., client_message_ids={"MESSAGE_ID": "CLIENT_ID"})`; прежний список той же длины преобразуется в словарь.
После сбоя сети пересылка не повторяется: результат неизвестен.

```sh
ITD_TOKEN=... ITD_CHAT=... python examples/forward_bot/forward_bot.py
```
