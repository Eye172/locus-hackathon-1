import { test } from 'node:test'
import assert from 'node:assert/strict'
import { streamAdvisor, streamProfile } from '../src/lib/api.ts'

test('chat handles CRLF split across chunks and finishes once', async () => {
  const original = globalThis.fetch
  const tokens: string[] = [], errors: string[] = []
  let done = 0
  globalThis.fetch = async () => new Response(new ReadableStream({ start(controller) {
    for (const part of ['event: token\r', '\ndata: {"text":"Привет"}\r\n\r', '\nevent: done\r\ndata: {"provider":"test"}\r\n\r\n']) controller.enqueue(new TextEncoder().encode(part))
    controller.close()
  } }))
  try {
    await streamAdvisor({ a: 'Q1', b: 'Q2', lang: 'ru', messages: [] }, (t) => tokens.push(t), () => { done++ }, (e) => errors.push(e))
    assert.deepEqual(tokens, ['Привет']); assert.equal(done, 1); assert.deepEqual(errors, [])
  } finally { globalThis.fetch = original }
})

test('truncated chat stream reports an error instead of leaving typing active', async () => {
  const original = globalThis.fetch
  globalThis.fetch = async () => new Response('event: token\ndata: {"text":"partial"}\n\n')
  const errors: string[] = []
  try {
    await streamAdvisor({ a: 'Q1', b: 'Q2', lang: 'ru', messages: [] }, () => {}, () => assert.fail('unexpected completion'), (e) => errors.push(e))
    assert.equal(errors.length, 1)
  } finally { globalThis.fetch = original }
})

test('profile connection error while EventSource reconnects is surfaced and closed', () => {
  const original = globalThis.EventSource
  let instance: FakeEventSource
  class FakeEventSource {
    readyState = 0 // CONNECTING is the usual state on a network error
    closed = false
    listeners = new Map<string, (e: object) => void>()
    constructor() { instance = this }
    addEventListener(name: string, fn: (e: object) => void) { this.listeners.set(name, fn) }
    close() { this.closed = true }
  }
  globalThis.EventSource = FakeEventSource as unknown as typeof EventSource
  const errors: string[] = []
  try {
    streamProfile('Q1', false, { onError: (e) => errors.push(e) })
    instance!.listeners.get('error')!({})
    assert.equal(errors.length, 1)
    assert.equal(instance!.closed, true)
  } finally { globalThis.EventSource = original }
})
