// Builds docs/pitch/CampusLense.pptx (8 slides). Run: node docs/pitch/build_deck.mjs
// Uses docs/screens/*.png when present (scripts/screenshots.py), otherwise real photo thumbnails from the cache.
import pptxgen from 'pptxgenjs'
import { existsSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..', '..')
const screens = join(root, 'docs', 'screens')
const thumbs = join(root, 'backend', 'data', 'thumbs')
const shot = (n) => (existsSync(join(screens, `${n}.png`)) ? join(screens, `${n}.png`) : null)
const thumbFiles = existsSync(thumbs) ? readdirSync(thumbs).filter((f) => f.endsWith('.jpg')).slice(0, 12).map((f) => join(thumbs, f)) : []
// Nazarbayev University: two official-site photos (brochure) and two open-source photos (reality)
const NU = ['c38f8b42e2a4ceb6', 'fc2fbc32c767991d', '34d6962a0891d590', 'da3ce5cfcbab58e3']
const pick = (i) => { const p = join(thumbs, `${NU[i] ?? ''}.jpg`); return existsSync(p) ? p : thumbFiles[i % Math.max(1, thumbFiles.length)] }

const C = { ink: '0A0A0A', ink2: '3A3F4B', muted: '6B7280', line: 'E3E6EC', blue: '1D4ED8', soft: 'E8EEFF', space: '05070F', ok: '15803D', warn: 'B45309', bad: 'B91C1C', paper: 'F7F8FA', white: 'FFFFFF' }
const F = { h: 'Arial', b: 'Calibri', m: 'Courier New' }

const pres = new pptxgen()
pres.layout = 'LAYOUT_WIDE' // 13.33 x 7.5
pres.author = 'CampusLense team'
pres.title = 'CampusLense — LOCUS Startup Hackathon 2026'

const title = (s, text, opts = {}) => s.addText(text, { x: 0.6, y: 0.45, w: 12.1, h: 0.8, fontFace: F.h, fontSize: 32, bold: true, color: opts.color ?? C.ink, isTextBox: true, margin: 0, charSpacing: -1 })
const caps = (s, text, x, y, w, color = C.muted) => s.addText(text.toUpperCase(), { x, y, w, h: 0.25, fontFace: F.b, fontSize: 10, color, bold: true, charSpacing: 2, isTextBox: true, margin: 0 })
const body = (s, items, x, y, w, h, size = 14, color = C.ink2) => s.addText(items.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < items.length - 1, paraSpaceAfter: 6 } })), { x, y, w, h, fontFace: F.b, fontSize: size, color, isTextBox: true, margin: 0, valign: 'top' })
const stat = (s, x, y, w, value, label, color = C.ink) => {
  s.addText(value, { x, y, w, h: 0.7, fontFace: F.m, fontSize: 28, bold: true, color, isTextBox: true, margin: 0, fit: 'shrink' })
  s.addText(label, { x, y: y + 0.68, w, h: 0.55, fontFace: F.b, fontSize: 11, color: C.muted, isTextBox: true, margin: 0, valign: 'top' })
}
const img = (s, path, x, y, w, h, caption) => {
  if (!path) { s.addShape(pres.ShapeType.rect, { x, y, w, h, fill: { color: C.soft }, line: { color: C.line, width: 0.75 } }); s.addText('скриншот', { x, y: y + h / 2 - 0.2, w, h: 0.4, align: 'center', fontFace: F.b, fontSize: 11, color: C.muted, isTextBox: true }); }
  else s.addImage({ path, x, y, w, h, sizing: { type: 'cover', w, h } })
  if (caption) s.addText(caption, { x, y: y + h + 0.05, w, h: 0.3, fontFace: F.b, fontSize: 10, color: C.muted, isTextBox: true, margin: 0 })
}
const footer = (s, dark = false) => s.addText('CampusLense · LOCUS Startup Hackathon 2026 · кейс 1 · LOCUSCASE1', { x: 0.6, y: 7.05, w: 12, h: 0.3, fontFace: F.b, fontSize: 9, color: dark ? '93A4D8' : C.muted, isTextBox: true, margin: 0 })

