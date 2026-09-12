# DEPLOY — автономный завод Shorts

## Что делает этот автопилот

Завод — пайплайн CS2-шортсов, работающий без участия оператора:

1. **Скаутинг** — поиск свежих VOD через `yt-dlp` (до 90 дней).
2. **Скоринг** — LLM-оценка (Gemini/OpenAI/Anthropic) и отбор лучших фрагментов.
3. **Нарезка** — ffmpeg: вертикальные клипы 9:16, субтитры, хуки-баннеры.
4. **Озвучка** (опционально) — Azure TTS (`SHORTS_VO_ENABLED`).
5. **Партнёрские ссылки** (опционально) — баннеры/карточки (`AFFILIATE_ENABLED`).
6. **Compliance** — автоматическая проверка контента LLM.
7. **Публикация** — YouTube / Instagram / TikTok (ключи API пока не настроены).
8. **Метрики** — SQLite-база `data/metrics.sqlite`.

---

## Способ 1: Docker-контейнер

### Быстрый старт

```bash
# 1. Склонировать репозиторий
git clone https://github.com/anomalyco/shorts-clipper.git
cd shorts-clipper

# 2. Создать .env из шаблона
cp .env.example .env
# Заполнить ключи (см. раздел Переменные окружения)

# 3. Собрать образ (ARM64 для Oracle Ampere — см. ниже)
docker build -t shorts-factory .

# 4. Запустить
docker run --rm --env-file .env -v "$(pwd)/outputs:/app/outputs" shorts-factory
```

### ARM64 (Oracle Always Free Ampere)

```bash
docker build --platform linux/arm64 -t shorts-factory .
```

> ffmpeg из `apt` доступен для arm64 в `python:3.11-slim`. Модели Whisper
> подтянутся через `pip` (CPU-only, int8).

### Docker Compose (пример)

```yaml
services:
  factory:
    build: .
    env_file: .env
    volumes:
      - ./outputs:/app/outputs
      - ./models:/app/models
      - ./data:/app/data
```

---

## Способ 2: systemd + venv (классический VPS)

### Установка одной командой

```bash
sudo bash deploy/install.sh
```

Скрипт:
- Проверяет `git`, `ffmpeg`, `python >= 3.10`.
- Клонирует репо в `$BASE_DIR` (по умолчанию `/srv/shorts-clipper`).
- Создаёт `.venv` и ставит зависимости.
- Копирует `.env.example` → `.env` (если `.env` нет).
- Устанавливает `shorts-factory.service` + `shorts-factory.timer`.
- Таймер: ежедневно в 12:00 + 5 мин после загрузки.

### Ручные команды

```bash
# Ручной запуск
sudo systemctl start shorts-factory.service

# Логи
journalctl -u shorts-factory -f

# Статус таймера
systemctl list-timers shorts-factory.timer

# Остановить автозапуск
sudo systemctl disable --now shorts-factory.timer
```

---

## Переменные окружения (.env)

Все переменные читаются из `.env` в корне проекта (или из system env).
Обозначения: ✅ — обязательно, ⚙️ — опционально.

### API ключи LLM

| Переменная | Дефолт | Описание |
|---|---|---|
| `GEMINI_API_KEY` ⚙️ | `—` | Google Gemini API |
| `OPENAI_API_KEY` ⚙️ | `—` | OpenAI API |
| `ANTHROPIC_API_KEY` ⚙️ | `—` | Anthropic API |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Локальный Ollama |
| `SHORTS_PROVIDER` | `gemini` | Дефолтный LLM-провайдер |

### Ключи API соцсетей / публикации

