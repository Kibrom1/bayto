import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSessionEvents } from './useSessionEvents'

class FakeEventSource {
  static last: FakeEventSource
  onopen: ((e: Event) => void) | null = null
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  close = vi.fn()
  listeners: Record<string, (e: MessageEvent) => void> = {}
  url: string
  constructor(url: string) {
    this.url = url
    FakeEventSource.last = this
  }
  addEventListener(name: string, fn: (e: MessageEvent) => void) {
    this.listeners[name] = fn
  }
}

const msg = (id: string, from = 'architect') => ({ data: JSON.stringify({ message_id: id, from, body: id }) }) as MessageEvent

beforeEach(() => vi.stubGlobal('EventSource', FakeEventSource))
afterEach(() => vi.unstubAllGlobals())

describe('useSessionEvents', () => {
  it('asks the server to replay the stored transcript', () => {
    renderHook(() => useSessionEvents('s1'))
    expect(FakeEventSource.last.url).toBe('/sessions/s1/events?since_seq=0')
  })

  it('ignores a message it already has (a reconnect can replay one)', () => {
    const { result } = renderHook(() => useSessionEvents('s1'))
    act(() => {
      FakeEventSource.last.onmessage?.(msg('m1'))
      FakeEventSource.last.onmessage?.(msg('m2'))
      FakeEventSource.last.onmessage?.(msg('m1'))
    })
    expect(result.current.messages.map((m) => m.message_id)).toEqual(['m1', 'm2'])
  })

  it('closes the stream on the server\'s end event so the browser does not reconnect forever', () => {
    const { result } = renderHook(() => useSessionEvents('s1'))
    act(() => FakeEventSource.last.onopen?.(new Event('open')))
    expect(result.current.connected).toBe(true)
    act(() => FakeEventSource.last.listeners['end']?.({} as MessageEvent))
    expect(FakeEventSource.last.close).toHaveBeenCalled()
    expect(result.current.connected).toBe(false)
  })
})

describe('useSessionEvents reconnecting', () => {
  it('reopens the stream when the status changes (Pause then Resume), resuming after the last seq', () => {
    const { result, rerender } = renderHook(({ status }) => useSessionEvents('s1', status), { initialProps: { status: 'active' } })
    const first = FakeEventSource.last
    act(() => {
      first.onmessage?.({ data: JSON.stringify({ message_id: 'm1', seq: 4, from: 'a', body: 'hi' }) } as MessageEvent)
      first.listeners['end']?.({} as MessageEvent) // paused: the runner ended, so the stream ended
    })
    expect(first.close).toHaveBeenCalled()
    rerender({ status: 'needs_human' })
    rerender({ status: 'active' }) // resumed
    expect(FakeEventSource.last).not.toBe(first)
    expect(FakeEventSource.last.url).toBe('/sessions/s1/events?since_seq=4')
    expect(result.current.messages).toHaveLength(1) // what we had is kept
  })

  it('does not reconnect when the stream ends but the status has not changed', () => {
    const { rerender } = renderHook(({ status }) => useSessionEvents('s1', status), { initialProps: { status: 'active' } })
    const first = FakeEventSource.last
    act(() => first.listeners['end']?.({} as MessageEvent))
    rerender({ status: 'active' }) // a stale poll says the same thing again
    expect(FakeEventSource.last).toBe(first)
  })
})