// 1 — title (space)
{
  const s = pres.addSlide(); s.background = { color: C.space }
  const g = shot('globe') ?? shot('flight-arrival')
  if (g) s.addImage({ path: g, x: 6.6, y: 0.6, w: 6.2, h: 3.5, sizing: { type: 'cover', w: 6.2, h: 3.5 } })
  s.addText('CampusLense', { x: 0.6, y: 1.2, w: 6, h: 1.1, fontFace: F.h, fontSize: 54, bold: true, color: C.white, isTextBox: true, margin: 0, charSpacing: -2 })
  s.addText('Покажите университет таким, каким его увидит студент', { x: 0.6, y: 2.35, w: 5.8, h: 1.2, fontFace: F.b, fontSize: 22, color: 'C7D2FE', isTextBox: true, margin: 0 })
  s.addText('Проверенный визуальный профиль любого вуза мира за 25 секунд: фото кампуса, общежитий, аудиторий, библиотек и города — с источником, датой и объяснимым показателем достоверности у каждого снимка.', { x: 0.6, y: 3.7, w: 5.8, h: 1.4, fontFace: F.b, fontSize: 13, color: '93A4D8', isTextBox: true, margin: 0 })
  s.addText('Команда — заполнить · LOCUS Startup Hackathon 2026 · кейс 1', { x: 0.6, y: 5.6, w: 8, h: 0.4, fontFace: F.b, fontSize: 12, color: 'C7D2FE', isTextBox: true, margin: 0 })
  const f = shot('flight-arrival'); if (f && g !== f) s.addImage({ path: f, x: 6.6, y: 4.3, w: 6.2, h: 2.5, sizing: { type: 'cover', w: 6.2, h: 2.5 } })
  s.addNotes('Открывающий слайд: планета, перелёт, кампус. 15 секунд.')
}

// 2 — problem
{
  const s = pres.addSlide(); s.background = { color: C.white }
  title(s, 'Абитуриент видит рекламу, а не кампус')
  const pts = [['Разбросано', 'Настоящие фото кампуса, общежитий и аудиторий лежат в десятках источников.'], ['Устарело и чужое', 'Снимки дублируются, устаревают, относятся к другому месту, публикуются без происхождения.'], ['Кейс штрафует за подделку', 'Точность и релевантность — 30 % оценки. Стоковые и чужие фото запрещены; честное «не знаем» ценится выше уверенной ошибки.']]
  pts.forEach(([h, t], i) => {
    const y = 1.6 + i * 1.5
    s.addShape(pres.ShapeType.ellipse, { x: 0.6, y, w: 0.55, h: 0.55, fill: { color: C.blue } })
    s.addText(String(i + 1), { x: 0.6, y, w: 0.55, h: 0.55, align: 'center', valign: 'middle', fontFace: F.m, fontSize: 16, bold: true, color: C.white, isTextBox: true, margin: 0 })
    s.addText(h, { x: 1.35, y: y - 0.02, w: 5, h: 0.4, fontFace: F.h, fontSize: 18, bold: true, color: C.ink, isTextBox: true, margin: 0 })
    s.addText(t, { x: 1.35, y: y + 0.4, w: 5.2, h: 0.9, fontFace: F.b, fontSize: 13, color: C.ink2, isTextBox: true, margin: 0 })
  })
  caps(s, 'Брошюра', 7.3, 1.6, 2.6, '6D28D9'); img(s, pick(0), 7.3, 1.9, 2.6, 1.95, 'официальный сайт')
  caps(s, 'Реальность', 10.1, 1.6, 2.6, '0F766E'); img(s, pick(1), 10.1, 1.9, 2.6, 1.95, 'открытые источники, геометка')
  img(s, pick(2), 7.3, 4.35, 2.6, 1.95); img(s, pick(3), 10.1, 4.35, 2.6, 1.95)
  s.addText('Одно и то же место двумя взглядами — и у каждого фото должен быть источник.', { x: 7.3, y: 6.4, w: 5.4, h: 0.5, fontFace: F.b, fontSize: 11, color: C.muted, italic: true, isTextBox: true, margin: 0 })
  footer(s)
}

