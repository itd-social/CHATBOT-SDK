"""Пример команд и удобных действий с сообщением."""

import os

from itd_bot import Bot, Dispatcher

with Bot.from_refresh_token(os.environ["ITD_REFRESH_TOKEN"]) as bot:
    router = Dispatcher.from_bot(bot)

    @router.command("start")
    def start(ctx):
        ctx.reply("Привет! Напишите /help")

    @router.command("help")
    def help_command(ctx):
        ctx.reply("Команды: /start, /help, /echo текст, /profile")

    @router.command("echo")
    def echo(ctx):
        ctx.reply(ctx.args or "Напишите текст после /echo")

    @router.command("profile")
    def profile(ctx):
        user = ctx.get_sender()
        ctx.reply(f"Привет, {user.display_name or user.username or 'друг'}!")

    @router.photo()
    def photo(ctx):
        ctx.reply(f"Получено фото: {len(ctx.photo)}")

    @router.message()
    def other(ctx):
        ctx.reply("Введите /help, чтобы посмотреть команды")

    router.run_polling(state_path=".bot-state.json")
