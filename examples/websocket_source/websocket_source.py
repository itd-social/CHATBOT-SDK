"""Приём кадров WS с HTTP-восстановлением, без вывода личных данных."""

import os

from itd_bot import Bot, ErrorAction, EventContext, MessageContext, Router, UpdateEvent


def main() -> None:
    if not any(
        os.environ.get(name)
        for name in ("ITD_TOKEN", "ITD_REFRESH_TOKEN", "ITD_SESSION_PATH")
    ):
        raise SystemExit("Задайте ITD_TOKEN, ITD_REFRESH_TOKEN или ITD_SESSION_PATH")
    with Bot.from_env() as bot:
        dispatcher = {**bot.dispatcher_options, "auto_typing": False}
        router = Router.from_bot(bot, **dispatcher)

        @router.message()
        def message(ctx: MessageContext) -> None:
            print("Получено входящее сообщение")

        @router.on_event("typing")
        def typing(ctx: EventContext) -> None:
            print("Получено событие typing")

        @router.on_event("presence")
        def presence(ctx: EventContext) -> None:
            print("Получено событие presence")

        @router.on_error()
        def error(exc: Exception, event: UpdateEvent) -> ErrorAction:
            return ErrorAction.STOP

        options = {**bot.poll_options, "source": "websocket", "chat_queues": True}
        router.run_polling(state_path=".bot-state.json", **options)


if __name__ == "__main__":
    main()
