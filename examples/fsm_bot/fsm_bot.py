"""Анкета из трёх шагов без ожидания следующего сообщения в обработчике."""

import os

from itd_bot import (
    FSM,
    Bot,
    CommandContext,
    ErrorAction,
    FileFSMStore,
    MessageContext,
    Router,
    UpdateEvent,
    filters,
)


def configure(bot: Bot, fsm: FSM, **options) -> Router:
    router = Router.from_bot(bot, match="first", **options)

    @router.on_error()
    def failed(exc: Exception, event: UpdateEvent) -> ErrorAction:
        return ErrorAction.STOP

    @router.message(filters.command("start"))
    def start(ctx: CommandContext) -> None:
        ctx.reply("Как вас зовут?")
        fsm.set(ctx, "name")

    @router.message(filters.command("cancel"))
    def cancel(ctx: CommandContext) -> None:
        fsm.clear(ctx)
        ctx.reply("Анкета отменена. Начать заново: /start")

    @router.message(filters.state(fsm, "name"), lambda ctx: bool(ctx.text))
    def name(ctx: MessageContext) -> None:
        ctx.reply("Сколько вам лет?")
        fsm.set(ctx, "age", data={"name": ctx.text})

    @router.message(filters.state(fsm, "age"), lambda ctx: bool(ctx.text))
    def age(ctx: MessageContext) -> None:
        if not ctx.text or not ctx.text.isascii() or not ctx.text.isdigit():
            ctx.reply("Напишите возраст цифрами")
            return
        data = fsm.get(ctx)["data"]
        ctx.reply("Подтвердить анкету? Напишите да")
        fsm.set(ctx, "confirm", data={**data, "age": ctx.text})

    @router.message(filters.state(fsm, "confirm") & filters.text("да"))
    def confirm(ctx: MessageContext) -> None:
        ctx.reply("Готово, анкета завершена")
        fsm.clear(ctx)

    @router.message(filters.state(fsm, "confirm"))
    def remind(ctx: MessageContext) -> None:
        ctx.reply("Напишите да или /cancel")

    return router


def main() -> None:
    if not any(
        os.environ.get(name)
        for name in ("ITD_TOKEN", "ITD_REFRESH_TOKEN", "ITD_SESSION_PATH")
    ):
        raise SystemExit("Задайте ITD_TOKEN, ITD_REFRESH_TOKEN или ITD_SESSION_PATH")
    fsm = FSM(FileFSMStore(".fsm-state.json"), namespace="questionnaire-bot")
    with Bot.from_env() as bot:
        router = configure(bot, fsm, **bot.dispatcher_options)
        polling = {**bot.poll_options, "chat_queues": True}
        router.run_polling(state_path=".bot-state.json", **polling)


if __name__ == "__main__":
    main()
