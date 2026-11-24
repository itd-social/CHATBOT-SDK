"""Бот сохраняет присланные вложения в папку downloads/.

Имя файла берётся из file_id, а не от отправителя, чтобы имя не могло
указать за пределы папки. Существующие файлы SDK не перезаписывает.
"""

import os
import sys
from pathlib import Path

from itd_bot import Bot, Dispatcher

TARGET = Path("downloads")


def main() -> None:
    token = os.environ.get("ITD_TOKEN")
    if not token:
        sys.exit("Задайте ITD_TOKEN")
    TARGET.mkdir(exist_ok=True)
    with Bot(token) as bot:
        router = Dispatcher.from_bot(bot)

        @router.message()
        def save(ctx):
            saved = 0
            for item in ctx.attachments:
                if not item.file_id:
                    continue
                try:
                    item.download(TARGET / item.file_id)
                    saved += 1
                except FileExistsError:
                    pass
            if saved:
                ctx.reply(f"Сохранено файлов: {saved}")

        router.run_polling(state_path=".media-saver-state.json")


if __name__ == "__main__":
    main()
