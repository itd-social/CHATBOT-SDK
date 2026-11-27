"""Обработка серверных ошибок без вывода содержимого ответа в лог."""

import os

from itd_bot import (
    APIError,
    BlockedError,
    Bot,
    MessagesClosedError,
    NukstaRequiredError,
)


def main() -> None:
    token = os.environ.get("ITD_TOKEN")
    chat_id = os.environ.get("ITD_CHAT")
    if not token or not chat_id:
        raise SystemExit("Задайте ITD_TOKEN и ITD_CHAT")
    with Bot(token) as bot:
        try:
            bot.send_message(chat_id, "Привет!")
        except NukstaRequiredError:
            print("Проверьте подписку ИТД НУКСТА у аккаунта бота")
        except (BlockedError, MessagesClosedError):
            print("Отправка этому получателю недоступна")
        except APIError as error:
            print(f"Сервер вернул HTTP {error.status}")


if __name__ == "__main__":
    main()
