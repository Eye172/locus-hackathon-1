/**
 * localStorage state shared with any other part of the project (e.g. a Next.js app on the same origin).
 * Keys and shapes are documented in docs/INTEGRATION.md. Every access is wrapped: private mode, blocked
 * storage or thumbnail capture must never break the page.
 */
import { useSyncExternalStore } from 'react'
import { KEYS, readRecord, type SavedProfile, type PlanItem } from './storage.ts'
export { KEYS, type SavedProfile, type PlanItem } from './storage.ts'

function read<T>(key: string, fallback: T): T {
  try { return readRecord(localStorage, key) as T } catch { return fallback }
}
function write(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* storage unavailable */ }
}

const listeners = new Set<() => void>()
const notify = () => listeners.forEach((f) => f())
let version = 0
function bump() { version++; notify() }
// another tab (or the Next.js part on this origin) changed a key: the pages re-read it
try { window.addEventListener('storage', (e) => { if (!e.key || e.key.startsWith('campuslens.')) bump() }) } catch { /* no window */ }

// one parse per key and change: a profile's photo tiles each ask for the favourites, hundreds of times per render
const parsed = new Map<string, { v: number; value: unknown }>()
function cached<T>(key: string, fallback: T): T {
  const c = parsed.get(key)
  if (c && c.v === version) return c.value as T
  const value = read(key, fallback)
  parsed.set(key, { v: version, value })
  return value
}

const subscribe = (cb: () => void) => { listeners.add(cb); return () => { listeners.delete(cb) } }
const getVersion = () => version
const getServerVersion = () => 0
export function useStoreVersion() {
  return useSyncExternalStore(subscribe, getVersion, getServerVersion)
}

export const store = {
  favorites(qid: string): string[] { return cached<Record<string, string[]>>(KEYS.favorites, {})[qid] ?? [] },
  toggleFavorite(qid: string, photoId: string) {
    const all = read<Record<string, string[]>>(KEYS.favorites, {})
    const list = new Set(all[qid] ?? [])
    if (list.has(photoId)) list.delete(photoId); else list.add(photoId)
    all[qid] = [...list]
    write(KEYS.favorites, all); bump()
  },
  saved(): Record<string, SavedProfile> { return cached(KEYS.saved, {}) },
  // writers start from a fresh read: the cached object may be held by a page that is rendering
  save(qid: string, p: SavedProfile) { const all = read<Record<string, SavedProfile>>(KEYS.saved, {}); all[qid] = p; write(KEYS.saved, all); bump() },
  unsave(qid: string) { const all = read<Record<string, SavedProfile>>(KEYS.saved, {}); delete all[qid]; write(KEYS.saved, all); bump() },
  // a name saved under an older naming rule («Стэнфордский университет» -> Stanford University)
  rename(qid: string, name: string) { const all = read<Record<string, SavedProfile>>(KEYS.saved, {}); if (all[qid] && name && all[qid].name !== name) { all[qid] = { ...all[qid], name }; write(KEYS.saved, all); bump() } },
  plan(qid: string): PlanItem[] { return cached<Record<string, { items: PlanItem[] }>>(KEYS.visitPlan, {})[qid]?.items ?? [] },
  addPlan(qid: string, label: string, photoId?: string) {
    const all = read<Record<string, { items: PlanItem[] }>>(KEYS.visitPlan, {})
    const items = all[qid]?.items ?? []
    if (photoId && items.some((i) => i.photoId === photoId)) return
    items.push({ id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, label, done: false, photoId })
    all[qid] = { items }; write(KEYS.visitPlan, all); bump()
  },
  togglePlan(qid: string, id: string) {
    const all = read<Record<string, { items: PlanItem[] }>>(KEYS.visitPlan, {})
    const items = all[qid]?.items ?? []
    all[qid] = { items: items.map((i) => (i.id === id ? { ...i, done: !i.done } : i)) }
    write(KEYS.visitPlan, all); bump()
  },
  removePlan(qid: string, id: string) {
    const all = read<Record<string, { items: PlanItem[] }>>(KEYS.visitPlan, {})
    all[qid] = { items: (all[qid]?.items ?? []).filter((i) => i.id !== id) }
    write(KEYS.visitPlan, all); bump()
  },
}
