# Парсер транскриптов

Извлекает субтитры/транскрипты с видео (1000+ сайтов через yt-dlp):
авторские субтитры → автосубтитры → Whisper API (OpenAI) → «нет транскриптов».

## Запуск

```bash
python3 -m venv --system-site-packages .venv   # уже создан в этом репо
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # вписать OPENAI_API_KEY (опционально, для Whisper)
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Открыть http://127.0.0.1:8000 — вставить ссылки, получить транскрипты,
смотреть с таймкодами, скачать .srt / .txt / ZIP.

## Тесты

```bash
.venv/bin/python -m pytest -v
```

## Как это работает

- `app/transcription.py` — цепочка источников и кэш.
- `app/vtt.py` — разбор VTT/SRT, удаление roll-up дублей автосубтитров.
- `app/whisper_client.py` — OpenAI Whisper API (только как резерв).
- `app/batch.py` — пакетная обработка до 5 ссылок параллельно.
- `data/` — кэш транскриптов (JSON), попадает в .gitignore.

Требуется `ffmpeg` в PATH (конвертация аудио перед Whisper).
