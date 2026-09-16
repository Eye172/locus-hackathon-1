# Архитектура CampusLens

Рабочее название продукта: **CampusLens** — «покажи университет таким, каким его увидит студент».

## 1. Принцип

Три тезиса, на которых стоит всё решение:

1. **Принадлежность доказывается по построению, а не по догадке.** Фото берутся из источников, где связь с вузом гарантирована самим источником: официальный сайт, категория и structured-data «depicts» на Wikimedia Commons, статьи Википедии, объекты внутри полигона кампуса из OpenStreetMap, геопривязанные снимки Mapillary/Flickr/Places. Модель компьютерного зрения используется как дополнительный фильтр, а не как единственный судья.
2. **Неопределённость видна.** У каждого фото — уверенность от 0 до 1 из независимых сигналов, панель «почему мы уверены», вкладка отклонённых с причинами, индикатор покрытия по категориям.
3. **Пайплайн прозрачен.** Этапы, тайминги и статусы источников стримятся в интерфейс; режим жюри показывает сырые логи. Это одновременно и UX скорости, и доказательство отсутствия ручного подбора.

## 2. Схема пайплайна

```
 ввод названия
      │
      ▼
 [1] RESOLVE ─── офлайн-индекс (rapidfuzz) + Wikidata wbsearchentities (ru/en/kk)
      │            → кандидаты «Вы имели в виду» → QID
      ▼
 [2] ENRICH (параллельно, ≤5 с)
      ├─ Wikidata entity: labels, aliases, сайт, Commons-категория, город, страна, год, студенты
      ├─ Wikipedia summary (ru→en→kk): описание, координаты-fallback, превью
      ├─ Overpass: полигон кампуса + здания с тегами; fallback — радиус 500 м
      └─ Город: координаты центра (P625 города) → расстояние
      ▼
 [3] COLLECT кандидатов (параллельно, таймаут 6 с на источник)
      ├─ official   : главная + до 4 подстраниц (кампус/общежитие/галерея)
      ├─ commons    : категория · depicts(P180) · геопоиск в bbox · файлы статей Википедии
      ├─ city       : файлы статьи о городе + категория города на Commons
      ├─ mapillary  : снимки в bbox полигона          (если есть токен)
      ├─ flickr     : геофото в bbox                   (если есть ключ)
      └─ places     : фото сущности Google Places      (если есть ключ)
      ▼
 [4] FETCH  до 90 кандидатов, 10 параллельно, 5 с на файл; ресайз до 512 px; EXIF-дата; сохранение превью
      ▼
 [5] ANALYZE
      ├─ pHash → точные копии (Hamming ≤ 8) → слияние, sources_count++
      ├─ CLIP ViT-B/32 → эмбеддинги; косинус ≥ 0.93 → визуально похожие (скрыть в кластер)
      ├─ Zero-shot: 8 категорий + 8 классов мусора
      ├─ Приоры: тег здания OSM (гео в 40 м), ключевые слова в подписи/URL (ru/kk/en)
      └─ Уверенность = Σ сигналов → уровень verified / likely / unverified
      ▼
 [6] ASSEMBLE  категории, покрытие, таймлайн, «брошюра vs реальность», прогулка, описание с цитатами
      ▼
 [7] CONTEXT (параллельно с [3]) климат Open-Meteo, остановки Overpass, расстояние до центра
      ▼
 SSE-события → фронтенд; профиль → SQLite-кэш; превью → data/thumbs
```

**Бюджет времени:** мягкий дедлайн 25 с. По истечении отдаётся всё готовое с флагом `partial=true`, недоделанные источники помечаются `skipped`, фоновая задача дописывает кэш.

## 3. Формула уверенности

`confidence = clamp(Σ weights, 0, 1)`

| Сигнал | Ключ | Вес | Условие |
|---|---|---|---|
| Источник: «depicts» на Commons | `src_depicts` | +0.45 | structured data P180 = QID вуза |
| Источник: статья Википедии о вузе | `src_wikipedia` | +0.40 | файл используется в статье ru/en/kk |
| Источник: официальный сайт | `src_official` | +0.35 | домен из Wikidata P856 |
| Источник: категория Commons | `src_commons_cat` | +0.35 | P373 |
| Источник: Mapillary в полигоне | `src_mapillary` | +0.40 | геопривязка по построению |
| Источник: Google Places | `src_places` | +0.35 | фото привязано к place |
| Источник: Flickr геофото | `src_flickr` | +0.20 | |
| Источник: геопоиск Commons | `src_commons_geo` | +0.20 | |
| Геометка внутри полигона | `geo_inside` | +0.30 | shapely `contains` |
| Геометка в буфере 300 м | `geo_near` | +0.10 | |
| Геометка далеко (> 1 км) | `geo_far` | −0.25 | |
| Семантика CLIP | `clip_match` | +0.25·p | p — вероятность лучшей категории (не мусор) |
| Похоже на мусор | `clip_junk` | −0.30 | p(junk) > 0.5 и выше любой категории → отклонение |
| Название/алиас в подписи, alt, заголовке страницы | `name_match` | +0.15 | нормализованное совпадение любого алиаса |
| Найдено в N источниках | `cross_source` | +0.10·(N−1), max +0.20 | по pHash |
| Есть дата | `has_date` | +0.03 | |
| Флаг пользователя «не этот вуз» | `user_flag` | −0.50 | из таблицы flags |

