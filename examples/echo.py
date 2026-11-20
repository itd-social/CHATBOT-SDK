"""Пример: ITD_TOKEN, ITD_SELF_USER_ID и ITD_SINCE должны быть заданы явно."""

import os
from itd_bot import Bot

self_id = os.environ["ITD_SELF_USER_ID"]
since = int(os.environ["ITD_SINCE"])

with Bot(os.environ["ITD_TOKEN"]) as bot:

    def handle(event):

        messages = (
            [event["message"]]
            if event["type"] == "new_message"
            else event.get("messages", [])
            if event["type"] == "new_messages"
            else []
        )
        for message in messages:
            if message.get("sender_id") == self_id:
                continue
            bot.mark_read(event["chat_id"], message["message_id"])
            if not message.get("text"):
                continue
            bot.send_message(
                event["chat_id"],
                "Эхо: " + message["text"],
                reply_to_id=message["message_id"],
            )

    bot.poll(
        since, handle, on_checkpoint=lambda cursor: print("cursor:", cursor, flush=True)
    )
