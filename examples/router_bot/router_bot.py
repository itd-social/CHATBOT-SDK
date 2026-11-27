"""Команды во вложенном Router и фильтр обычного текста."""

import os

from itd_bot import Bot, CommandContext, MessageContext, Router, filters


def main() -> None:
    if not any(
        os.environ.get(name)
        for name in ("ITD_TOKEN", "ITD_REFRESH_TOKEN", "ITD_SESSION_PATH")
    ):
        raise SystemExit("Задайте ITD_TOKEN, ITD_REFRESH_TOKEN или ITD_SESSION_PATH")
    commands = Router()

    @commands.message(filters.command("start"))
    def start(ctx: CommandContext) -> None:
        ctx.reply("Здравствуйте! Напишите /echo текст или помощь")

    @commands.message(filters.command("echo"))
    def echo(ctx: CommandContext) -> None:
        ctx.reply(ctx.args or "Добавьте текст после /echo")

    with Bot.from_env() as bot:
        router = Router.from_bot(bot, **bot.dispatcher_options)
        router.include_router(commands)

        @router.message(filters.text("помощь") | filters.regex(r"^help\b"))
        def help_message(ctx: MessageContext) -> None:
            ctx.reply("Команды: /start, /echo текст")

        router.run_polling(state_path=".bot-state.json", **bot.poll_options)


if __name__ == "__main__":
    main()
