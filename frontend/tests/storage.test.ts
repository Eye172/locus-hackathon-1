import { test } from 'node:test'
import assert from 'node:assert/strict'
import { exportBackup, importBackup, KEYS, parseLang, readRecord } from '../src/lib/storage.ts'

function storage() {
  const data = new Map<string, string>()
  return { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => { data.set(k, v) }, removeItem: (k: string) => { data.delete(k) } }
}
const saved = { name: 'MIT', city: 'Cambridge', savedAt: '2026-09-19T00:00:00Z', photos: 199 }

test('backup round trip includes raw language, favorites, saved profiles and completed plan', () => {
  const source = storage(), destination = storage()
  source.setItem(KEYS.lang, 'kk')
  source.setItem(KEYS.saved, JSON.stringify({ Q49108: saved }))
  source.setItem(KEYS.favorites, JSON.stringify({ Q49108: ['photo-1'] }))
  source.setItem(KEYS.visitPlan, JSON.stringify({ Q49108: { items: [{ id: 'one', label: 'Library', done: true, photoId: 'photo-1' }] } }))
  destination.setItem(KEYS.saved, JSON.stringify({ Q2783344: { ...saved, name: 'NU' } }))
  importBackup(exportBackup(source), destination)
  assert.equal(destination.getItem(KEYS.lang), 'kk')
  assert.deepEqual(JSON.parse(destination.getItem(KEYS.saved)!), { Q49108: saved, Q2783344: { ...saved, name: 'NU' } })
  for (const key of [KEYS.favorites, KEYS.visitPlan]) assert.equal(destination.getItem(key), source.getItem(key))
})

test('invalid imports leave existing data unchanged and never write unrelated keys', () => {
  const dest = storage()
  dest.setItem(KEYS.saved, JSON.stringify({ Q49108: saved }))
  const before = exportBackup(dest)
  for (const input of ['null', '[]', '{', '{}', JSON.stringify({ [KEYS.saved]: { Q1: { name: 'broken' } } }), JSON.stringify({ [KEYS.favorites]: { Q1: 'wrong' } }), JSON.stringify({ [KEYS.visitPlan]: { Q1: { items: [null] } } }), JSON.stringify({ [KEYS.lang]: 'invalid' })]) {
    assert.throws(() => importBackup(input, dest))
    assert.equal(exportBackup(dest), before)
  }
  importBackup(JSON.stringify({ unrelated: 'overwrite', [KEYS.lang]: 'en' }), dest)
  assert.throws(() => importBackup(JSON.stringify({ [KEYS.lang]: ['en'] }), dest))
  assert.equal(dest.getItem('unrelated'), null)
  assert.equal(dest.getItem(KEYS.lang), 'en')
})

test('quota failure restores keys already written', () => {
  const dest = storage()
  dest.setItem(KEYS.saved, JSON.stringify({ Q49108: saved }))
  const before = exportBackup(dest)
  const set = dest.setItem
  dest.setItem = (k, v) => { if (k === KEYS.lang) throw new Error('QuotaExceededError'); set(k, v) }
  assert.throws(() => importBackup(JSON.stringify({ [KEYS.saved]: { Q2783344: saved }, [KEYS.lang]: 'en' }), dest))
  assert.equal(exportBackup(dest), before)
})

test('corrupt stored records are ignored without losing valid neighbors', () => {
  const dest = storage()
  for (const value of ['null', '[]', '"text"', '{']) {
    dest.setItem(KEYS.saved, value)
    assert.deepEqual(readRecord(dest, KEYS.saved), {})
  }
  dest.setItem(KEYS.saved, JSON.stringify({ Q49108: saved, Q2: { name: 'invalid' } }))
  assert.deepEqual(readRecord(dest, KEYS.saved), { Q49108: saved })
  dest.setItem(KEYS.favorites, '{"__proto__": [], "Q49108": ["x"]}')
  assert.deepEqual(readRecord(dest, KEYS.favorites), { Q49108: ['x'] })
  assert.deepEqual(readRecord({ getItem() { throw new Error('denied') } }, KEYS.saved), {})
  assert.equal(parseLang('"en"'), 'en')
  assert.equal(parseLang('unknown'), 'ru')
})
