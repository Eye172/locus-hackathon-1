# Развёртывание CampusLense

## Рабочий сайт: Modal

**https://pip00sya--campuslense-web.modal.run** — бэкенд и собранный фронтенд в одном контейнере (`deploy/modal_app.py`), кэш профилей и превью — в
Modal Volume `campuslens-data`, один контейнер держится тёплым (`MIN_CONTAINERS=1`), чтобы у жюри не было холодного старта.

```bash
pip install modal && modal token new          # один раз, вход через браузер
cd frontend && npm run build && cd ..         # приложение, которое отдаёт контейнер
modal deploy deploy/modal_app.py              # образ: Python 3.12, torch CPU, CLIP, ffmpeg
# сохранённые профили и превью (необязательно: без них вузы собираются вживую)
modal volume put campuslens-data <slim.sqlite3> /campuslens.sqlite3 --force
modal volume put campuslens-data backend/data/thumbs /thumbs --force
```

Ключи берутся из `backend/.env` и `backend/secrets/vertex-sa.json` при деплое и хранятся как Modal Secret, в образ
они не попадают. Ключ Google Maps для браузера (`VITE_GOOGLE_MAPS_3D_KEY`) встраивается в сборку фронтенда.

## Другие варианты


## Вариант A: один Space на Hugging Face (бэкенд + фронтенд + прогретый кэш)

> С сентября 2026 Docker-Spaces даже на бесплатном CPU требуют PRO-подписку ($9/мес): `deploy_hf.py` получает
> `402 Payment Required` без неё. Static Spaces бесплатны, но бэкенду нужен Docker.

1. Токен с правом **Write**: https://huggingface.co/settings/tokens → Create new token → тип Write → скопировать.
2. Добавить в `backend/.env` строку `HF_TOKEN=hf_...` (файл в `.gitignore`).
3. `python deploy/deploy_hf.py --space campuslens` — соберёт фронтенд, соберёт папку Space, зальёт её и выставит
   секреты `GEMINI_API_KEY`, `MAPILLARY_TOKEN` из `.env`. Первая сборка образа ~10 минут.
4. Адрес приложения: `https://<логин>-campuslens.hf.space`; проверка `…/api/health`.
5. UptimeRobot на `/api/health` каждые 5 минут, чтобы Space не засыпал в дни проверки.


## Вариант B: Modal (бесплатные $30/мес, карта не нужна) — `deploy/modal_app.py`

1. `pip install modal` и один раз `modal setup` (откроется браузер, вход через GitHub/Google).
2. `modal deploy deploy/modal_app.py` — образ с torch + CLIP собирается ~5 мин, дальше стабильный адрес
   вида `https://<логин>--campuslens-web.modal.run` (бэкенд + фронтенд, один адрес).
3. Кэш профилей и превью: `modal volume put campuslens-data backend/data/campuslens.sqlite3 /campuslens.sqlite3`
   и `modal volume put campuslens-data backend/data/thumbs /thumbs`.
4. На дни проверки поднять `MIN_CONTAINERS=1` в `deploy/modal_app.py`, чтобы не было холодного старта (~30 с).

## Вариант C: ноутбук + туннель (бесплатно, но компьютер должен работать)

Фронтенд на Vercel, бэкенд локально: `ngrok http 8000 --domain <ваш-статичный>.ngrok-free.app`, в `vercel.json`
подставить этот адрес в rewrite `/api/*` и `/s/*`. Запросы из приложения идут с заголовком, обходящим заставку ngrok.

## Backend → Hugging Face Spaces (Docker) — детали варианта A

1. Создать Space: тип **Docker**, hardware CPU basic (2 vCPU, 16 ГБ RAM).
2. В корень Space положить содержимое `backend/` (Dockerfile уже настроен на порт 7860 и пользователя `user`).
   Файл `README.md` Space должен начинаться с:
   ```
   ---
   title: CampusLense API
   sdk: docker
   app_port: 7860
   ---
   ```
3. Secrets Space: `GEMINI_API_KEY` (опц.), `MAPILLARY_TOKEN`, `FLICKR_API_KEY`, `GOOGLE_MAPS_API_KEY` (опц.).
   Переменная `FRONTEND_ORIGIN=https://<проект>.vercel.app` — чтобы страницы шаринга `/s/{qid}` перенаправляли в приложение.
4. Persistent storage (если доступно) для `data/` — иначе кэш профилей живёт до рестарта; прогрев можно запускать
   после старта: `python scripts/prewarm.py` внутри контейнера.
5. Проверка: `https://<space>.hf.space/api/health` → `{"ok": true, "clip_ready": true}`.
6. UptimeRobot: HTTP-монитор на `/api/health` каждые 5 минут, чтобы Space не засыпал в дни проверки.

Запасной вариант: Railway (Docker из `backend/`, порт из `$PORT` — добавить `--port ${PORT:-7860}` в CMD).

## Frontend → Vercel

1. Импортировать репозиторий, Root Directory = `frontend`, Framework = Vite.
2. В `frontend/vercel.json` заменить `YOUR-SPACE.hf.space` на адрес Space (rewrite `/api/*` → backend, поэтому
   `VITE_API_BASE` можно оставить пустым).
3. Env: `VITE_GOOGLE_MAPS_KEY`, `VITE_MAPILLARY_TOKEN` (опц.).
4. Build: `npm run build`, Output: `dist`.

## Один контейнер (альтернатива)

`npm run build` в `frontend/`, затем backend раздаёт `frontend/dist` сам (см. `app/main.py`, монтирование `/assets` и SPA-роут).
Собрать образ из корня с `COPY frontend/dist ./frontend/dist`.

## Перед сабмитом

- Все ссылки открываются в приватном окне без входа и оплаты.
- В репозитории нет `.env`, ключей и `data/thumbs`.
- `main` зафиксирован до 19.09 12:00 по Астане; после дедлайна только восстановление доступа.
