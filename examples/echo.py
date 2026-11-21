"""Эхо-бот с refresh-сессией и сохранением курсора."""

import os

from itd_bot import Bot, Dispatcher

with Bot.from_refresh_token(os.environ["ITD_REFRESH_TOKEN"]) as bot:
    router = Dispatcher.from_bot(bot)

    @router.message()
    def echo(ctx):
        if ctx.text:
            ctx.reply("Эхо: " + ctx.text)

    router.run_polling(state_path=".bot-state.json")
