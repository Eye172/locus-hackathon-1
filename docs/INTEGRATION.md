# Интеграция с частью проекта на Next.js / Node

Этот документ описывает, как вторая часть проекта (Next.js + React + TypeScript, Node.js, Sharp + SHA-1/dHash,
Leaflet, Gemini + Zod, localStorage) стыкуется с Python-пайплайном CampusLens без переписывания ни одной из сторон.

## 1. Точки стыковки

| Что | Где | Как |
|---|---|---|
| Единый контракт данных | `GET /api/schema` | JSON Schema моделей `Profile`, `Photo`, `PhotoCandidate`, `ExternalCandidates` (pydantic). На стороне Node: `npx json-schema-to-zod` → Zod-схемы, или `json-schema-to-typescript` → типы. TypeScript-типы уже есть в `frontend/src/lib/types.ts`. |
| Приём кандидатов от Node-коллекторов | `POST /api/ingest` | Node собирает URL фото (Wikidata/Commons/что угодно) → отправляет список `PhotoCandidate` → Python скачивает, проверяет (CLIP, полигон, сигналы), категоризирует и включает в профиль. Профиль пересобирается при следующем `GET /api/profile/{qid}/stream`. |
| Совместимые хэши | поля `Photo.sha1`, `Photo.dhash`, `Photo.phash` | `sha1` — SHA-1 байтов файла. `dhash` — классический 8×8 difference hash (imagehash.dhash), тот же алгоритм, что в типовой реализации на Sharp: grayscale → resize 9×8 → сравнение соседних пикселей по строкам → 64 бита hex. Дубли, найденные любой стороной, сходятся. |
| Готовые профили | `GET /api/profile/{qid}` | Next.js-страницы могут рендерить профиль напрямую (SSR или клиент). Превью: `GET /api/thumb/{photo_id}.jpg`. |
| Живая сборка | `GET /api/profile/{qid}/stream` (SSE) | События `stage`, `university`, `campus`, `source`, `photos`, `profile`, `error`. Клиент — `frontend/src/lib/api.ts::streamProfile`, переносится в Next.js без изменений (обычный `EventSource`). |
| Поиск | `GET /api/search?q=` | fuzzy-индекс + Wikidata, три языка, опечатки, города. |
| LLM | `LLM_PROVIDER=auto|claude|gemini|none` | Один и тот же промпт и одна JSON-схема ответа для Claude и Gemini (`backend/app/pipeline/describe.py::_prompt`). Ответ валидируется pydantic (эквивалент Zod). Node-часть может вызывать Gemini сама по той же схеме `{sentences:[{text, sources:[int]}]}`. |
| localStorage | ключи ниже | Общая схема, обе части читают и пишут одинаково (при одном origin). |

## 2. Схема localStorage

| Ключ | Форма | Назначение |
|---|---|---|
| `campuslens.favorites` | `{ [qid]: string[] }` — id фото | избранные фото |
| `campuslens.saved` | `{ [qid]: { name, city, savedAt, photos } }` | сохранённые профили |
| `campuslens.visitPlan` | `{ [qid]: { items: [{ id, label, done, photoId? }] } }` | план визита на кампус |
| `campuslens.lang` | `"ru" \| "kk" \| "en"` | язык интерфейса |

Все обращения обёрнуты в `try/catch`: приватный режим и заблокированное хранилище не ломают страницу.

## 3. Пример: Node-коллектор отдаёт кандидатов

```ts
// в Next.js route handler или скрипте
const candidates = [{
  url: 'https://upload.wikimedia.org/…/800px-Foo.jpg',     // прямой URL изображения
  page_url: 'https://commons.wikimedia.org/wiki/File:Foo.jpg', // кликабельный источник
  source: 'commons_cat',           // известный источник → его вес доверия; иначе 'external' (0.15)
  title: 'Main building', text: 'Nazarbayev University main building', author: 'Someone', license: 'CC BY-SA 4.0',
  date: '2023-05-01', date_source: 'exif', lat: 51.09, lon: 71.40, collector: 'node-commons',
}]
await fetch(`${API}/api/ingest`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ qid: 'Q2783344', collector: 'node-commons', candidates }) })
// затем открыть /api/profile/Q2783344/stream — кандидаты будут проверены и попадут в профиль
```

Доверие определяется Python-стороной: имя `source` из известного списка (`official`, `commons_cat`, `commons_depicts`,
`commons_geo`, `wikipedia`, `city_article`, `city_cat`, `mapillary`, `flickr`, `places`) получает свой вес,
неизвестное — базовый 0.15. Подделать уверенность через `collector` нельзя.

## 4. Варианты сборки в один продукт

1. **Next.js как фронтенд, Python как API.** Компоненты из `frontend/src/components` — обычный React без Vite-специфики;
   переносятся в `app/` Next.js как клиентские компоненты (`'use client'`). Переменная `NEXT_PUBLIC_API_BASE` вместо `VITE_API_BASE`
   (в `lib/api.ts` одна строка). Tailwind-токены объявлены как CSS-переменные в `:root` (`--color-brand`, `--color-verified` …),
   их можно использовать из обычного CSS без Tailwind.
2. **Next.js API routes как BFF.** Route handlers проксируют `/api/*` в Python-сервис и добавляют свои источники через `/api/ingest`.
3. **Два деплоя.** Next.js на Vercel, Python на HF Spaces/Railway; CORS у Python открыт (`CORS_ORIGINS`).

## 5. Порты и переменные

| | Python backend | Vite frontend |
|---|---|---|
| dev | `uvicorn app.main:app --port 8000` | `npm run dev` (5173, proxy `/api` → 8000) |
| env | `backend/.env` (см. `.env.example`) | `VITE_API_BASE` (пусто = тот же origin) |

## 6. Что менять нельзя без согласования

- Имена полей `Photo`, `Profile`, `PhotoCandidate` (`GET /api/schema` — источник истины).
- Идентификатор фото: `sha1(url)[:16]` — стабилен между пересборками, на нём держатся флаги и избранное.
- Пороги уверенности: verified ≥ 0.65, likely ≥ 0.40 (в `backend/app/config.py`).
