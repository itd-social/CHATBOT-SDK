"""Long-running refresh-session test bot."""

import os
import time
from pathlib import Path

import httpx

from itd_bot import APIError, Bot, Dispatcher, ProtocolError

REFRESH_TOKEN = ""
SESSION_PATH = ".itd-refresh-session.json"
CURSOR_PATH = ".itd-refresh-test-cursor"


def main():
    refresh_token = os.environ.get("ITD_REFRESH_TOKEN") or REFRESH_TOKEN
    if not refresh_token:
        raise SystemExit("Set ITD_REFRESH_TOKEN or REFRESH_TOKEN in this file")
    session_path = os.environ.get("ITD_SESSION_FILE", SESSION_PATH)
    cursor_path = Path(os.environ.get("ITD_CURSOR_FILE", CURSOR_PATH))
    with Bot.from_refresh_token(refresh_token, session_path=session_path) as bot:
        router = Dispatcher.from_bot(bot)

        @router.command("start")
        def start(ctx):
            ctx.reply("Бот работает. Команды: /ping и /echo текст")

        @router.command("ping")
        def ping(ctx):
            ctx.reply("pong")

        @router.command("echo")
        def echo(ctx):
            ctx.reply(ctx.args or "Укажите текст после /echo")

        @router.message()
        def other(ctx):
            ctx.reply("Отправьте /start")

        if cursor_path.exists():
            cursor = int(cursor_path.read_text(encoding="utf-8").strip())
        else:
            cursor = bot.get_chats(limit=1).get("event_seq")
            if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
                raise ProtocolError("Chat list does not contain an event cursor")
        print(
            f"Бот запущен. Успешных обновлений access-токена: {bot.refresh_count}",
            flush=True,
        )
        last_count = bot.refresh_count
        while True:
            try:
                batch = bot.get_updates(cursor)
            except (httpx.TimeoutException, httpx.NetworkError):
                print("Временная сетевая ошибка; повтор через 10 секунд", flush=True)
                time.sleep(10)
                continue
            except APIError as exc:
                if exc.status != 429 and exc.status < 500:
                    raise
                delay = getattr(exc, "retry_after_seconds", None) or (
                    int(exc.retry_after)
                    if exc.retry_after and exc.retry_after.isdecimal()
                    else 10
                )
                print(
                    f"Сервис временно недоступен (HTTP {exc.status}); повтор через {delay} сек.",
                    flush=True,
                )
                time.sleep(max(1, delay))
                continue
            for event in batch["events"]:
                router.handle(event)
            cursor = batch["next_event_seq"]
            temp = cursor_path.with_name(cursor_path.name + ".tmp")
            temp.write_text(str(cursor), encoding="utf-8")
            os.replace(temp, cursor_path)
            if bot.refresh_count != last_count:
                last_count = bot.refresh_count
                print(
                    f"Access-токен обновлён. Всего обновлений: {last_count}", flush=True
                )
            time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Бот остановлен.")
