# Google Photos Sync

**English:** [README.md](../README.md) | **Русский:** этот файл

Выгрузка **альбомов Google Photos** (включая те, что шарили с вами) в оригинальном качестве. Python, локальный SQLite, вход через cookies браузера. Работает на **Linux**, Windows и macOS.

Это **неофициальный** клиент, не связанный с Google. Он вызывает тот же внутренний `batchexecute`, что и сайт [photos.google.com](https://photos.google.com/). Официальный Photos Library API с марта 2025 года больше не умеет читать библиотеку и shared-альбомы.

Используйте на свой риск. В [условиях использования Google](https://policies.google.com/terms?hl=ru) запрещён автоматизированный доступ к контенту в обход машинных инструкций вроде [photos.google.com/robots.txt](https://photos.google.com/robots.txt) (там закрыты `/albums`, `/photo/`, `/shared` и похожие пути). За существенное или повторное нарушение Google может приостановить аккаунт; см. [почему аккаунты отключают](https://support.google.com/accounts/answer/40695?hl=ru).

## Что умеет

- Все альбомы, в том числе shared. Одна и та же фотография в двух альбомах сохраняется **дважды** (во второй раз копируется с диска, без повторной загрузки).
- Фото и видео, в том числе те, где вы не владелец.
- Оригинал: URL "Download original" из веб-UI (аналог Shift+D), не транскод API.
- Имя файла как у оригинала (из метаданных Google или `Content-Disposition`).
- Дата файла = дата съёмки из Google Photos (mtime; на Windows ещё и creation time).
- SQLite со статусами `pending` / `downloaded` / `failed` - можно останавливать и продолжать большие альбомы.

## Установка

Нужен Python 3.11+.

**Linux / macOS**

```bash
cd google-photos-sync
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
```

На Linux Chromium часто требует системные библиотеки. Если окно не открывается:

```bash
playwright install-deps chromium
```

`install-deps` обычно просит `sudo` (Debian/Ubuntu и аналоги).

**Windows (PowerShell)**

```powershell
cd google-photos-sync
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
playwright install chromium
```

После активации venv команда `gpsync` доступна в этом терминале.

## Быстрый старт

```bash
gpsync init --output ~/Photos/GooglePhotos
gpsync login
gpsync enumerate
gpsync download --limit 5          # пробная пачка
gpsync status
gpsync download                    # всё оставшееся
```

На Windows путь может быть, например, `D:\Photos\GooglePhotos`.

Структура на диске:

```
~/Photos/GooglePhotos/
  albums/
    Отпуск 2024/
      IMG_1234.HEIC
      clip.mp4
    Shared trip/
      photo_from_friend.jpg
```

Состояние лежит в `./gpsync-data/` (`cookies.txt`, `photos.db`, `config.json`, профиль Chromium). Этот каталог в git не коммитится - там сессия Google.

## Вход

### Вариант 1 - окно Chromium (проще)

Нужна графическая сессия (обычный рабочий стол). По SSH без X11/Wayland и на headless-сервере используйте вариант 2.

```bash
gpsync login
```

Откроется Chromium. Войдите в Google, дождитесь своей ленты Photos, вернитесь в терминал и нажмите Enter. Cookies сохранятся в `gpsync-data/cookies.txt`.

Сессия Google живёт недолго. Если пойдёт редирект на логин:

```bash
gpsync login
gpsync download --failed-only
```

### Вариант 2 - cookies.txt из вашего браузера

Предпочтительнее вариант 1: расширение браузера - чужой код, этот репозиторий его не поставляет и не проверяет.

1. Расширение [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc).
2. Расширению нужна работа в режиме инкогнито (Chrome: `chrome://extensions` -> детали расширения -> "Разрешить в режиме инкогнито").
3. Откройте **новое окно инкогнито**, войдите в Google, откройте [photos.google.com](https://photos.google.com/).
4. Откройте пустую вкладку, **закройте вкладку Photos**.
5. Export All Cookies -> сохраните `cookies.txt`.
6. Закройте инкогнито (чтобы эта сессия больше не была открыта в браузере - иначе Google быстрее её инвалидирует).

```bash
gpsync login --cookies ~/Downloads/cookies.txt
gpsync whoami
```

На Windows файл чаще лежит в `C:\Users\you\Downloads\cookies.txt`. Импорт оставляет только cookies доменов Google.

Если в cookies несколько аккаунтов (`photos.google.com/u/1/`):

```bash
gpsync --account-index 1 whoami
```

`cookies.txt` - это пароль. Не коммитьте его и не шарьте. Сбросить сессию, не трогая базу и скачанные файлы:

```bash
gpsync logout
```

## Команды

| Команда | Зачем |
|---|---|
| `gpsync init --output PATH` | Куда складывать файлы |
| `gpsync login` | Войти через Chromium |
| `gpsync login --cookies FILE` | Импорт Netscape cookies |
| `gpsync logout` | Удалить cookies и профиль Chromium; SQLite и файлы оставить |
| `gpsync logout -y` | То же без вопроса |
| `gpsync whoami` | Проверить, что сессия жива |
| `gpsync enumerate` | Собрать альбомы и элементы в SQLite |
| `gpsync enumerate --library` | Плюс основная лента Library (и archived) |
| `gpsync enumerate --album "Отпуск"` | Только альбомы с таким фрагментом названия |
| `gpsync enumerate --refresh` | Пересканировать уже обойденные альбомы (новые фото подхватятся, статусы скачивания не сбросятся) |
| `gpsync download` | Скачать `pending` / `failed` |
| `gpsync download --workers 3` | Параллельные HTTP-загрузки (не ставьте слишком много) |
| `gpsync download --failed-only` | Только ошибки |
| `gpsync download --limit 20` | Ограничить пачку |
| `gpsync download --max-size 50M` | Только файлы не больше 50MiB |
| `gpsync download --min-size 50M` | Только файлы не меньше 50MiB |
| `gpsync download --folder 10` | Альбом с таким номером в `gpsync status` (спросит Y/N) |
| `gpsync download --folder 1,4-6,10` | Несколько альбомов: запятые, диапазоны `N-M`, без пробелов, максимум 10 |
| `gpsync download -y --folder 10` | То же без вопроса |
| `gpsync download --ignore-folder 1,2,4` | Не качать эти альбомы только в этом запуске (номера из `gpsync status`) |
| `gpsync ignore --folder 1,2,4` | Навсегда пропускать эти альбомы при `gpsync download` |
| `gpsync ignore -y --folder 1,2,4` | То же без вопроса |
| `gpsync unignore --folder 1,2` | Снять флаг ignore с этих альбомов |
| `gpsync unignore --all` | Снять флаг ignore со всех альбомов |
| `gpsync download --date-prefix` | Имена: папка `YYYY.MM.DD ...`, файл `YYYY.MM.DD HH.MM ...` (по времени съёмки) |
| `gpsync download --pause 3` | Пауза 3 секунды между стартом каждого файла |
| `gpsync download --pause 1-5` | Случайная пауза 1-5 секунд между стартами |
| `gpsync download --fix-time` | Проставить даты съёмки уже скачанным файлам (без HTTP) |
| `gpsync download --fix-time --folder 1,4` | То же только для этих альбомов |
| `gpsync status` | Таблица альбомов: `#`, имя, shared, скачано/всего, ошибки, ignore, дата enumerate |
| `gpsync verify --reset` | Пропавшие с диска файлы снова станут `pending` |

Пути по умолчанию: `--data-dir gpsync-data`, `--output-dir photos`.

`gpsync status` рисует таблицу. Колонка `#` - номер для `--folder` / `--ignore-folder` / `gpsync ignore`. Можно `1,4,9`, `2-5` или `1,4-6,10` (без пробелов). После раскрытия диапазонов не больше 10 альбомов. Имена альбомов в кавычках, `done/total` - скачано / всего в базе, `ignore` - `yes` если альбом помечен через `gpsync ignore`, `scanned` - время последнего `enumerate`.

`gpsync download` без `--folder` / `--album` пропускает альбомы с постоянным ignore. `--folder` или `--album` качают их всё равно. `--ignore-folder` пропускает альбомы только в этом запуске и SQLite не меняет. Можно вместе с `--folder`: `gpsync download --folder 1-10 --ignore-folder 3,5`.

`--date-prefix` по умолчанию выключен. Для файлов время идёт как `HH.MM`, а не `HH:MM`: двоеточие нельзя в имени файла на Windows. Дата папки - съёмка самого раннего файла в альбоме.

`--min-size` / `--max-size` понимают `50M`, `100KB`, `1.5G` (1024). Файлы, которые не подошли, остаются `pending` - их можно выгрузить другим фильтром.

`--pause` разносит момент старта каждого файла. Если `--workers` больше 1, загрузки всё ещё могут идти параллельно; для паузы после каждого файла: `--workers 1 --pause 1-5`.

`--fix-time` ничего не качает. Берёт уже скачанные строки из SQLite и ставит mtime (на Windows ещё creation time) по дате съёмки из enumerate. Путь берётся из `local_path` в базе (и companion у Live Photo), поэтому файлы, скачанные с `--date-prefix`, находятся. `--folder` / `--album` / `--ignore-folder` ограничивают альбомы; `--pause`, `--workers`, фильтры размера и `--date-prefix` игнорируются. Альбомы с постоянным ignore пропускаются, если не указаны `--folder` или `--album`.

**Ctrl+C** останавливает `gpsync download` и `gpsync enumerate`. Уже записанные файлы и SQLite остаются; незавершённая загрузка сбрасывается и элемент остаётся `pending`. Повторите ту же команду, чтобы продолжить.

Во время `gpsync download` справа в строке альбома могут быть буквы воркеров:

| Буква | Значение |
|---|---|
| `d` | HTTP-загрузка |
| `s` | пауза (`--pause`) |
| `i` | запрос метаданных элемента |
| `c` | копия с диска (тот же файл уже есть в другом альбоме) |
| `z` | распаковка zip от Google |
| `!` | ошибка |

Когда альбом закончен, вместо букв пишется `done`.

## Как это устроено

1. **enumerate** дергает те же RPC, что UI: список альбомов (`Z5xsfc`), shared links (`F2A0H`), страницы альбома (`snAcKc`, пагинация как при скролле), опционально лента (`lcxiM`).
2. В SQLite: `albums`, `media`, `album_items` (статус по паре альбом+фото).
3. **download** для каждого элемента берёт `downloadOriginalUrl` (`VrseUb`) и качает HTTP-ом с вашими cookies. Live Photo: ещё и `.mov`. ZIP от Google распаковывается. Дата файла выставляется после записи.

Протокол неофициальный. Google может сменить RPC без предупреждения. Поэтому паузы и мало воркеров по умолчанию.

## Ограничения

- Locked Folder (PIN) не выгружается.
- Partner sharing ("с партнёром") без отдельного альбома - только если фото уже в Library: `gpsync enumerate --library`.
- Имена RPC и формат ответов могут сломаться после обновления Photos.
- Очень большие видео качаются дольше; при обрыве файл помечается `failed`, повторите `--failed-only`.

## Безопасность и зависимости

Cookies и профиль Chromium живут только в `gpsync-data/` (в `.gitignore`). Подробности: [SECURITY.md](../SECURITY.md).

Прямые зависимости - три пакета с PyPI с **закреплёнными версиями** в `pyproject.toml`. Код xob0t **не ставится** через pip; используются только перенесённые RPC-оффсеты (MIT).

## Разработка

```bash
pip install -e ".[dev]"
pytest
```

Сторонние наработки - [CREDITS.md](../CREDITS.md). Лицензия - [LICENSE](../LICENSE). Upstream MIT xob0t - [third_party/xob0t-LICENSE](../third_party/xob0t-LICENSE).
