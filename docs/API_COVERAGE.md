# Покрытие API по бандлу

Проверьте новую сборку сайта локально. Инструмент не исполняет JavaScript и не обращается к ИТД:

```powershell
python tools/check_bundle.py "C:\path\to\bundle"
python tools/check_bundle.py "C:\path\to\bundle" --json --snapshot .venv/bundle-candidate.json
```

На Linux/macOS передайте обычный путь к распакованной папке. Дополнительные зависимости не нужны. Скрипт и опись включены в sdist; запускайте из исходников репозитория или распакованного sdist.

Коды завершения: **0** — различий нет; **1** — есть новое, исчезнувшее или изменившееся; **2** — ошибка чтения, разбора или описи. Отсутствующий/неполный бандл не считается успешной проверкой. `--snapshot` создаёт новый файл и не перезаписывает существующий. Сначала проверьте кандидата вручную, затем перенесите согласованные записи в [api-coverage.json](api-coverage.json). Сохраните ручные заметки об уверенности и скрытых возможностях: кандидат содержит только автоматическую часть.

В обязательном CI нет запуска по настоящему бандлу. CI проверяет синтаксис скрипта и тесты на вымышленных JS-фрагментах. Исходники бандла, сессии и сырые диагностические выгрузки в репозиторий не добавляйте.

## Что сравнивается

«Новое» и «исчезнувшее» — изменился набор имён маршрутов или событий. «Изменившееся» — поменялись видимые HTTP-методы, соответствия методам SDK или автоматически определённая уверенность. Хеши и смещения помогают найти источник; их смена сама по себе не означает изменение API.

Явные query и fragment удаляются; подстановки, UUID и длинные значения сегментов заменяются на `{}`. Имена параметров и содержимое запросов не сравниваются. Соответствия SDK получаются из AST прямых `request`/`api_request` и регистрации событий Dispatcher, без импорта SDK; общие `request`, `on_event` и `websocket_events` не считаются отдельными реализациями каждой возможности.

## Уверенность и ограничения

- **Подтверждено в клиенте (`confirmed`)**: виден вызов `.get/.post/...`, `fetch` или ветка обработки события. Имя метода адаптера ещё не доказывает HTTP-вызов: найденные места проверяйте вручную. Это не подтверждение схемы или поведения сервера.
- **Подготовлено, но не активно (`prepared_inactive`)**: ручная заметка о скрытой возможности. Сейчас так отмечены стикеры; методов отправки и каталога из этой записи не следует.
- **Гипотеза (`hypothesis`)**: строка API-пути либо частично динамический шаблон. Метод и назначение не установлены. У каждого места в JSON есть собственная уверенность; для объединённой строки показано наиболее сильное свидетельство.

Это лексический анализатор, а не полный JS-парсер. Он понимает строки, шаблоны с вложенными выражениями, комментарии, обычные regex-литералы и `switch(event.type)` с известной веткой сообщений. Он не вычисляет алиасы, конкатенации, вызовы-фабрики, импортированные значения и произвольные формы JavaScript. Новый способ построения URL или переключатель через переменную может выглядеть как исчезновение. Изменения полей JSON, лимитов, доступности presence/typing, а также динамические и не попавшие в выбранные файлы маршруты он не подтверждает. Отсутствие расхождений не доказывает отсутствие изменений API.

## Файлы и исходная точка

Проверены локальные файлы 27 ноября 2026 года относительно SDK v1.4.0 (`41cc7d7`). Версия сайта неизвестна. Основной файл: `index-1f7c8b01465d.js`, SHA-256 `279217a6802831c32f91e3808312b101c7352d93e8acb2126b81a81c5c226e37`.

| Роль | Что искать |
|---|---|
| `index-*.js` | Общий клиент, HTTP-адаптеры, схемы и переключатель событий; проверяются все файлы этого семейства |
| `route-*.js` | Интерфейс мессенджера и его проверки; строки навигации сами по себе не являются API |
| `messengerMediaApi-*.js` | Инициализация и завершение загрузок медиа |

Нужны все три семейства. Остальные JS-файлы не анализируются. Точные относительные имена, хеши и смещения в символах Unicode от начала файла записаны в JSON. Номера строк из Issues относятся к другой раскладке бандла.

Автоматическая опись: **106 маршрутов-кандидатов**, **14 типов событий**. В таблицах «—» означает отсутствие прямого статического соответствия, а не доказанное отсутствие поддержки.

## Маршруты