| Переменная | Дефолт | Описание |
|---|---|---|
| `YOUTUBE_API_KEY` ⚙️ | `—` | YouTube Data API v3 |
| `INSTAGRAM_USERNAME` ⚙️ | `—` | Instagram логин |
| `INSTAGRAM_PASSWORD` ⚙️ | `—` | Instagram пароль |
| `IG_ACCESS_TOKEN` ⚙️ | `—` | Instagram Graph API токен |
| `IG_ACCOUNT_ID` ⚙️ | `—` | Instagram Business ID |
| `TT_CLIENT_KEY` ⚙️ | `—` | TikTok Client Key |
| `TT_CLIENT_SECRET` ⚙️ | `—` | TikTok Client Secret |
| `TT_ACCESS_TOKEN` ⚙️ | `—` | TikTok Access Token |
| `TT_OPEN_ID` ⚙️ | `—` | TikTok Open ID |

### R2 / S3 хранилище

| Переменная | Дефолт | Описание |
|---|---|---|
| `R2_ACCOUNT_ID` ⚙️ | `—` | Cloudflare R2 Account ID |
| `R2_ACCESS_KEY_ID` ⚙️ | `—` | R2 Access Key |
| `R2_SECRET_ACCESS_KEY` ⚙️ | `—` | R2 Secret Key |
| `R2_BUCKET_NAME` ⚙️ | `—` | R2 Bucket |

### Whisper (STT)

| Переменная | Дефолт | Описание |
|---|---|---|
| `SHORTS_WHISPER_MODEL` | `tiny.en` | Модель Whisper |
| `SHORTS_WHISPER_DEVICE` | `cpu` | `cpu` или `cuda` |
| `SHORTS_WHISPER_COMPUTE_TYPE` | `int8` | `int8` / `float16` |
| `SHORTS_WHISPER_LANGUAGE` | `—` | Язык для Whisper |
| `SHORTS_ENABLE_GPU` | `false` | GPU-режим (меняет codec/preset/device) |

### Пути и каталоги

| Переменная | Дефолт | Описание |
|---|---|---|
| `SHORTS_MODELS_DIR` | `models` | Каталог для моделей |
| `SHORTS_OUTPUT_DIR` | `outputs` | Выходные клипы |
| `SHORTS_CACHE_DIR` | `.cache/shorts-clipper` | Кэш |
| `SHORTS_PUBLISHED_ARCHIVE` | `outputs/archive` | Архив опубликованных |
| `SHORTS_CHANNEL_CREDS_DIR` | `data/creds` | OAuth-токены каналов |
| `SHORTS_METRICS_PATH` | `data/metrics.sqlite` | SQLite метрик |
| `SHORTS_PROCESSED_VIDEOS_PATH` | `data/processed_videos.json` | Уже обработанные |
| `SHORTS_COMPLIANCE_REPORT_DIR` | `outputs/compliance` | Отчёты compliance |

### Видео / Кодеки

| Переменная | Дефолт | Описание |
|---|---|---|
| `SHORTS_VIDEO_CODEC` | `libx264` | (`h264_nvenc` при GPU) |
| `SHORTS_VIDEO_PRESET` | `ultrafast` | (`fast` при GPU) |
| `SHORTS_OUTPUT_ASPECT` | `vertical` | `vertical` / `wide` / `both` |

### Скаутинг и отбор

| Переменная | Дефолт | Описание |
|---|---|---|
| `SHORTS_SCOUT_MAX_AGE_DAYS` | `90` | Макс. возраст VOD |
| `SHORTS_CLIP_RETENTION_DAYS` | `30` | Хранение клипов |
| `SHORTS_MAX_KEEP_CLIPS` | `200` | Макс. кол-во клипов |
| `SHORTS_CLIP_MIN_SEPARATION` | `15.0` | Мин. пауза между клипами (сек) |
| `SHORTS_PROCESSED_CHECK_ENABLED` | `false` | Проверять processed_videos.json |
| `SHORTS_LOG_LEVEL` | `INFO` | Уровень логирования |

### Субтитры

| Переменная | Дефолт | Описание |
|---|---|---|
| `SHORTS_SUBTITLE_LANGS` | `ru,en` | Языки субтитров (через запятую) |
| `SHORTS_SUBTITLE_STYLE` | `default` | Стиль субтитров |

