"""Подсказки типов для страницы чатов; вывод только количества и счётчика."""

from itd_bot import Bot, ChatsResponse


def unread_total(page: ChatsResponse) -> int:
    """Посчитать непрочитанные сообщения только на загруженной странице."""
    return sum(chat.get("unread_count", 0) for chat in page.get("chats", []))


def main() -> None:
    with Bot.from_env() as bot:
        page = bot.get_chats(limit=20)
        print("Чатов на странице:", len(page.get("chats", [])))
        print("Непрочитанных на странице:", unread_total(page))


if __name__ == "__main__":
    main()