Уровни: `verified` ≥ 0.65 · `likely` 0.40–0.65 · `unverified` < 0.40 (уходит во вкладку «Отклонено» с причиной «низкая уверенность»).

Для категории **«город»** цель проверки — город, а не вуз: источники — статья Википедии о городе и категория города на Commons (`src_city_article` +0.45, `src_city_cat` +0.35), геосигналы считаются относительно центра города (радиус 15 км).

## 4. Категории и промпты CLIP

| Категория | Ключ | Фильтр кейса | Промпты (усредняются) |
|---|---|---|---|
| Кампус | `campus` | — | university campus building exterior · main building of a university · campus grounds with walkways |
| Общежитие | `dormitory` | «общежитие» | student dormitory building · dorm room with beds and desks · student residence hall |
| Аудитории | `classroom` | — | lecture hall with rows of seats · classroom with desks and whiteboard · students in an auditorium |
| Библиотека | `library` | — | library reading room with bookshelves · students studying in a library |
| Лаборатории | `lab` | «лаборатории» | science laboratory with equipment · students in a computer lab · research lab with instruments |
| Спорт | `sports` | «спорт» | sports stadium · gym or fitness hall · swimming pool · students playing sports on a field |
| Студенческая жизнь | `student_life` | «студенческая жизнь» | students at a campus event · group of students celebrating · student festival on campus · students in a cafeteria |
| Город | `city` | — | city street with buildings · city skyline · city park or landmark · mountains and city view |

Мусор: `a logo` · `a map` · `a screenshot of a website` · `a document with text` · `a poster or banner with text` · `a close-up portrait of a person` · `a diagram or chart` · `a certificate or award`.

Приоры: тег здания OSM → +0.25 к соответствующей категории; ключевые слова (`жатақхана|общежит|dorm|hostel` → dormitory, `кітапхана|библиотек|library` → library, `зертхана|лаборатор|lab` → lab, `спорт|стадион|sport|gym|stadium` → sports, `аудитор|lecture|classroom|дәріс` → classroom) → +0.20.

## 5. Модель данных (Pydantic)

```
Candidate   { qid, label, description, city, country, logo_url, score, origin: index|wikidata }
University  { qid, name, names{ru,en,kk}, aliases[], description, website, commons_category,
              wikipedia{lang:title}, lat, lon, coord_source, city, city_qid, city_lat, city_lon,
              country, founded, students, logo_url, image }
Campus      { osm_type, osm_id, polygon[[lat,lon]]|null, bbox[minlat,minlon,maxlat,maxlon],
              mode: polygon|radius, buildings[Building] }
Building    { osm_id, name, name_en, kind: dormitory|library|sports|academic|student_life|other, lat, lon }
Signal      { key, label, weight, value }
Photo       { id, url, page_url, thumb, width, height, source, source_label, title, author, license,
              date, date_source, lat, lon, geo_inside, building, category, category_scores{}, secondary,
              confidence, level, signals[], phash, similar[PhotoRef], sources_count, is_brochure,
              rejected, reject_reason, outdated }
Stage       { key, label, status: running|done|skipped|error, ms, detail, count }
Profile     { university, campus, photos[], rejected[], categories{key: {verified, likely, rejected, sources_checked[]}},
              coverage{overall, per_category}, description{mode, sentences[{text, sources[]}], sources[]},
              context{distance_km, center_name, transport_stops, climate{jan, jul}}, walk[], timeline{year:count},
              brochure_vs_reality{category: {brochure[], reality[]}}, stages[], sources_status{},
              generated_at, elapsed_ms, partial }
```

