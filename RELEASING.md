# Релизы

Версия лежит в `pyproject.toml`. Выпуск делает [Python Semantic Release](https://python-semantic-release.readthedocs.io/): он считает версию по Conventional Commits, обновляет `CHANGELOG.md`, создаёт коммит и тег, собирает пакет и делает GitHub Release. Настройки в `pyproject.toml`, лицензия проекта Apache License 2.0.

Автоматического выпуска при каждом push нет: релиз запускают вручную.

## Перед релизом

- Вы на `main`, `origin/main` свежий, незакоммиченных изменений нет.
- Изменения прошли PR и CI, [чеклист](dev.md#чеклист-изменения-sdk) выполнен.
- В Git нет refresh-токенов, файлов сессий, личных сообщений и бандла.
- Есть `origin`, полная история и теги. Workflow **Release** должен иметь право создавать релизы и пушить коммиты и теги (защита `main` может это запрещать).
- Версия в `pyproject.toml` согласована с тегами. Особенно важно перед первым релизом: инструмент берёт прошлую версию из тегов, а не из `project.version`.

Установите инструменты и проверьте проект:

```sh
python -m pip install -e ".[dev,release]"
python -m ruff check itd_bot examples tests
python -m ruff format --check itd_bot examples tests
python -m pytest -q
python -m build
```

## Посмотреть план

```sh
semantic-release --noop version
```

Команда ничего не меняет: ни файлы, ни теги, ни удалённый репозиторий. Проверьте версию и список изменений. Ей нужна Git-история.

> **Windows.** Чтобы русский текст обрабатывался правильно, перед командами задайте `$env:PYTHONUTF8 = "1"` (PowerShell) или `set PYTHONUTF8=1` (cmd).

## Выпустить версию

В GitHub откройте **Actions → Release → Run workflow** на `main`.

- `publish=false` (по умолчанию) только запускает проверки и dry-run.
- `publish=true` создаёт коммит `chore(release): vX.Y.Z`, тег `vX.Y.Z`, пушит их в `origin/main`, делает GitHub Release и прикладывает wheel и sdist.

Защита ветки не пускает `GITHUB_TOKEN`? Договоритесь о разрешённом для проекта способе выпуска. Защиту ради обхода ошибки не отключайте.

## Версии и changelog

- `feat` и `feature` дают minor, `fix`, `perf`, `refactor`, `revert` дают patch. Откаты пишите как `revert: ...`: стандартное `Revert ...` не считается Conventional Commit.
- `!` или `BREAKING CHANGE:` значит несовместимое изменение. До `1.0.0` оно поднимает только minor (`major_on_zero = false`).
- Changelog строится из коммитов после прошлого тега. Маркер `<!-- version list -->` в `CHANGELOG.md` нужен инструменту, не удаляйте его.
- Вид changelog и заметок релиза задают шаблоны в `templates/`. Формат повторяет changelog [max-ui](https://github.com/max-messenger/max-ui).

| Тип коммита | Раздел | Версия |
|---|---|---|
| `feat` / `feature` | ✨ Новые возможности | minor |
| `fix` | 🐛 Исправления | patch |
| `perf` | ⚡️ Производительность | patch |
| `refactor` | ♻️ Рефакторинг | patch |
| `revert` | ⏪️ Откаты изменений | patch |
| `docs` | 📚 Документация | — |
| `style` | 💎 Стили | — |
| `test` | ✅ Тесты | — |
| `chore` | 🛠 Прочее | — |
| `build` | 📦 Сборка | — |
| `ci` | 👷 CI | — |
| `!` / `BREAKING CHANGE:` | ⚠️ Несовместимые изменения | major (до `1.0.0` — minor) |

Пустые разделы не выводятся, коммиты без распознаваемого типа в changelog не попадают. Ссылки зависят от тегов `vX.Y.Z` и URL `origin`, поэтому проверьте результат до выпуска (`--noop`) и заметки созданного GitHub Release после.

## После выпуска

Проверьте GitHub Release, тег, номера версий и оба файла из `dist/`. Нашли ошибку? Опубликованный тег не переписывайте и force push не используйте: выпустите исправляющую версию.

## PyPI

После GitHub Release PyPI сам не вызывается. Отдельный ручной workflow **Publish to PyPI** публикует выпущенный тег. Что настроить и как запустить: [docs/PUBLISHING.md](docs/PUBLISHING.md).
