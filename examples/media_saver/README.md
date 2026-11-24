# media_saver
Сохраняет вложения входящих сообщений в `downloads/` под именем `file_id`.
Существующие файлы не перезаписываются: SDK бросает `FileExistsError`.
Переменные окружения: `ITD_TOKEN` (access-токен).

```sh
ITD_TOKEN=... python examples/media_saver/media_saver.py
```