## 6. API

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/search?q=` | кандидаты для «Вы имели в виду» (индекс + Wikidata) |
| GET | `/api/profile/{qid}/stream?refresh=0` | SSE: события `stage`, `university`, `photos`, `profile`, `error` |
| GET | `/api/profile/{qid}` | профиль из кэша (404 если нет) |
| POST | `/api/profile/{qid}/refresh` | пересборка профиля |
| POST | `/api/flag` | `{qid, photo_id, reason}` — флаг «не этот вуз» |
| GET | `/api/compare?a=&b=` | два профиля |
| GET | `/api/thumb/{photo_id}.jpg` | превью из локального кэша |
| GET | `/api/health`, `/api/sources` | состояние сервиса и ключей |
| GET | `/api/recent` | последние собранные профили |

## 7. Стек

- **Backend:** Python 3.12, FastAPI, httpx (async), sse-starlette, open_clip (ViT-B/32 laion2b), imagehash, Pillow, shapely, rapidfuzz, BeautifulSoup/lxml, SQLite (aiosqlite), anthropic SDK (опционально).
- **Frontend:** Vite + React 19 + TypeScript, Tailwind CSS v4, react-router, Leaflet/react-leaflet, lucide-react, EventSource для SSE.
- **Деплой:** backend — Hugging Face Spaces (Docker, CPU, 16 ГБ RAM, бесплатно) с пингом UptimeRobot, чтобы не засыпал; frontend — Vercel. Локальный запуск — `uvicorn` + `vite`.

## 8. Структура репозитория

```
campuslens/
├── README.md                     # задача, решение, стек, запуск, тестовый сценарий, роли, источники, ограничения
├── docs/
│   ├── RESEARCH.md               # исследование источников с живыми замерами
│   ├── ARCHITECTURE.md           # этот файл
│   ├── PLAN.md                   # план на 72 часа
│   ├── TECH_NOTE.md              # техническая справка для сабмита
│   ├── DEMO_SCRIPT.md            # сценарий демо-видео (3 мин) и защиты (5 мин)
│   └── PITCH_OUTLINE.md          # структура 8 слайдов
├── backend/
│   ├── app/
│   │   ├── main.py               # FastAPI, маршруты, SSE
│   │   ├── config.py             # настройки и ключи из .env
│   │   ├── models.py             # Pydantic-схемы
│   │   ├── cache.py              # SQLite: профили, флаги, недавние
│   │   ├── http.py               # общий httpx-клиент, UA, SSL-fallback, таймауты
│   │   ├── geo.py                # гаверсинус, полигоны, bbox
│   │   └── pipeline/
│   │       ├── orchestrator.py   # этапы, бюджет времени, события
│   │       ├── resolve.py        # индекс + Wikidata → кандидаты
│   │       ├── enrich.py         # entity, Wikipedia, город
│   │       ├── fetch.py          # загрузка и ресайз изображений, EXIF
│   │       ├── vision.py         # CLIP: эмбеддинги, zero-shot, мусор
│   │       ├── dedup.py          # pHash + косинус, кластеры
│   │       ├── verify.py         # сигналы → уверенность → уровень
│   │       ├── categorize.py     # категории с приорами
│   │       ├── describe.py       # описание с цитатами (LLM / шаблон)
│   │       ├── context.py        # климат, транспорт, расстояние
│   │       └── sources/
│   │           ├── wikidata.py · wikipedia.py · commons.py · osm.py
│   │           ├── official_site.py · mapillary.py · flickr.py · places.py
│   ├── data/
│   │   ├── universities.json     # офлайн-индекс (генерируется скриптом)
│   │   ├── labels.json           # ручная разметка для eval
│   │   └── thumbs/               # кэш превью (в .gitignore)
│   ├── scripts/
│   │   ├── build_university_index.py
│   │   ├── prewarm.py            # прогрев кэша для списка вузов
│   │   └── eval.py               # precision/recall верификации
│   ├── tests/
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
└── frontend/
    ├── src/
    │   ├── main.tsx · App.tsx · index.css
    │   ├── lib/  api.ts (SSE-клиент) · types.ts · i18n.ts · format.ts
    │   ├── pages/ Home.tsx · Profile.tsx · Compare.tsx
    │   └── components/
    │       SearchBox · CandidateList · PipelineTimeline · CoverageMeter · CategoryTabs
    │       PhotoGrid · PhotoCard · PhotoPassport · ConfidenceBadge · BrochureVsReality
    │       CampusMap · WalkStrip · TimelineSlider · Description · RejectedTab · JudgePanel
    │       CompareView · EmptyState · LanguageSwitch
    ├── index.html · vite.config.ts · package.json
    └── vercel.json
```

## 9. Известные ограничения (честно в README)

- Покрытие Commons/Mapillary/Flickr для небольших региональных вузов может быть слабым → индикатор покрытия и пустые состояния вместо стоковых фото.
- Некоторые сайты вузов отдают неполную цепочку SSL → повтор без проверки сертификата только для чтения публичных страниц и картинок.
- Без ключей Mapillary/Flickr/Places соответствующие источники отключены и показаны как «нет ключа».
- Без ключа Claude описание собирается шаблоном из фактов с цитатами.
- Overpass — общий публичный сервис, при перегрузке этап полигона пропускается и включается режим радиуса.
