"""Бот ставит реакцию на команду /like и печатает чужие реакции.

Доступные эмодзи определяет сервер: см. bot.get_reaction_catalog().
"""

import os
import sys

from itd_bot import APIError, Bot, Dispatcher

EMOJI = "👍"


def main() -> None:
    token = os.environ.get("ITD_TOKEN")
    if not token:
        sys.exit("Задайте ITD_TOKEN")
    with Bot(token) as bot:
        router = Dispatcher.from_bot(bot)

        @router.command("like")
        def like(ctx):
            try:
                ctx.react(EMOJI)
            except APIError as error:
                ctx.reply(f"Реакция не поставлена: {error.status}")

        @router.reaction()
        def seen(ctx):
            print("реакции в сообщении:", ctx.message_id, ctx.reactions)

        router.run_polling(state_path=".reactions-bot-state.json")


if __name__ == "__main__":
    main()
