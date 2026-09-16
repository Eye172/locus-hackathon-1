# Деплой

## Backend → Hugging Face Spaces (Docker, бесплатный CPU)

1. Создать Space: тип **Docker**, hardware CPU basic (2 vCPU, 16 ГБ RAM).
2. В корень Space положить содержимое `backend/` (Dockerfile уже настроен на порт 7860 и пользователя `user`).
   Файл `README.md` Space должен начинаться с:
   ```
   ---
   title: CampusLens API
   sdk: docker
   app_port: 7860
   ---
   ```
3. Secrets Space: `GEMINI_API_KEY` (опц.), `MAPILLARY_TOKEN`, `FLICKR_API_KEY`, `GOOGLE_MAPS_API_KEY` (опц.).
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