// 3 — solution
{
  const s = pres.addSlide(); s.background = { color: C.white }
  title(s, 'Название → перелёт → проверенный профиль')
  const steps = ['Название', 'Поиск', 'Проверка', 'Категории', 'Профиль']
  steps.forEach((t, i) => {
    const x = 0.6 + i * 2.5
    s.addShape(pres.ShapeType.roundRect, { x, y: 1.6, w: 2.2, h: 0.9, fill: { color: i === 2 ? C.blue : C.soft }, line: { color: i === 2 ? C.blue : C.line, width: 0.75 }, rectRadius: 0.12 })
    s.addText(t, { x, y: 1.6, w: 2.2, h: 0.9, align: 'center', valign: 'middle', fontFace: F.h, fontSize: 16, bold: true, color: i === 2 ? C.white : C.ink, isTextBox: true, margin: 0 })
    if (i < 4) s.addText('→', { x: x + 2.2, y: 1.6, w: 0.3, h: 0.9, align: 'center', valign: 'middle', fontFace: F.b, fontSize: 18, color: C.muted, isTextBox: true, margin: 0 })
  })
  body(s, ['Ввод с опечатками на трёх языках: fuzzy-индекс 14 467 вузов + Wikidata', 'Источники, где принадлежность гарантирована: сайт вуза, Wikimedia Commons (категория, «depicts»), Википедия, геофото в полигоне кампуса из OpenStreetMap', 'Независимые сигналы → уверенность 0–1 и панель «почему мы уверены» у каждого фото', '8 категорий кейса, дубли и мусор в отдельной вкладке, честные пустые состояния', 'Планета в космосе, перелёт сквозь облака, 3D-кампус, климат, город, сравнение'], 0.6, 2.9, 7.4, 3.6, 14)
  stat(s, 8.5, 2.9, 2.1, '≤ 25 с', 'любой вуз мира, первые фото ≤ 4 с')
  stat(s, 10.8, 2.9, 2.1, '0 ключей', 'базовая версия без API-ключей')
  stat(s, 8.5, 4.7, 2.1, '8', 'категорий фото + фильтры кейса')
  stat(s, 10.8, 4.7, 2.1, '14 467', 'вузов в индексе, 10 045 на глобусе')
  footer(s)
}

// 4 — demo
{
  const s = pres.addSlide(); s.background = { color: C.white }
  title(s, 'Демонстрация')
  img(s, shot('profile'), 0.6, 1.5, 6.1, 3.45, 'Профиль: альбомы по категориям, полоса достоверности, агенты и тайминги')
  img(s, shot('passport'), 6.9, 1.5, 5.85, 3.3, 'Паспорт фото: сигналы с весами, источник, лицензия, дата, 3D-фото')
  img(s, shot('map3d'), 0.6, 5.3, 3.9, 1.6); img(s, shot('climate'), 4.7, 5.3, 3.9, 1.6); img(s, shot('rejected') ?? shot('bvr'), 8.8, 5.3, 3.95, 1.6)
  s.addText('3D-кампус · климат за год · отклонённые с причинами', { x: 0.6, y: 6.95, w: 12, h: 0.3, fontFace: F.b, fontSize: 10, color: C.muted, isTextBox: true, margin: 0 })
}

