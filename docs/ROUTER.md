# Фильтры и несколько обработчиков

Подключите `Router`, если нужны фильтры, middleware или несколько обработчиков одного события. Существующий `Dispatcher` работает как раньше.

```python
from itd_bot import Bot, CommandContext, MessageContext, Router, filters

commands = Router()

@commands.message(filters.command("start"))
def start(ctx: CommandContext) -> None:
    ctx.reply("Здравствуйте! Напишите /start текст")

with Bot.from_env() as bot:
    router = Router.from_bot(bot, match="first", **bot.dispatcher_options)
    router.include_router(commands)
    router.run_polling(state_path=".bot-state.json", **bot.poll_options)
```

Настройте [окружение](ENVIRONMENT.md) перед запуском. `from_bot` получает профиль аккаунта и исключает его собственные сообщения. Если профиль уже известен, используйте `Router(bot, self_user_id="SELF_ID", username="bot_name")`.

## Выберите сообщения

```python
@commands.message(filters.chat("CHAT_ID") & ~filters.user("USER_ID"))
def chat_message(ctx):
    ctx.reply("Сообщение получено")

@commands.message(filters.text("помощь") | filters.regex(r"^help\b"))
def help_message(ctx):
    ctx.reply("Напишите /start")

@commands.message(filters.command("echo", prefix="!"), lambda ctx: bool(ctx.args))
def echo(ctx):
    ctx.reply(ctx.args)

@commands.on_event("message_edited", filters.event_type("message_edited"))
def edited(ctx):
    message_id = ctx.message_id
```

`text` сравнивает весь текст, `regex` ищет совпадение. Свой синхронный callable получает контекст; оберните его в `filters.predicate`, чтобы соединять через `&`, `|`, `~`. Несколько аргументов декоратора означают И. Проверки прекращаются, как только известен результат.

`filters.user` проверяет `sender_id` сообщения или верхнее поле `user_id` другого события. Отсутствующее поле не совпадает. Исходные словари доступны через `ctx.event` и `ctx.message`, включая неизвестные поля.

Успешный `command` передаёт существующий `CommandContext` с `command` и `args` следующим условиям И и обработчику. ИЛИ сохраняет контекст первой успешной ветки; НЕ оставляет исходный контекст. Если объединяете команду с обычным текстом через ИЛИ, проверяйте тип контекста перед обращением к `args`.

Команды разбираются локально. `/start@name` совпадает только с известным именем аккаунта; серверные команды, кнопки и callback этим не создаются. Другие сообщения получают `MessageContext` с прежними `reply`, `react`, `forward_to`; другие события — `EventContext`.

## Задайте порядок

`message(*conditions, order=0)` и `on_event(type, *conditions, order=0)` допускают несколько обработчиков. Меньший `order` вызывается раньше во всём дереве. При равенстве действует порядок регистрации; вложенный роутер разворачивается в месте `include_router`.

- `match="first"` у корня вызывает первый подходящий обработчик.
- `match="all"` вызывает все подходящие обработчики по порядку.
- Возврат `Router.STOP` прекращает маршрутизацию текущего сообщения или события.

Для `new_messages` выбор и остановка выполняются отдельно для каждого сообщения. Для входящих сообщений используйте `message()`, для остальных событий — `on_event()`.

Включайте только непривязанные `Router()`. Повторное включение и циклы отклоняются. Завершите регистрацию всего дерева до первого `handle()` или `run_polling()`: после этого изменения вызывают `RuntimeError`.

## Оберните обработчик

```python
@commands.middleware
def access(next_handler, ctx):
    if isinstance(ctx, MessageContext) and ctx.sender_id == "BLOCKED_USER_ID":
        return Router.STOP
    try:
        return next_handler(ctx)
    finally:
        pass  # Освободите здесь ресурсы текущего вызова.
```

Middleware вызывается после фильтров для каждого выбранного обработчика. Корневые обёртки окружают вложенные; `ctx.event` содержит исходное событие. Вызовите `next_handler(ctx)` не больше одного раза. Без вызова текущий обработчик пропускается; в режиме `all` продолжаются следующие. `Router.STOP` из middleware или обработчика останавливает текущую маршрутизацию даже без возврата результата `next_handler` наружу.

Все фильтры, обработчики, middleware и `on_error` должны быть синхронными. Их общий пользовательский state защищайте самостоятельно, если включаете параллельную обработку.

## Ошибки, прочтение и повторы

Корень использует прежние настройки [прочтения и дедупликации](USAGE.md#защита-от-повторной-обработки), typing, [очередей по чатам](CHAT_QUEUES.md), курсора и остановки. Прочтение выполняется до фильтров и обработчиков, в том числе для неподходящих сообщений. В режиме `batch` сохраняется одна отметка на чат за пакет. Для всей выбранной цепочки используется один сеанс typing; дубли пропускаются до прочтения.

Сообщение считается обработанным после успешного завершения всей цепочки. Ошибки фильтров, middleware и обработчиков идут в прежнюю [политику `on_error`](USAGE.md#если-обработчик-упал); при прямом `handle()` исключение выходит вызывающему коду. `ResetRequired` продолжает выходить из polling.

> **Осторожно.** Явный `ErrorAction.RETRY` повторяет цепочку с начала. Уже выполненные ответы и другие действия могут повториться; отката нет. Автоматических повторов HTTP-записи Router не добавляет.

`run_polling` закрывает свою очередь прочтения при завершении. `close()` и контекстный менеджер Router также закрывают её; переданный `Bot` закрывайте отдельно, как в первом примере.

Маршрутизация проверена автономными тестами с `httpx.MockTransport`; новых проверок на живом ИТД для Router не выполнялось.

## Что дальше

- [Запускаемый пример](../examples/router_bot/README.md)
- [Сигнатуры Router и фильтров](API_REFERENCE.md#router)
- [Настройки окружения](ENVIRONMENT.md)
