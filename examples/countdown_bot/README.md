# countdown_bot
Команда `/countdown` отправляет сообщение, трижды правит его и удаляет.
Показывает `reply_message`, `SentMessage.edit` и `delete`; паузы между правками выдерживает SDK.
Переменные окружения: `ITD_TOKEN` (access-токен).

```sh
ITD_TOKEN=... python examples/countdown_bot/countdown_bot.py
```
