"""Статусы действий в чате: typing и upload_video с прогрессом.

Нужны переменные окружения ITD_TOKEN (access-токен) и ITD_CHAT (ID чата).
"""

import os
import sys
import time

from itd_bot import Bot, ChatAction

token = os.environ.get("ITD_TOKEN")
chat_id = os.environ.get("ITD_CHAT")
if not token or not chat_id:
    sys.exit("Задайте ITD_TOKEN и ITD_CHAT")

with Bot(token) as bot:
    # 1. «печатает...»: статус повторяется в фоне, пока выполняется блок with.
    with bot.chat_action(chat_id):
        time.sleep(5)

    # 2. upload_video с плавным прогрессом от 0 до 1. Фоновый поток отправляет
    #    текущее handle.progress каждые interval секунд (не меньше 1).
    with bot.chat_action(
        chat_id, ChatAction.UPLOAD_VIDEO, interval=1.0, progress=0.0
    ) as action:
        for step in range(1, 11):
            time.sleep(1)
            action.progress = step / 10

    # 3. Прогресс не обязан расти: подойдёт любая последовательность из 0..1.
    with bot.chat_action(
        chat_id, ChatAction.UPLOAD_VIDEO, interval=1.0, progress=0.99
    ) as action:
        for value in (0.98, 0.97):
            time.sleep(1.5)
            action.progress = value

    # Ошибка фонового потока (например, 429) не прерывает блок, её видно здесь.
    if action.error is not None:
        print("Фоновая отправка остановилась:", type(action.error).__name__)
