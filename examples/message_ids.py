"""Автоматический UUIDv7 и серверный флаг replayed."""

import os

from itd_bot import Bot, StaleClientMessageIdError


def main() -> None:
    with Bot(os.environ["ITD_TOKEN"]) as bot:
        try:
            result = bot.send_message(os.environ["ITD_CHAT"], "Проверка клиентского ID")
        except StaleClientMessageIdError:
            print("Проверьте системные часы и источник клиентского ID")
            return
        if result.get("replayed") is True:
            print("Сервер сообщил о повторной отправке")
        # При сетевом сбое не отправляйте сообщение повторно вслепую.


if __name__ == "__main__":
    main()