### Хуки / Баннеры

| Переменная | Дефолт | Описание |
|---|---|---|
| `SHORTS_HOOK_BANNER_ENABLED` | `true` | Баннер «WAIT FOR IT…» |
| `SHORTS_HOOK_BANNER_TEXT` | `WAIT FOR IT…` | Текст баннера |
| `SHORTS_HOOK_JUDGE_ENABLED` | `false` | LLM-оценка хука |
| `SHORTS_HOOK_MIN_SCORE` | `0.5` | Мин. оценка хука |

### Аудио-энергия / Стриминг

| Переменная | Дефолт | Описание |
|---|---|---|
| `SHORTS_STREAM_AUDIO_ENERGY` | `true` | Детекция энергии аудио |
| `SHORTS_STREAM_ENERGY_WINDOW` | `1.0` | Окно анализа (сек) |
| `SHORTS_STREAM_ENERGY_THRESHOLD` | `0.15` | Порог энергии |

### Gameplay-режим

| Переменная | Дефолт | Описание |
|---|---|---|
| `SHORTS_GAMEPLAY_MODE` | `false` | Включить gameplay-режим |
| `SHORTS_GAMEPLAY_SCAN_MAX_SECONDS` | `3600` | Макс. сканирование (сек) |
| `SHORTS_GAMEPLAY_TOP_WINDOWS` | `5` | Топ-N окон |
| `SHORTS_GAMEPLAY_MIN_LENGTH` | `12.0` | Мин. длина клипа (сек) |
| `SHORTS_GAMEPLAY_MAX_LENGTH` | `60.0` | Макс. длина клипа (сек) |
| `SHORTS_GAMEPLAY_CLUTCH_MODE` | `energy` | `energy` / `emotion` |
| `SHORTS_GAMEPLAY_CLIP_SECONDS` | `30.0` | Длительность клипа (сек) |
| `SHORTS_GAMEPLAY_MUSIC_FORWARD` | `true` | Музыка вперёд |

### BGM / Музыка

| Переменная | Дефолт | Описание |
|---|---|---|
| `SHORTS_BGM_MODE` | `off` | Режим фоновой музыки |
| `SHORTS_MUSIC_DIR` | `D:/shorts_music` | Каталог музыки |
| `SHORTS_BGM_VOLUME` | `0.30` | Громкость BGM |
| `SHORTS_PHONK_MIN_TRACKS` | `2` | Мин. phonk-треков |
| `SHORTS_PHONK_FETCH_MAX_TRACKS` | `6` | Макс. треков для фетча |
| `SHORTS_PHONK_FETCH_PAGES` | `3` | Страниц фетча |
| `SHORTS_PIXABAY_API_KEY` ⚙️ | `—` | Pixabay API |
| `SHORTS_JAMENDO_API_KEY` ⚙️ | `—` | Jamendo API |

### Партнёрки (Affiliate)

| Переменная | Дефолт | Описание |
|---|---|---|
| `AFFILIATE_ENABLED` | `false` | Включить партнёрские ссылки |
| `AFFILIATE_PARTNERS_PATH` | `affiliate_partners.json` | Файл партнёров |
| `AFFILIATE_BANNER_POSITION` | `bottom_left` | `bottom_left` / `bottom_right` / `top_left` / `top_right` |
| `SHORTS_AFFILIATE_AD_CARD` | `false` | Карточка-реклама |
| `SHORTS_AFFILIATE_AD_START_FRACTION` | `0.45` | Начало вставки (0.0–1.0) |
| `SHORTS_AFFILIATE_AD_DURATION_SEC` | `4.0` | Длительность вставки (сек) |
| `SHORTS_AFFILIATE_CTA_TEXT` | `""` | Текст CTA |

### Compliance

