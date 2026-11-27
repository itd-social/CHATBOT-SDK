"""Проверка клиентских лимитов перед отправкой сообщения."""

import os

from itd_bot import Bot, MessageValidationError, SendResponse


def send_checked(bot: Bot, chat_id: str, text: str) -> SendResponse | None:
    try:
        return bot.send_message(chat_id, text, validate_limits=True)
    except MessageValidationError as error:
        print(error)
        return None


def main() -> None:
    chat_id = os.environ.get("ITD_CHAT")
    if not chat_id:
        raise SystemExit("Задайте ITD_CHAT и настройки аккаунта ITD_*")
    with Bot.from_env() as bot:
        send_checked(bot, chat_id, "Здравствуйте!")


if __name__ == "__main__":
    main()
