"""/countdown: бот отправляет сообщение, правит его и удаляет.

Правки идут через ограничитель SDK: корзина правок небольшая, поэтому
SDK сам выдерживает паузы между ними.
"""

import os
import sys

from itd_bot import Bot, Dispatcher

STEPS = (3, 2, 1)


def main() -> None:
    token = os.environ.get("ITD_TOKEN")
    if not token:
        sys.exit("Задайте ITD_TOKEN")
    with Bot(token) as bot:
        router = Dispatcher.from_bot(bot)

        @router.command("countdown")
        def countdown(ctx):
            sent = ctx.reply_message(f"Осталось: {STEPS[0]}")
            for value in STEPS[1:]:
                sent.edit(f"Осталось: {value}")
            sent.edit("Готово")
            sent.delete()

        router.run_polling(state_path=".countdown-bot-state.json")


if __name__ == "__main__":
    main()
