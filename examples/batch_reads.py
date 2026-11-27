"""Одна отметка прочтения на чат за полученный пакет."""

import os

from itd_bot import Bot, Dispatcher


def main() -> None:
    with Bot(os.environ["ITD_TOKEN"]) as bot:
        router = Dispatcher.from_bot(bot, read_mode="batch")

        @router.message()
        def reply(ctx):
            ctx.reply("Сообщение получено")

        # Последнее сообщение пакета отмечается до первого обработчика чата.
        router.run_polling(state_path=".bot-state.json", interval=5.0)


if __name__ == "__main__":
    main()
