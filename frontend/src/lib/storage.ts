/** Shared validation for browser storage and portable backups. */
export const KEYS = {
  favorites: 'campuslens.favorites', saved: 'campuslens.saved',
  visitPlan: 'campuslens.visitPlan', lang: 'campuslens.lang',
} as const

export interface SavedProfile { name: string; city?: string | null; savedAt: string; photos: number }
export interface PlanItem { id: string; label: string; done: boolean; photoId?: string }
type BrowserStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const text = (v: unknown): v is string => typeof v === 'string'
const safeKey = (k: string) => /^[A-Za-z0-9_-]+$/.test(k) && !['__proto__', 'constructor', 'prototype'].includes(k)
const validators: Record<string, (v: unknown) => boolean> = {
  [KEYS.saved]: (v) => object(v) && text(v.name) && text(v.savedAt) && Number.isFinite(Date.parse(v.savedAt)) &&
    typeof v.photos === 'number' && Number.isFinite(v.photos) && v.photos >= 0 && (v.city == null || text(v.city)),
  [KEYS.favorites]: (v) => Array.isArray(v) && v.every(text),
  [KEYS.visitPlan]: (v) => object(v) && Array.isArray(v.items) && v.items.every((i) => object(i) && text(i.id) && text(i.label) && typeof i.done === 'boolean' && (i.photoId === undefined || text(i.photoId))),
}

export function parseLang(value: unknown): 'ru' | 'en' | 'kk' {
  // Accept older backups that accidentally JSON-encoded the language twice.
  if (typeof value === 'string' && value.startsWith('"')) { try { value = JSON.parse(value) } catch { /* fallback */ } }
  return value === 'en' || value === 'kk' ? value : 'ru'
}

export function readRecord<T>(storage: Pick<Storage, 'getItem'>, key: string): Record<string, T> {
  try {
    const value: unknown = JSON.parse(storage.getItem(key) ?? '{}')
    if (!object(value)) return {}
    return Object.fromEntries(Object.entries(value).filter(([k, v]) => safeKey(k) && validators[key]?.(v))) as Record<string, T>
  } catch { return {} }
}

export function exportBackup(storage: BrowserStorage): string {
  return JSON.stringify({
    [KEYS.saved]: readRecord(storage, KEYS.saved),
    [KEYS.favorites]: readRecord(storage, KEYS.favorites),
    [KEYS.visitPlan]: readRecord(storage, KEYS.visitPlan),
    [KEYS.lang]: parseLang(storage.getItem(KEYS.lang)),
  }, null, 2)
}

export function importBackup(raw: string, storage: BrowserStorage): void {
  if (raw.length > 5_000_000) throw new Error('Backup too large')
  const data: unknown = JSON.parse(raw)
  if (!object(data)) throw new Error('Invalid backup')
  const changes = new Map<string, string>()
  for (const key of Object.values(KEYS)) {
    const value = data[key]
    if (value == null) continue // older exports used null for empty sections
    if (key === KEYS.lang) {
      if (typeof value !== 'string' || !['ru', 'en', 'kk', '"ru"', '"en"', '"kk"'].includes(value)) throw new Error('Invalid language')
      changes.set(key, parseLang(value))
    } else {
      if (!object(value) || !Object.entries(value).every(([k, v]) => safeKey(k) && validators[key](v))) throw new Error('Invalid backup data')
      changes.set(key, JSON.stringify({ ...readRecord(storage, key), ...value }))
    }
  }
  if (!changes.size) throw new Error('No CampusLense data')
  // Validate everything before writing; restore earlier keys on quota/storage failure.
  const before = new Map([...changes.keys()].map((k) => [k, storage.getItem(k)]))
  const written: string[] = []
  try {
    for (const [key, value] of changes) { storage.setItem(key, value); written.push(key) }
  } catch (error) {
    for (const key of written.reverse()) {
      const value = before.get(key)
      try { if (value == null) storage.removeItem(key); else storage.setItem(key, value) } catch { /* storage was disabled */ }
    }
    throw error
  }
}
