"""Бот пересылает входящие сообщения в служебный чат.

Нужны ITD_TOKEN (access-токен) и ITD_CHAT (ID чата-получателя).
Пересылка не повторяется после сбоя сети: результат неизвестен.
Для своих ID используйте bot.forward_messages(..., client_message_ids={...});
прежний список той же длины, что message_ids, преобразуется в словарь.
"""

import os
import sys

from itd_bot import Bot, Dispatcher


def main() -> None:
    token = os.environ.get("ITD_TOKEN")
    target = os.environ.get("ITD_CHAT")
    if not token or not target:
        sys.exit("Задайте ITD_TOKEN и ITD_CHAT")
    with Bot(token) as bot:
        router = Dispatcher.from_bot(bot)

        @router.message()
        def forward(ctx):
            if ctx.chat_id != target:
                ctx.forward_to(target)

        router.run_polling(state_path=".forward-bot-state.json")


if __name__ == "__main__":
    main()
