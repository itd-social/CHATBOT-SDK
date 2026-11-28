"""Create a small bot project with ``python -m itd_bot init NAME``."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

_FILES = {
    "bot.py": """from pathlib import Path

from dotenv import load_dotenv

from itd_bot import Bot, CommandContext, Dispatcher

ROOT = Path(__file__).resolve().parent


def main() -> None:
    load_dotenv(ROOT / ".env", override=False)
    with Bot.from_env(session_path=ROOT / ".itd-session.json") as bot:
        router = Dispatcher.from_bot(bot, **bot.dispatcher_options)

        @router.command("start")
        def start(ctx: CommandContext) -> None:
            ctx.reply("Привет! Я бот на ИТД. Напишите /ping.")

        @router.command("ping")
        def ping(ctx: CommandContext) -> None:
            ctx.reply("pong")

        router.run_polling(state_path=ROOT / ".bot-state.json", **bot.poll_options)


if __name__ == "__main__":
    main()
""",
    ".env": "ITD_REFRESH_TOKEN=\n",
    ".env.example": "ITD_REFRESH_TOKEN=\n",
    ".gitignore": """.env
.env.*
!.env.example
.venv/
.itd-session*
.bot-state.json*
__pycache__/
""",
    "requirements.txt": "itd-chatbot-sdk[env]>=1.5,<2\n",
    "README.md": """# Мой бот на ИТД

1. Впишите refresh-токен аккаунта бота в `.env`: `ITD_REFRESH_TOKEN=значение`.
2. Запустите `.venv/bin/python bot.py`; в PowerShell — `& .\\.venv\\Scripts\\python.exe bot.py`.
3. Напишите боту `/start`, затем `/ping`. Остановка: `Ctrl+C`.

Для отправки сообщений аккаунту нужна подписка «ИТД НУКСТА».
Токен, сессия и курсор хранятся рядом с `bot.py`. Не публикуйте их и не запускайте две копии одной сессии.
[Получить токен и продолжить разработку](https://github.com/itd-social/CHATBOT-SDK/blob/main/docs/GETTING_STARTED.md).
""",
}


def _command(arguments: list[str]) -> str:
    if os.name == "nt":
        return "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in arguments)
    return shlex.join(arguments)


def _init(path: str) -> int:
    try:
        project = Path(path).expanduser().resolve()
        project.mkdir(parents=True)
        for name, content in _FILES.items():
            with (project / name).open("x", encoding="utf-8", newline="\n") as file:
                file.write(content)
    except FileExistsError:
        print(
            "Путь уже существует. Выберите новую папку: чужие файлы не изменены.",
            file=sys.stderr,
        )
        return 1
    except OSError:
        print(
            "Не удалось создать проект. Проверьте путь, права и свободное место.",
            file=sys.stderr,
        )
        return 1

    print("Создаём виртуальное окружение…", flush=True)
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(project / ".venv")],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        print(
            "Не удалось создать .venv. Проверьте поддержку venv и ensurepip в вашем Python.\n"
            "В Ubuntu/Debian установите python3-venv. Созданные файлы сохранены; для повтора выберите новую папку.",
            file=sys.stderr,
        )
        return 1

    python = (
        project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    install = [
        str(python),
        "-m",
        "pip",
        "install",
        "-r",
        str(project / "requirements.txt"),
    ]
    print("Устанавливаем SDK и дополнение env…", flush=True)
    try:
        subprocess.run(
            install, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.CalledProcessError):
        print(
            "Не удалось установить зависимости. Проверьте доступ к PyPI и повторите:\n"
            + _command(install),
            file=sys.stderr,
        )
        return 1

    print(
        f"\nПроект готов: {project}\nВпишите refresh-токен в {project / '.env'}\nЗапустите:\n"
        + _command([str(python), str(project / "bot.py")])
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Создайте первого бота для ИТД")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="создать проект с /start и /ping")
    init.add_argument("path", help="новая папка проекта")
    args = parser.parse_args(argv)
    try:
        return _init(args.path)
    except KeyboardInterrupt:
        print("\nСоздание остановлено. Созданные файлы сохранены.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
