"""Interactive API test bot. Run only with a dedicated test account.

Environment: ITD_TOKEN, ITD_SELF_USER_ID, ITD_TESTER_ID. Optional:
ITD_CURSOR_FILE (defaults to .itd-test-bot-cursor) and ITD_BASE_URL.
"""

import json
import os
import tempfile
import time
from pathlib import Path

from itd_bot import APIError, Bot, Dispatcher, Text

HELP = """Команды тестового бота:
/start, /help — эта справка
/ping — проверка ответа
/echo текст — повторить текст
/id — идентификаторы чата, отправителя и сообщения
/chat — сведения о текущем чате
/history — последние сообщения чата
/presence — присутствие отправителя
/typing — действие «печатает»
/wait [секунды] — проверить typing при длительной обработке
/react [эмодзи] — поставить реакцию на эту команду
/format — пример оформления текста
/file — отправить небольшой текстовый файл
/download FILE_ID — скачать вложение текущего чата и отправить обратно
/api METHOD /api/путь [JSON] — экспериментальный API-запрос
"""


def main():
    token = os.environ["ITD_TOKEN"]
    self_id = os.environ["ITD_SELF_USER_ID"]
    tester_id = os.environ["ITD_TESTER_ID"]
    cursor_path = Path(os.environ.get("ITD_CURSOR_FILE", ".itd-test-bot-cursor"))
    with Bot(
        token, base_url=os.environ.get("ITD_BASE_URL", "https://xn--d1ah4a.com")
    ) as bot:
        router = Dispatcher(bot, self_user_id=self_id)

        def command(name):
            def decorate(fn):
                @router.command(name)
                def guarded(ctx):
                    if ctx.message["sender_id"] != tester_id:
                        return
                    try:
                        fn(ctx)
                    except APIError as exc:
                        ctx.reply(
                            f"API error: HTTP {exc.status}, code={exc.code or 'unknown'}"
                        )
                    except (ValueError, KeyError) as exc:
                        ctx.reply("Некорректный запрос: " + str(exc)[:400])

                return guarded

            return decorate

        def show(ctx, value):
            text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
            ctx.reply(text[:3500] + ("\n… вывод сокращён" if len(text) > 3500 else ""))

        @command("start")
        @command("help")
        def help_command(ctx):
            ctx.reply(HELP)

        @command("ping")
        def ping(ctx):
            ctx.reply("pong")

        @command("echo")
        def echo(ctx):
            ctx.reply(ctx.args or "Передайте текст после /echo")

        @command("id")
        def ids(ctx):
            show(
                ctx,
                {
                    "chat_id": ctx.chat_id,
                    "sender_id": ctx.message["sender_id"],
                    "message_id": ctx.message["message_id"],
                },
            )

        @command("chat")
        def chat(ctx):
            show(ctx, bot.get_chat(ctx.chat_id))

        @command("history")
        def history(ctx):
            show(ctx, bot.get_messages(ctx.chat_id, limit=5))

        @command("presence")
        def presence(ctx):
            show(ctx, bot.get_presence(ctx.message["sender_id"]))

        @command("typing")
        def typing(ctx):
            bot.send_action(ctx.chat_id)
            ctx.reply("Отправил действие typing")

        @command("wait")
        def wait_command(ctx):
            seconds = int(ctx.args or "10")
            if not 1 <= seconds <= 30:
                raise ValueError("Укажите от 1 до 30 секунд")
            time.sleep(seconds)
            ctx.reply(f"Готово через {seconds} секунд")

        @command("react")
        def react(ctx):
            bot.add_reaction(ctx.chat_id, ctx.message["message_id"], ctx.args or "👍")
            ctx.reply("Реакция отправлена")

        @command("format")
        def formatting(ctx):
            content = (
                Text("Форматирование 👋\n")
                .bold("Жирный")
                .add(" · ")
                .italic("курсив")
                .add(" · ")
                .underline("подчёркнутый")
                .add("\n")
                .strike("зачёркнутый")
                .add(" · ")
                .code("код")
                .add(" · ")
                .spoiler("спойлер")
                .add("\n")
                .link("Ссылка", "https://xn--d1ah4a.com")
            )
            ctx.reply_formatted(content)

        @command("file")
        def file_command(ctx):
            bot.send_file(
                ctx.chat_id,
                b"ITD SDK test file\n",
                filename="itd-test.txt",
                mime="text/plain",
                text="Тестовый файл",
                reply_to_id=ctx.message["message_id"],
            )

        @command("download")
        def download(ctx):
            if not ctx.args:
                raise ValueError("Укажите file_id из вложения")
            with tempfile.TemporaryDirectory(prefix="itd-test-") as directory:
                target = Path(directory) / "downloaded.bin"
                bot.download_file(ctx.chat_id, ctx.args, destination=target)
                bot.send_file(
                    ctx.chat_id,
                    target,
                    text="Скачанный файл",
                    reply_to_id=ctx.message["message_id"],
                )

        @command("api")
        def api(ctx):
            parts = ctx.args.split(maxsplit=2)
            if len(parts) < 2 or parts[0].upper() not in {
                "GET",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
            }:
                raise ValueError("Используйте /api METHOD /api/путь [JSON]")
            body = json.loads(parts[2]) if len(parts) == 3 else None
            show(ctx, bot.api_request(parts[0].upper(), parts[1], json=body))

        @router.on_message
        def fallback(event, message):
            if message["sender_id"] == tester_id:
                bot.send_message(
                    event["chat_id"],
                    "Используйте /help для списка команд",
                    reply_to_id=message["message_id"],
                )

        if cursor_path.exists():
            since = int(cursor_path.read_text(encoding="utf-8").strip())
        else:
            since = bot.get_chats(limit=1).get("event_seq")
            if isinstance(since, bool) or not isinstance(since, int) or since < 0:
                raise RuntimeError("Сервер не вернул начальный курсор")
            print("Первый запуск: старые события пропущены.", flush=True)

        def checkpoint(cursor):

            temp = cursor_path.with_name(cursor_path.name + ".tmp")
            temp.write_text(str(cursor), encoding="utf-8")
            os.replace(temp, cursor_path)

        print("Тестовый бот запущен. Остановка: Ctrl+C.", flush=True)
        router.run_polling(since, on_checkpoint=checkpoint)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nБот остановлен.")