| Путь клиента | Видимые методы | Прямое соответствие SDK | Уверенность |
|---|---|---|---|
| `/api/legal-documents` | — | — | Гипотеза |
| `/api/legal-documents/social/{}` | — | — | Гипотеза |
| `/api/legal-documents/social/{}/versions` | — | — | Гипотеза |
| `/api/legal-documents/social/{}/versions/{}` | — | — | Гипотеза |
| `/api/v1/auth` | — | — | Гипотеза |
| `/api/v1/auth/phone/config` | GET | — | Вызов/ветка в клиенте |
| `/api/v1/auth/phone/resume` | — | — | Гипотеза |
| `/api/v1/auth/phone/reverse` | — | — | Гипотеза |
| `/api/v1/auth/terms-acceptance` | — | — | Гипотеза |
| `/api/v1/event-assets` | — | — | Гипотеза |
| `/api/v1/messenger` | — | — | Гипотеза |
| `/api/v1/messenger/browser-file-session` | — | — | Гипотеза |
| `/change/confirm` | POST | — | Вызов/ветка в клиенте |
| `/change/pending` | GET | — | Вызов/ветка в клиенте |
| `/change/pending/{}` | DELETE | — | Вызов/ветка в клиенте |
| `/change/proof` | POST | — | Вызов/ветка в клиенте |
| `/change/wait` | POST | — | Вызов/ветка в клиенте |
| `/chats/dm` | POST | `Bot.create_direct_chat` | Вызов/ветка в клиенте |
| `/chats/unread` | GET | `Bot.get_unread_count` | Вызов/ветка в клиенте |
| `/chats/{}` | GET | `Bot.get_chat` | Вызов/ветка в клиенте |
| `/chats/{}/accept` | POST | `Bot.accept_chat` | Вызов/ветка в клиенте |
| `/chats/{}/action` | POST | `Bot.send_action` | Вызов/ветка в клиенте |
| `/chats/{}/delete` | POST | `Bot.delete_chat` | Вызов/ветка в клиенте |
| `/chats/{}/file-links` | POST | `Bot.get_file_links` | Вызов/ветка в клиенте |
| `/chats/{}/heard` | POST | `Bot.get_heard_messages` | Вызов/ветка в клиенте |
| `/chats/{}/media` | GET | `Bot.get_media` | Вызов/ветка в клиенте |
| `/chats/{}/messages` | POST | `Bot.send_message` | Вызов/ветка в клиенте |
| `/chats/{}/messages/around/{}` | GET | `Bot.get_messages_around` | Вызов/ветка в клиенте |
| `/chats/{}/messages/delete-for-all` | POST | `Bot.delete_messages` | Вызов/ветка в клиенте |
| `/chats/{}/messages/delete-for-me` | POST | `Bot.delete_messages` | Вызов/ветка в клиенте |
| `/chats/{}/messages/forward` | POST | `Bot.forward_messages` | Вызов/ветка в клиенте |
| `/chats/{}/messages/{}` | PATCH | `Bot.edit_message` | Вызов/ветка в клиенте |
| `/chats/{}/messages/{}/heard` | POST | `Bot.mark_voice_heard` | Вызов/ветка в клиенте |
| `/chats/{}/messages/{}/reactions` | GET, PUT | `Bot.get_reactions`, `Bot.set_reactions` | Вызов/ветка в клиенте |
| `/chats/{}/messages{}` | GET | — | Гипотеза |
| `/chats/{}/mute` | PATCH | `Bot.set_muted` | Вызов/ветка в клиенте |
| `/chats/{}/pin` | PATCH | `Bot.set_pinned` | Вызов/ветка в клиенте |
| `/chats/{}/read` | POST | `Bot.mark_read` | Вызов/ветка в клиенте |
| `/chats/{}/unread` | PATCH | `Bot.mark_unread` | Вызов/ветка в клиенте |
| `/chats{}{}manual_unread=1` | GET | — | Гипотеза |
| `/correctors/cancel` | POST | — | Вызов/ветка в клиенте |
| `/correctors/inventory` | GET | — | Вызов/ветка в клиенте |
| `/correctors/report` | POST | — | Вызов/ветка в клиенте |
| `/correctors/state` | GET | — | Вызов/ветка в клиенте |
| `/email/attach` | POST | — | Вызов/ветка в клиенте |
| `/email/change` | POST | — | Вызов/ветка в клиенте |
| `/event-nicknames` | GET | — | Вызов/ветка в клиенте |
| `/files/banner-drawing` | POST | — | Вызов/ветка в клиенте |
| `/media/upload/init` | POST | `Bot.upload_file` | Вызов/ветка в клиенте |
| `/media/upload/{}/finalize` | POST | `Bot.upload_file` | Вызов/ветка в клиенте |
| `/messenger/entry` | POST | — | Вызов/ветка в клиенте |
| `/methods` | GET | — | Вызов/ветка в клиенте |
| `/phone/change` | POST | — | Вызов/ветка в клиенте |
| `/presence` | POST | `Bot.get_presence_many` | Вызов/ветка в клиенте |
| `/presence/{}` | GET | `Bot.get_presence` | Вызов/ветка в клиенте |
| `/profile-avatar` | DELETE, GET | — | Вызов/ветка в клиенте |
| `/reactions/available` | GET | `Bot.get_reaction_catalog` | Вызов/ветка в клиенте |
| `/red-pens/cancel` | POST | — | Вызов/ветка в клиенте |
| `/red-pens/inventory` | GET | — | Вызов/ветка в клиенте |
| `/red-pens/report` | POST | — | Вызов/ветка в клиенте |
| `/red-pens/state` | GET | — | Вызов/ветка в клиенте |
| `/resend-otp` | POST | — | Вызов/ветка в клиенте |
| `/set-password` | POST | — | Вызов/ветка в клиенте |
| `/updates` | GET | `Bot.get_updates` | Вызов/ветка в клиенте |
| `/users` | GET | — | Вызов/ветка в клиенте |
| `/users/check-username` | GET | — | Вызов/ветка в клиенте |
| `/users/profile` | POST | — | Вызов/ветка в клиенте |
| `/users/{}/messages` | POST | `Bot.send_message_to_user` | Вызов/ветка в клиенте |
| `/v1/aliceai/balance` | GET | — | Вызов/ветка в клиенте |
| `/v1/aliceai/inventory` | GET | — | Вызов/ветка в клиенте |
| `/v1/aliceai/nicknames` | GET | — | Вызов/ветка в клиенте |
| `/v1/aliceai/nicknames/active` | PUT | — | Вызов/ветка в клиенте |
| `/v1/aliceai/profiles/{}` | GET | — | Вызов/ветка в клиенте |
| `/v1/aliceai/profiles/{}/balloons` | POST | — | Вызов/ветка в клиенте |
| `/v1/aliceai/profiles/{}/curtains` | PUT | — | Вызов/ветка в клиенте |
| `/v1/aliceai/profiles/{}/curtains/donations` | POST | — | Вызов/ветка в клиенте |
| `/v1/aliceai/profiles/{}/cushion` | POST | — | Вызов/ветка в клиенте |
| `/v1/aliceai/profiles/{}/cushion/claim` | POST | — | Вызов/ветка в клиенте |
| `/v1/aliceai/profiles/{}/placements` | POST | — | Вызов/ветка в клиенте |
| `/v1/aliceai/profiles/{}/placements/{}/erase` | POST | — | Вызов/ветка в клиенте |
| `/v1/aliceai/profiles/{}/window/break` | POST | — | Вызов/ветка в клиенте |
| `/v1/aliceai/shop` | GET | — | Вызов/ветка в клиенте |
| `/v1/aliceai/waste-paper/posts/{}` | POST | — | Вызов/ветка в клиенте |
| `/v1/auth/sessions` | — | — | Гипотеза |
| `/v1/auth/sessions/{}` | — | — | Гипотеза |
| `/v1/event-assets` | GET | — | Вызов/ветка в клиенте |
| `/v1/event/status` | GET | — | Вызов/ветка в клиенте |
| `/v1/halloween` | — | — | Гипотеза |
| `/v1/i` | — | — | Гипотеза |
| `/v1/portal` | GET | — | Вызов/ветка в клиенте |
| `/v1/subscription` | — | — | Гипотеза |
| `/v1/subscription/auto-renewal` | — | — | Гипотеза |
| `/v1/subscription/bind-card` | — | — | Гипотеза |
| `/v1/subscription/checkout-release` | — | — | Гипотеза |
| `/v1/subscription/checkout-state` | — | — | Гипотеза |
| `/v1/subscription/gift-giver` | GET | — | Вызов/ветка в клиенте |
| `/v1/subscription/gifts` | — | — | Гипотеза |
| `/v1/subscription/gifts/{}` | — | — | Гипотеза |
| `/v1/subscription/international-checkouts` | — | — | Гипотеза |
| `/v1/subscription/methods` | — | — | Гипотеза |
| `/v1/subscription/methods/{}` | — | — | Гипотеза |
| `/v1/subscription/methods/{}/default` | — | — | Гипотеза |
| `/v1/subscription/pay` | — | — | Гипотеза |
| `/v1/subscription/payments/{}` | — | — | Гипотеза |
| `/v1/x` | — | — | Гипотеза |
| `/verify-otp` | POST | — | Вызов/ветка в клиенте |

## События

| Тип | Прямое соответствие SDK |
|---|---|
| `chat_created` | — |
| `chat_deleted` | — |
| `chat_settings` | — |
| `history_cleared` | — |
| `message_deleted` | `Dispatcher.deleted` |
| `message_edited` | `Dispatcher.edited` |
| `message_reactions` | `Dispatcher.reaction` |
| `messages_deleted` | `Dispatcher.deleted` |
| `messages_read` | — |
| `new_message` | `Dispatcher.handle` |
| `new_messages` | `Dispatcher.handle` |
| `own_messages` | — |
| `presence` | — |
| `typing` | — |

Наличие ветки `presence` или `typing` в клиенте не доказывает доставку через `/updates`. Этот живой пункт остаётся в [#31](https://github.com/itd-social/CHATBOT-SDK/issues/31). Общие правила исследования маршрутов — в [шпаргалке](ROUTE_INVENTORY.md).
