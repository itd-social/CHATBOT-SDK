"""Чтение подготовленной сущности стикера на подставных данных, без сети."""

from itd_bot import MessagePayload, message_stickers


def main() -> None:
    sample: MessagePayload = {
        "message_id": "EXAMPLE_MESSAGE_ID",
        "entities": [
            {"type": "sticker", "sticker_id": "EXAMPLE_STICKER_ID", "future": True},
            {"type": "future_entity"},
        ],
    }
    print("Стикер-сущностей в подставном сообщении:", len(message_stickers(sample)))
    print("Отправка и каталог стикеров не поддерживаются")


if __name__ == "__main__":
    main()
