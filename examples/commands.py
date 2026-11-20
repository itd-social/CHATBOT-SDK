import os
from itd_bot import Bot, Dispatcher

with Bot(os.environ["ITD_TOKEN"]) as bot:
    router = Dispatcher(bot, self_user_id=os.environ["ITD_SELF_USER_ID"])

    @router.command("start")
    def start(ctx):
        ctx.reply("Привет! Напишите /help")

    @router.command("help")
    def help_command(ctx):
        ctx.reply("Команды: /start, /help, /echo текст")

    @router.command("echo")
    def echo(ctx):
        ctx.reply(ctx.args or "Напишите текст после /echo")

    router.run_polling(
        int(os.environ["ITD_SINCE"]),
        on_checkpoint=lambda n: print("cursor:", n, flush=True),
    )
