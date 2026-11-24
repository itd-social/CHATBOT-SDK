# rate_limit_demo
Офлайн-показ корзин токенов `RateLimiter` с подменёнными временем и ожиданием.
Печатает паузы для отправки, правок в разных чатах и 429 с `Retry-After`.
Переменные окружения не нужны, сеть и токен не используются.

```sh
python examples/rate_limit_demo/rate_limit_demo.py
```
