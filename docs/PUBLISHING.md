# Публикация SDK

Эта страница для владельца проекта. Пакет называется `itd-chatbot-sdk` (имя берётся из `pyproject.toml`), а после публикации ставится так:

```sh
python -m pip install itd-chatbot-sdk
```

Публикация идёт вручную через GitHub Actions: настройки лежат в `.github/workflows/release.yml` и `publish-pypi.yml`. Репозиторий: [itd-social/CHATBOT-SDK](https://github.com/itd-social/CHATBOT-SDK).

## 1. Загрузите код на GitHub

Сначала убедитесь, что в проекте нет секретов:

- в `examples/refresh_test_bot.py` не осталось токена;
- файлы `.itd-session.json`, `.itd-refresh-session.json`, `.env`, курсоры, `.venv` и `.bundle` не попадут в коммит (стандартные пути уже в `.gitignore`, свои добавьте сами).

Если Git ещё не настроен:

```sh
git init -b main
git remote add origin https://github.com/itd-social/CHATBOT-SDK.git
git status --short --ignored
```

Если уже настроен, проверьте `git remote -v` и не добавляйте второй `origin`. Затем:

```sh
git add .
git diff --cached --stat
git diff --cached        # просмотрите на предмет секретов
git commit -m "feat: initial SDK"
git push -u origin main
```

Если на GitHub уже есть коммиты, сначала согласуйте историю обычным Git workflow. Принудительный push не используйте.

Уже сейчас SDK можно поставить прямо из GitHub:

```sh
python -m pip install "git+https://github.com/itd-social/CHATBOT-SDK.git"
```

## 2. Дождитесь зелёного CI

Откройте вкладку **Actions** и дождитесь успешного workflow **CI**: он гоняет тесты на нескольких версиях Python, собирает пакет и проверяет релизную конфигурацию.

Для релиза у workflow **Release** должно быть разрешение `contents: write`, а защита `main` должна позволять ему отправить коммит и тег. Если не позволяет, договоритесь о способе выпуска, а защиту не отключайте. Правила веток: [GIT_WORKFLOW.md](../GIT_WORKFLOW.md).

## 3. Создайте GitHub Release

1. **Actions → Release → Run workflow** на ветке `main`, **без** `publish`. Это предпросмотр: проверьте версию и changelog в логе.
2. Если всё верно, запустите снова с включённым `publish`. Workflow прогонит проверки, обновит версию и `CHANGELOG.md`, создаст коммит, тег и GitHub Release с файлами пакета.
3. В разделе **Releases** убедитесь, что тег и файлы те, что нужны.

Версия считается по тегам и Conventional Commits. Если релиз не предлагается, проверьте формат коммитов и вывод предпросмотра. Подробности и особенности первого релиза: [RELEASING.md](../RELEASING.md).

PyPI на этом шаге ещё не затронут.

## 4. Настройте PyPI один раз

1. Создайте аккаунт на [PyPI](https://pypi.org/) и включите защиту аккаунта.
2. Проверьте, свободно ли имя `itd-chatbot-sdk`. На 20 ноября 2026 года запрос к проекту возвращал 404, но имя не зарезервировано. Чужой проект не перезаписывайте.
3. На странице [PyPI Publishing](https://pypi.org/manage/account/publishing/) создайте **pending publisher**:

   | Поле | Значение |
   |---|---|
   | PyPI project name | `itd-chatbot-sdk` |
   | Owner | `itd-social` |
   | Repository name | `CHATBOT-SDK` |
   | Workflow name | `publish-pypi.yml` |
   | Environment name | `pypi` |

4. В настройках репозитория создайте environment `pypi` и, если доступно, включите обязательное подтверждение владельца.

Это Trusted Publishing: GitHub получает краткоживущие права на загрузку, поэтому постоянный API-токен PyPI в секретах не нужен. [Официальная инструкция](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/).

## 5. Опубликуйте

1. **Actions → Publish to PyPI → Run workflow**.
2. Укажите тег уже созданного релиза, например `v0.1.0` (подходят стабильные теги `vX.Y.Z`).
3. Workflow проверит релиз, совпадение тега с версией в `pyproject.toml`, тесты, сборку и метаданные, а затем загрузит пакет в PyPI.
4. Проверьте установку в чистом окружении:

```sh
python -m venv check-install
check-install/bin/python -m pip install itd-chatbot-sdk
check-install/bin/python -c "from itd_bot import Bot; print('SDK установлен')"
```

На Windows: `.\check-install\Scripts\python.exe` вместо `check-install/bin/python`.

Для следующей версии повторите шаги 3 и 5. Опубликованную версию не перезаписывают: ошибки исправляются новым релизом.

## Если что-то пошло не так

| Проблема | Что делать |
|---|---|
| PyPI говорит, что имя занято | Уточните права на проект или выберите другое имя в `pyproject.toml` и в настройках Trusted Publisher |
| Release не может отправить коммит или тег | Проверьте права workflow и защиту `main`, не обходя правила проекта |
| PyPI отклоняет Trusted Publishing | Сверьте владельца, репозиторий, имя workflow и environment. Для нового проекта нужен pending publisher |
| Тег не совпадает с версией | Workflow остановится намеренно. Сверьте `project.version` в коммите тега |
| `pip` не находит пакет | Убедитесь, что публикация завершилась, подождите минуту и повторите |
