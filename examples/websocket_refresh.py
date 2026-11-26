"""Use WebSocket wakes with refresh and cursor-based recovery."""

import os

from itd_bot import Bot, Dispatcher


def main() -> None:
    # Install itd-chatbot-sdk[websocket] before running this example.
    with Bot(refresh_token=os.environ["ITD_REFRESH_TOKEN"]) as bot:
        router = Dispatcher.from_bot(bot)

        @router.message()
        def on_message(message):
            bot.send_message(message.chat_id, "Сообщение получено")

        router.run_polling(websocket=True, state_path=".bot-state.json")


if __name__ == "__main__":
    main()
