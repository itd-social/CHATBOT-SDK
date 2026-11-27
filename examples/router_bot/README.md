# Router с фильтрами

Задайте `ITD_TOKEN`, `ITD_REFRESH_TOKEN` или `ITD_SESSION_PATH` ([настройки](../../docs/ENVIRONMENT.md)).
Запустите `python examples/router_bot/router_bot.py` из корня репозитория.
Напишите `/start`, `/echo текст` или `помощь` с другого аккаунта.
Команды находятся во вложенном Router, текст выбирается фильтрами.
Правила порядка, middleware и повторов — в [руководстве](../../docs/ROUTER.md).