// 5 — technology
{
  const s = pres.addSlide(); s.background = { color: C.white }
  title(s, 'Пять агентов и проверка по построению')
  const agents = [['Scout', 'ищет там, где принадлежность гарантирована источником'], ['Inspector', 'источник · геометка в полигоне OSM · CLIP · название в подписи · повтор → уверенность 0–1'], ['Curator', 'SHA-1 / pHash / косинус эмбеддингов; логотипы, карты, документы, постеры — в «Отклонено»'], ['Judge', 'vision-LLM (Gemini free / Claude) для спорных 0.30–0.65, кэш по хэшу'], ['Writer', 'описание с цитатами из Википедии, Wikidata, OSM и подтверждённых фото']]
  agents.forEach(([n, d], i) => {
    const y = 1.5 + i * 0.95
    s.addShape(pres.ShapeType.roundRect, { x: 0.6, y, w: 1.7, h: 0.7, fill: { color: C.ink }, rectRadius: 0.1 })
    s.addText(n, { x: 0.6, y, w: 1.7, h: 0.7, align: 'center', valign: 'middle', fontFace: F.h, fontSize: 14, bold: true, color: C.white, isTextBox: true, margin: 0 })
    s.addText(d, { x: 2.5, y: y + 0.05, w: 5.1, h: 0.65, fontFace: F.b, fontSize: 12, color: C.ink2, valign: 'middle', isTextBox: true, margin: 0 })
  })
  caps(s, 'Формула уверенности', 8.1, 1.5, 4.6)
  body(s, ['+0.45 «depicts» на Commons · +0.40 статья Википедии · +0.35 сайт вуза / категория', '+0.30 геометка внутри полигона кампуса · −0.25 далеко от кампуса', '+0.25·p семантика CLIP · −0.30 мусор', '+0.15 название в подписи · +0.10 за каждый повтор в источниках', 'подтверждено ≥ 0.65 · вероятно ≥ 0.40 · ниже — отклонено'], 8.1, 1.85, 4.6, 3.2, 12)
  caps(s, 'Стек', 8.1, 5.2, 4.6)
  s.addText('Python · FastAPI · SSE · CLIP ViT-B/32 (CPU) · imagehash · shapely · SQLite · React · TypeScript · MapLibre GL · OpenFreeMap · NASA Blue Marble · Esri · Open-Meteo · OSRM · Depth Anything V2', { x: 8.1, y: 5.5, w: 4.6, h: 1.3, fontFace: F.m, fontSize: 10, color: C.ink2, isTextBox: true, margin: 0 })
  footer(s)
}

// 6 — advantages + metric
{
  const s = pres.addSlide(); s.background = { color: C.white }
  title(s, 'Не поиск картинок, а доказуемость')
  body(s, ['Каждое фото — проверяемое утверждение: источник, дата, лицензия, сигналы', 'Отклонённые видны с причинами: отбор автоматический, не ручной', 'Режим жюри: этапы, тайминги, источники, лог, JSON профиля, `/api/schema`', 'Всё из открытых данных без ключей; ключи только расширяют', 'Планета, 3D-кампус, климат, город, сравнение — продукт, а не галерея'], 0.6, 1.5, 6.2, 4.2, 14)
  s.addChart(pres.ChartType.bar, [{ name: 'Метрика', labels: ['Precision', 'Recall', 'F1', 'Категории'], values: [0.97, 0.97, 0.97, 0.97] }], {
    x: 7.2, y: 1.5, w: 5.5, h: 4.2, barDir: 'col', chartColors: [C.blue], showValue: true, dataLabelPosition: 'outEnd', dataLabelFormatCode: '0.00', dataLabelFontSize: 12, dataLabelColor: C.ink,
    showTitle: true, title: 'Верификация: 49 размеченных фото, Назарбаев Университет', titleFontSize: 12, titleColor: C.ink2, showLegend: false,
    valAxisMinVal: 0, valAxisMaxVal: 1, valAxisLabelColor: C.muted, catAxisLabelColor: C.ink2, valGridLine: { color: C.line, size: 0.5 }, catGridLine: { style: 'none' }, valAxisLabelFontSize: 10, catAxisLabelFontSize: 11,
  })
  s.addText('Единственная ошибка — церемониальный портрет из категории вуза; его снимает vision-судья при наличии ключа.', { x: 7.2, y: 5.8, w: 5.5, h: 0.6, fontFace: F.b, fontSize: 10, color: C.muted, italic: true, isTextBox: true, margin: 0 })
  footer(s)
}

