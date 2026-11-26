"""Долгий обработчик одного чата не задерживает свободные чаты."""

import os
import time

from itd_bot import Bot, Dispatcher


def main() -> None:
    with Bot(os.environ["ITD_TOKEN"]) as bot:
        router = Dispatcher.from_bot(bot)

        @router.command("slow")
        def slow(ctx):
            time.sleep(5)  # Вместо обращения к внешнему сервису.
            ctx.reply("Долгая задача завершена")

        @router.message()
        def reply(ctx):
            ctx.reply("Сообщение получено")

        router.run_polling(
            state_path=".bot-state.json",
            workers=4,
            chat_queues=True,
            chat_queue_size=64,
        )


if __name__ == "__main__":
    main()
