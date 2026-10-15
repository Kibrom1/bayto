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
