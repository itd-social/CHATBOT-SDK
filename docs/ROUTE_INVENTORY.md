# Маршруты ИТД

Шпаргалка для тех, кто исследует платформу или пользуется `request()` и `api_request()`. Список автоматически собран из строк JavaScript-бандла ИТД.

> **Это подсказка, а не спецификация.** Часть строк — пути интерфейса или шаблоны, динамические маршруты могут отсутствовать, а метод HTTP, права и формат ответа мы не проверяли. Прежде чем на что-то опираться, проверьте маршрут на тестовом аккаунте.

Почти всё найдено в основном файле сборки. Базовые пути: `/api/v1/messenger` для мессенджера, `/api/v1/auth` для входа, `/api/...` и `/v1/...` для остального. `{id}`, `{chat}` и `{message}` ниже заменяют подстановки из JS.

## Мессенджер

Для них есть готовые методы SDK, смотрите [справочник](API_REFERENCE.md).

| Область | Маршруты |
|---|---|
| Чаты | `/chats`, `/chats/{id}`, `/chats/unread`, `/chats/dm` |
| Настройки чата | `/chats/{id}/pin`, `/mute`, `/unread`, `/read`, `/accept` |
| Удаление чата | `/chats/{id}/delete`, `/clear-history`, `/clear-history-all` |
| Сообщения | `/chats/{id}/messages`, `/messages/{message}`, `/messages/around/{message}?limit=` |
| Удаление и пересылка | `/messages/delete-for-me`, `/messages/delete-for-all`, `/messages/forward` |
| Реакции | `/chats/{id}/messages/{message}/reactions`, `/reactions/available` |
| Голосовые | `/chats/{id}/heard`, `/chats/{id}/messages/{message}/heard` |
| Файлы | `/chats/{id}/file-links`, `/browser-file-session` |
| Загрузка файлов | `/media/upload/init`, `/media/upload/{id}/finalize` |
| Typing и статусы | `/chats/{id}/action`, `/presence`, `/presence/{id}` |
| Сообщение пользователю | `/users/{id}/messages` |

## Пользователи и профиль

`/users/{id}`, `/users/me`, `/users/me/privacy`, `/users/me/pin`, `/users/me/pins`, `/users/me/blocked`, `/users/me/restore`, `/users/profile`, `/users/check-username`, `/users/follow-status`, `/users/{id}/follow`, `/users/{id}/followers`, `/users/{id}/following`, `/users/{id}/block`, `/users/stats/top-clans`.

Файлы профиля: `/files/avatar`, `/files/banner-drawing`, `/files/upload`, `/files/{id}`, `/profile-avatar/`.

## Вход и сессии

`/api/v1/auth/` с подпутями `/refresh`, `/register`, `/verify-otp`, `/resend-otp`, `/reset-password`, `/change-password`, `/set-password`, `/logout`, `/logout-all`, `/sign-in`, `/sign-up`, `/methods`.

Телефон и почта: `/phone/start`, `/phone/sync`, `/phone/attach`, `/phone/change`, `/phone/password`, `/phone/reset-password`, `/phone/config`, `/phone/resume`, `/phone/reverse/`, `/email/attach`, `/email/change`, `/change/confirm`, `/change/pending`, `/change/proof`, `/change/wait`.

Сессии: `/v1/auth/sessions`, `/v1/auth/sessions/{id}`. Также найдены `/verification`, `/verification/status`, `/verification/submit`, `/captcha/page`.

## Посты, комментарии, уведомления

| Область | Маршруты |
|---|---|
| Посты | `/posts`, `/posts/{id}`, `/posts/user/{id}`, `/posts/user/{id}/liked`, `/posts/{id}/like`, `/pin`, `/repost`, `/restore`, `/poll/vote`, `/comments` |
| Комментарии | `/comments/{id}`, `/like`, `/replies`, `/restore` |
| Уведомления | `/notifications`, `/count`, `/read-all`, `/settings`, `/stream` |
| Прочее | `/search`, `/hashtags/trending`, `/reports`, `/platform/announcements`, `/platform/changelog` |

## События, магазин и подписки

Чтобы не загромождать список, перечислены только корни.

- **События:** `/event`, `/v1/event/status`, `/api/v1/event-assets/`, `/event-files/`.
- **Подписка и оплата:** `/v1/subscription/` (`/methods`, `/pay`, `/gifts`, `/bind-card`, `/auto-renewal`, `/checkout-state`, `/checkout-release`, `/international-checkouts`, `/gift-giver/`), `/checkout/handoff`, `/shop`, `/settings/subscription`.
- **Прочее:** `/v1/portal`.

## Юридические страницы и интерфейс

`/legal`, `/legal/{slug}`, `/legal/{slug}/history`, `/legal/{slug}/versions/{version}`, `/api/legal-documents?scope=social`, `/terms`, `/privacy`, `/cookies`, `/child-safety`, `/subscription-terms`, `/recurring-terms`, `/consent?flow=`, `/support`, `/delete-account`, `/onboarding`, `/messenger/entry`, `/messenger-sw.js`.

Это в основном страницы интерфейса, а не API.

## Проверка новой сборки

Запустите `python tools/check_bundle.py ПУТЬ_К_БАНДЛУ`, чтобы сравнить API
мессенджера в локальной сборке с машиночитаемой описью. [Таблица покрытия](API_COVERAGE.md) содержит
соответствия SDK, уровни уверенности и ограничения статического разбора.
