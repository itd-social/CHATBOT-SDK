"""/secret отвечает только подписчикам бота.

ctx.is_subscribed() делает отдельный запрос профиля и возвращает None,
если сервер не прислал поле is_followed_by.
"""

import os
import sys

from itd_bot import Bot, Dispatcher


def main() -> None:
    token = os.environ.get("ITD_TOKEN")
    if not token:
        sys.exit("Задайте ITD_TOKEN")
    with Bot(token) as bot:
        router = Dispatcher.from_bot(bot)

        @router.command("secret")
        def secret(ctx):
            status = ctx.is_subscribed()
            if status is None:
                ctx.reply("Не удалось определить подписку")
            elif status:
                ctx.reply("Секрет для подписчиков")
            else:
                ctx.reply("Сначала подпишитесь на аккаунт бота")

        router.run_polling(state_path=".subscribers-only-state.json")


if __name__ == "__main__":
    main()
