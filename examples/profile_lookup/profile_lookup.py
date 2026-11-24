"""Печатает публичные сведения о пользователе: python profile_lookup.py.

Нужны ITD_TOKEN (access-токен) и ITD_USER (ID или ник). Скрипт читает
данные и ничего не отправляет.
"""

import os
import sys

from itd_bot import Bot


def main() -> None:
    token = os.environ.get("ITD_TOKEN")
    user_id = os.environ.get("ITD_USER")
    if not token or not user_id:
        sys.exit("Задайте ITD_TOKEN и ITD_USER")
    with Bot(token) as bot:
        user = bot.get_user(user_id)
        print("Имя:", user.display_name or user.username)
        print("Подписчики:", user.followers_count)
        print("Подписки:", user.following_count)
        print("Постов:", user.posts_count)
        print("Подписан на бота:", user.is_followed_by)
        print("Баннер:", user.banner_url)


if __name__ == "__main__":
    main()
