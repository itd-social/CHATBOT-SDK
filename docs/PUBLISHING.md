# Как опубликовать SDK для pip

Пакет получает имя из `pyproject.toml`: `itd-chatbot-sdk`. После публикации в PyPI разработчики смогут установить его командой:

```sh
python -m pip install itd-chatbot-sdk
```

Ниже описан процесс для репозитория [itd-social/CHATBOT-SDK](https://github.com/itd-social/CHATBOT-SDK). Публикация выполняется владельцем проекта вручную через GitHub Actions. Настройки находятся в файлах `.github/workflows/release.yml` и `.github/workflows/publish-pypi.yml`.

## 1. Загрузите исходники на GitHub

Прежде чем добавлять файлы в Git, убедитесь, что в коде нет токенов. Если при тестировании вы вставляли значение в `REFRESH_TOKEN` в `examples/refresh_test_bot.py`, удалите его. Файлы `.itd-session.json`, `.itd-refresh-session.json`, `.env`, курсоры, `.venv` и `.bundle` не должны попасть в коммит; стандартные пути уже указаны в `.gitignore`. Если вы использовали собственный путь к сессии, добавьте его в исключения.

Если локальный Git ещё не инициализирован, из каталога проекта выполните:

```sh
git init -b main
git remote add origin https://github.com/itd-social/CHATBOT-SDK.git
git status --short --ignored
```

Если Git уже настроен, проверьте `git remote -v` и не добавляйте второй `origin` без необходимости. После проверки файлов:

```sh
git add .
git diff --cached --stat
git diff --cached
git commit -m "feat: initial SDK"
git push -u origin main
```

Перед коммитом посмотрите полный `git diff --cached` и убедитесь, что в нём нет секретов. Если на GitHub уже есть коммиты, локальная и удалённая история могут разойтись: сначала согласуйте их обычным Git workflow. Не используйте принудительный push, чтобы обойти ошибку.

После загрузки SDK можно установить прямо из GitHub, без PyPI. Установленный Git должен быть доступен в терминале:

```sh
python -m pip install "git+https://github.com/itd-social/CHATBOT-SDK.git"
```

## 2. Проверьте GitHub Actions

Откройте вкладку **Actions** в репозитории и дождитесь успешного workflow **CI**. Он проверяет код и тесты на нескольких версиях Python, собирает пакет и проверяет релизную конфигурацию.

Для создания GitHub Release workflow **Release** должен иметь разрешение `contents: write`. Правила защиты `main` также должны разрешать релизному процессу отправлять коммит и тег. Если они это запрещают, настройте согласованный способ выпуска; не отключайте защиту только для обхода ошибки. Инструкции о ветках и проверках: [GIT_WORKFLOW.md](../GIT_WORKFLOW.md).

## 3. Создайте GitHub Release

1. На GitHub откройте **Actions**, выберите **Release** и нажмите **Run workflow** для ветки `main`.
2. Оставьте `publish` выключенным. Это предварительный просмотр: проверьте предлагаемую версию и changelog в журнале запуска.
3. Если всё верно, запустите **Release** ещё раз с включённым `publish`. Workflow выполнит проверки, обновит версию и `CHANGELOG.md`, создаст коммит и тег, отправит их в GitHub и создаст GitHub Release с файлами пакета.
4. Проверьте в разделе **Releases**, что тег и файлы соответствуют ожидаемой версии.

Версия определяется по тегам и Conventional Commits. Например, если это первая история без тегов и релиз не предлагается, проверьте формат коммита и вывод dry-run. Подробности, включая особенности первого релиза и защиты ветки: [RELEASING.md](../RELEASING.md).

Этот шаг ещё не публикует пакет в PyPI.

## 4. Настройте доверенную публикацию в PyPI

1. Создайте аккаунт на [PyPI](https://pypi.org/) и включите необходимые способы защиты аккаунта.
2. Проверьте доступность имени `itd-chatbot-sdk`. На момент проверки 20 ноября 2026 года запрос к проекту возвращал 404, но имя не зарезервировано и ситуация может измениться. Не загружайте пакет в чужой проект.
3. Откройте [PyPI Publishing](https://pypi.org/manage/account/publishing/) и создайте **pending publisher** для нового проекта. Укажите:

   | Поле | Значение |
   |---|---|
   | PyPI project name | `itd-chatbot-sdk` |
   | Owner | `itd-social` |
   | Repository name | `CHATBOT-SDK` |
   | Workflow name | `publish-pypi.yml` |
   | Environment name | `pypi` |

4. В настройках репозитория GitHub создайте environment с именем `pypi`. Там, где это доступно, включите обязательное подтверждение владельца перед публикацией.

Trusted Publishing позволяет GitHub Actions получить краткоживущие полномочия для загрузки пакета; постоянный PyPI API-токен в GitHub Secrets для этого сценария не нужен. Настройки владельца и доверенного издателя создаются в PyPI вручную. [Официальная инструкция Python Packaging User Guide](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/).

## 5. Опубликуйте пакет

1. В GitHub Actions выберите workflow **Publish to PyPI** и нажмите **Run workflow**.
2. Укажите тег уже опубликованного GitHub Release, например `v0.1.0`. Поддерживаются стабильные теги формата `vX.Y.Z`.
3. Workflow проверит GitHub Release, соответствие тега версии в `pyproject.toml`, тесты, сборку и метаданные, после чего отправит дистрибутивы в PyPI через Trusted Publishing.
4. Проверьте страницу проекта в PyPI и установку в новом виртуальном окружении:

```sh
python -m venv check-install
```

В Windows:

```powershell
.\check-install\Scripts\python.exe -m pip install itd-chatbot-sdk
.\check-install\Scripts\python.exe -c "from itd_bot import Bot; print('SDK установлен')"
```

В Linux/macOS:

```sh
check-install/bin/python -m pip install itd-chatbot-sdk
check-install/bin/python -c "from itd_bot import Bot; print('SDK установлен')"
```

Для следующего выпуска повторите создание GitHub Release и ручной запуск публикации с новым тегом. Опубликованную версию не следует перезаписывать: ошибки исправляются новой версией.

## Если что-то не получилось

- **PyPI сообщает, что имя занято.** Уточните права на существующий проект или выберите другое имя в `pyproject.toml` и настройках Trusted Publisher.
- **GitHub Release не может отправить коммит или тег.** Проверьте права workflow и защиту `main`; выпуск не должен обходить правила проекта без согласования.
- **PyPI отклоняет Trusted Publishing.** Сверьте имя владельца, репозитория, файла workflow и environment в PyPI и GitHub. Для нового проекта нужен pending publisher.
- **Тег не совпадает с версией.** Workflow намеренно остановится. Сверьте `project.version` в коммите тега и GitHub Release.
- **Пакет не находится через pip.** Убедитесь, что публикация завершилась успешно, а имя установлено правильно. Дождитесь появления проекта в PyPI и повторите команду.