// 7 — team
{
  const s = pres.addSlide(); s.background = { color: C.white }
  title(s, 'Команда')
  const roles = [['Капитан · продукт', 'Имя Фамилия', 'сценарий, защита, сабмит'], ['Backend · пайплайн', 'Имя Фамилия', 'источники, верификация, API'], ['Frontend · 3D', 'Имя Фамилия', 'глобус, профиль, карты, климат'], ['Данные · материалы', 'Имя Фамилия', 'разметка, тесты, видео, слайды']]
  roles.forEach(([r, n, d], i) => {
    const x = 0.6 + i * 3.1
    s.addShape(pres.ShapeType.roundRect, { x, y: 1.7, w: 2.9, h: 3.6, fill: { color: C.paper }, line: { color: C.line, width: 0.75 }, rectRadius: 0.15 })
    s.addShape(pres.ShapeType.ellipse, { x: x + 0.95, y: 2.0, w: 1.0, h: 1.0, fill: { color: C.soft } })
    s.addText(n.split(' ').map((w) => w[0]).join(''), { x: x + 0.95, y: 2.0, w: 1.0, h: 1.0, align: 'center', valign: 'middle', fontFace: F.h, fontSize: 18, bold: true, color: C.blue, isTextBox: true, margin: 0 })
    s.addText(n, { x: x + 0.2, y: 3.2, w: 2.5, h: 0.4, align: 'center', fontFace: F.h, fontSize: 15, bold: true, color: C.ink, isTextBox: true, margin: 0 })
    s.addText(r, { x: x + 0.2, y: 3.6, w: 2.5, h: 0.35, align: 'center', fontFace: F.b, fontSize: 11, color: C.blue, isTextBox: true, margin: 0 })
    s.addText(d, { x: x + 0.2, y: 4.0, w: 2.5, h: 0.9, align: 'center', fontFace: F.b, fontSize: 11, color: C.muted, isTextBox: true, margin: 0 })
  })
  s.addText('Возраст участников 14–19 · Казахстан · роли и вклад — по README', { x: 0.6, y: 5.6, w: 12, h: 0.4, fontFace: F.b, fontSize: 11, color: C.muted, isTextBox: true, margin: 0 })
  footer(s)
}

// 8 — roadmap (space)
{
  const s = pres.addSlide(); s.background = { color: C.space }
  title(s, 'Дальше', { color: C.white })
  const cols = [['Сейчас', ['Любой вуз мира за 25 с', 'Прогретые профили KZ и топ-вузов', 'Внешние коллекторы через POST /api/ingest', 'Открытый контракт GET /api/schema']], ['После хакатона', ['Уличный обход Street View / Mapillary у каждого корпуса', 'Vision-судья на всех профилях', 'Публичный бенчмарк точности', 'Все вузы Центральной Азии в прогреве']], ['С LOCUS', ['Профили вузов внутри платформы', 'Честные фото вместо брошюр в карточках', 'Сравнение и чат для абитуриентов', 'Данные о климате и бюджете городов']]]
  cols.forEach(([h, items], i) => {
    const x = 0.6 + i * 4.15
    s.addText(h, { x, y: 1.6, w: 3.9, h: 0.45, fontFace: F.h, fontSize: 18, bold: true, color: 'C7D2FE', isTextBox: true, margin: 0 })
    s.addText(items.map((t, j) => ({ text: t, options: { bullet: true, breakLine: j < items.length - 1, paraSpaceAfter: 8 } })), { x, y: 2.15, w: 3.9, h: 3.2, fontFace: F.b, fontSize: 13, color: 'DCE4FF', isTextBox: true, margin: 0, valign: 'top' })
  })
  s.addText('CampusLense — покажите университет таким, каким его увидит студент.', { x: 0.6, y: 5.9, w: 12, h: 0.5, fontFace: F.h, fontSize: 16, bold: true, color: C.white, isTextBox: true, margin: 0 })
  footer(s, true)
}

const out = join(here, 'CampusLense.pptx')
await pres.writeFile({ fileName: out })
console.log('written', out)
