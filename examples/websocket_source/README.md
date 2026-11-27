# События из WebSocket

Установите `pip install "itd-chatbot-sdk[websocket]"` и задайте [ITD-переменные](../../docs/ENVIRONMENT.md).
Запустите `python examples/websocket_source/websocket_source.py` из корня репозитория.
Пример принимает сообщения, presence и typing, не отправляя ответов и не выводя личные данные.
Пункт живого сравнения presence/typing в #31 пока открыт.
Правила восстановления и курсора — в [руководстве](../../docs/WEBSOCKET_SOURCE.md).
