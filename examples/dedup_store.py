"""Явно подключённое хранилище дедупликации успешных сообщений."""

import os

from itd_bot import Bot, Dispatcher, MemoryDedupStore


def main() -> None:
    token = os.environ.get("ITD_REFRESH_TOKEN")
    if not token:
        raise SystemExit("Задайте ITD_REFRESH_TOKEN")
    store = MemoryDedupStore(capacity=4096)
    with Bot.from_refresh_token(token) as bot:
        router = Dispatcher.from_bot(bot, dedup_store=store)

        @router.message()
        def echo(ctx):
            ctx.reply(ctx.text or "Получено сообщение")

        router.run_polling(state_path=".bot-state.json")


if __name__ == "__main__":
    main()
