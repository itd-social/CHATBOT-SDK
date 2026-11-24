# forward_bot
Пересылает входящие сообщения в служебный чат (`ctx.forward_to`).
Переменные окружения: `ITD_TOKEN` (access-токен), `ITD_CHAT` (ID чата-получателя).
Пересылка не повторяется автоматически: после сбоя сети результат неизвестен.

```sh
ITD_TOKEN=... ITD_CHAT=... python examples/forward_bot/forward_bot.py
```