| Переменная | Дефолт | Описание |
|---|---|---|
| `SHORTS_COMPLIANCE_ENABLED` | `true` | Включить compliance |
| `SHORTS_COMPLIANCE_LLM` | `true` | LLM-проверка |
| `SHORTS_COMPLIANCE_FINANCE_STRICT` | `false` | Строгий финансовый фильтр |
| `SHORTS_COMPLIANCE_AUTO_DISCLAIMERS` | `true` | Авто-дисклеймеры |

### Озвучка (TTS)

| Переменная | Дефолт | Описание |
|---|---|---|
| `SHORTS_VO_ENABLED` | `false` | Включить TTS-озвучку |
| `SHORTS_VO_VOICE` | `en-US-GuyNeural` | Голос |
| `SHORTS_VO_RATE` | `+8%` | Скорость речи |

### Фабрика / Публикация

| Переменная | Дефолт | Описание |
|---|---|---|
| `SHORTS_PUBLISH_PLATFORMS` | `youtube,instagram` | Платформы (через запятую) |
| `SHORTS_PUBLISH_AT` | `—` | Время публикации (HH:MM) |
| `SHORTS_PUBLISH_INTERVAL` | `30` | Интервал между публикациями (сек) |
| `SHORTS_FACTORY_DAILY_CAP` | `6` | Лимит клипов/день |
| `SHORTS_TITLE_VARIANT` | `-1` | Вариант титула (auto) |
| `SHORTS_FACTORY_PUBLISH_HOUR_START` | `—` | Час начала окна публикации |
| `SHORTS_FACTORY_PUBLISH_HOUR_END` | `—` | Час конца окна публикации |
| `SHORTS_CHANNEL` | `—` | Имя канала (для мульти-канала) |

### Прочее

| Переменная | Дефолт | Описание |
|---|---|---|
| `SHORTS_PROXY` | `—` | HTTP-прокси |
| `YOUTUBE_API_KEY` | `—` | YouTube API (дубль для совместимости) |

---

## GitHub Actions (FACTORY_ENV secret)

Для запуска завода через GitHub Actions:

1. Создайте repository secret `FACTORY_ENV`.
2. Вставьте содержимое `.env` **целиком** (все ключи одной строкой через `\n`):

```
GEMINI_API_KEY=AIza...
YOUTUBE_API_KEY=AIza...
SHORTS_PROVIDER=gemini
...
```

3. Workflow запускается ежедневно в 06:00 UTC + вручную (`workflow_dispatch`).
4. Если `FACTORY_ENV` не задан — workflow пропускает запуск с `exit 0`.
5. По умолчанию `--limit 1` (чтобы не тратить лишнее время). Можно переопределить через UI.

---

## Проверка перед запуском

```bash
# 1. Проверить конфигурацию
python -m compileall shorts_clipper scripts -q

# 2. Доступность ffmpeg
ffmpeg -version

# 3. Тестовый прогон (1 клип, без публикации)
python scripts/multi_channel.py --limit 1

# 4. Помощь
python scripts/multi_channel.py --help
```

---

## Мониторинг

```bash
# systemd
journalctl -u shorts-factory -f           # в реальном времени
journalctl -u shorts-factory --since today # за сегодня

# Docker
docker logs -f <container_id>

# Метрики
sqlite3 data/metrics.sqlite "SELECT * FROM clips ORDER BY created_at DESC LIMIT 10;"
```

---

## Когда использовать что

| Сценарий | Рекомендация |
|---|---|
| **VPS / выделенный сервер** | systemd + venv — всегда работает, свои ресурсы, полный контроль |
| **Oracle Always Free Ampere** | Docker `--platform linux/arm64` — бесплатно, хватает для 6 клипов/день |
| **GitHub Actions** | Бесплатный крон-джоб, но лимит 6 часов/запуск, нет GPU, лимиты минут |
| **Локальная разработка** | Без автопилота: `python scripts/multi_channel.py --limit 1` |
