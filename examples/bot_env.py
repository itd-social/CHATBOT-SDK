"""Настройки из окружения и явно загружаемого .env."""

from itd_bot import Bot, Dispatcher


def main() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        raise SystemExit(
            'Установите дополнение: pip install "itd-chatbot-sdk[env]"'
        ) from None

    load_dotenv(".env", override=False)
    with Bot.from_env() as bot:
        router = Dispatcher.from_bot(bot, **bot.dispatcher_options)

        @router.message()
        def reply(ctx):
            ctx.reply("Сообщение получено")

        router.run_polling(state_path=".bot-state.json", **bot.poll_options)


if __name__ == "__main__":
    main()
