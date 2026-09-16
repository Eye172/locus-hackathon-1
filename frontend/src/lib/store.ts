/**
 * localStorage state shared with any other part of the project (e.g. a Next.js app on the same origin).
 * Keys and shapes are documented in docs/INTEGRATION.md. Every access is wrapped: private mode, blocked
 * storage or thumbnail capture must never break the page.
 */
import { useSyncExternalStore } from 'react'

export const KEYS = {
  favorites: 'campuslens.favorites',   // { [qid]: string[] }  photo ids
  saved: 'campuslens.saved',           // { [qid]: { name, city, savedAt, photos } }
  visitPlan: 'campuslens.visitPlan',   // { [qid]: { items: { id, label, done, photoId? }[] } }
  lang: 'campuslens.lang',
} as const

export interface SavedProfile { name: string; city?: string | null; savedAt: string; photos: number }
export interface PlanItem { id: string; label: string; done: boolean; photoId?: string }

function read<T>(key: string, fallback: T): T {
  try { const v = localStorage.getItem(key); return v ? (JSON.parse(v) as T) : fallback } catch { return fallback }
}
function write(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* storage unavailable */ }
}

const listeners = new Set<() => void>()
const notify = () => listeners.forEach((f) => f())
let version = 0
function bump() { version++; notify() }

export function useStoreVersion() {
  return useSyncExternalStore((cb) => { listeners.add(cb); return () => listeners.delete(cb) }, () => version, () => 0)
}

export const store = {
  favorites(qid: string): string[] { return read<Record<string, string[]>>(KEYS.favorites, {})[qid] ?? [] },
  toggleFavorite(qid: string, photoId: string) {
    const all = read<Record<string, string[]>>(KEYS.favorites, {})
    const list = new Set(all[qid] ?? [])
    if (list.has(photoId)) list.delete(photoId); else list.add(photoId)
    all[qid] = [...list]
    write(KEYS.favorites, all); bump()
  },
  saved(): Record<string, SavedProfile> { return read(KEYS.saved, {}) },
  save(qid: string, p: SavedProfile) { const all = store.saved(); all[qid] = p; write(KEYS.saved, all); bump() },
  unsave(qid: string) { const all = store.saved(); delete all[qid]; write(KEYS.saved, all); bump() },
  plan(qid: string): PlanItem[] { return read<Record<string, { items: PlanItem[] }>>(KEYS.visitPlan, {})[qid]?.items ?? [] },
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
