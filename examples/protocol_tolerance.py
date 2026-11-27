"""Продолжение опроса при изменении формата /updates."""

import os

from itd_bot import Bot, Dispatcher


def main() -> None:
    with Bot(os.environ["ITD_TOKEN"], on_protocol_error="skip") as bot:
        router = Dispatcher.from_bot(bot)

        @router.message()
        def on_message(ctx):
            ctx.reply("Сообщение получено")

        router.run_polling(state_path=".bot-state.json")
        # Фрагменты могут содержать личные данные: не печатайте их в лог.
        print("Сохранено проблемных фрагментов:", len(bot.protocol_errors))


if __name__ == "__main__":
    main()
