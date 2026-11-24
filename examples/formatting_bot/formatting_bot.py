"""Бот показывает форматирование сообщений через конструктор Text."""

import os
import sys

from itd_bot import Bot, Dispatcher, Text


def build_demo() -> Text:
    return (
        Text()
        .bold("Жирный")
        .add(", ")
        .italic("курсив")
        .add(", ")
        .underline("подчёркнутый")
        .add(", ")
        .strike("зачёркнутый")
        .add("\n")
        .code("print('код')")
        .add(" и ")
        .spoiler("спойлер")
        .add("\nСсылка: ")
        .link("репозиторий SDK", "https://github.com/itd-social/CHATBOT-SDK")
    )


def main() -> None:
    token = os.environ.get("ITD_TOKEN")
    if not token:
        sys.exit("Задайте ITD_TOKEN")
    with Bot(token) as bot:
        router = Dispatcher.from_bot(bot)

        @router.command("format")
        def show(ctx):
            ctx.reply_formatted(build_demo())

        @router.command("bold")
        def bold(ctx):
            ctx.reply_formatted(Text().bold(ctx.args or "Напишите текст после /bold"))

        router.run_polling(state_path=".formatting-bot-state.json")


if __name__ == "__main__":
    main()
