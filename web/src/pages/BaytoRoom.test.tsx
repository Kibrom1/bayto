import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BaytoRoom, stopReasonText } from './BaytoRoom'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        session: {
          id: 'session-123',
          task_id: 'task-1',
          mode_id: 'debate',
          status: 'running',
          started_at: '2026-09-30T12:00:00Z',
          ended_at: null,
          round: 1,
          budget: { max_tokens: 25000, max_cost: 25 },
          usage: { tokens_in: 12000, tokens_out: 3000, cost: 4.5 },
          turn_counts: [],
        },
      }),
      {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      },
    ),
  ))

  class FakeEventSource {
    static instances: FakeEventSource[] = []

    public onopen: ((event: Event) => void) | null = null
    public onmessage: ((event: MessageEvent) => void) | null = null
    public onerror: ((event: Event) => void) | null = null
    public close = vi.fn()

    public url: string
    constructor(url: string) {
      this.url = url
      FakeEventSource.instances.push(this)
    }

    addEventListener = vi.fn()
  }

  vi.stubGlobal('EventSource', FakeEventSource)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('BaytoRoom', () => {
  it('renders the Bayto room shell with transcript, roster, moderator summary and usage meter', async () => {
    render(
      <MemoryRouter initialEntries={['/sessions/session-123']}>
        <Routes>
          <Route path="/sessions/:sessionId" element={<BaytoRoom />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/bayto room/i, undefined, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.getByText(/transcript/i)).toBeInTheDocument()
    expect(screen.getByText(/roster/i)).toBeInTheDocument()
    expect(screen.getByText(/moderator summary/i)).toBeInTheDocument()
    expect(screen.getByText(/usage vs budget/i)).toBeInTheDocument()
  })

  it('says why a finished session ended', async () => {
    const body = JSON.stringify({ session: {
      id: 'session-123', task_id: 't', mode_id: 'debate', status: 'finished', started_at: null, ended_at: null,
      round: 6, budget: null, usage: { tokens_in: 1, tokens_out: 1, cost: 0 }, turn_counts: [], stop_reason: 'max_rounds' } })
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () =>
      new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } })))
    render(
      <MemoryRouter initialEntries={['/sessions/session-123']}>
        <Routes>
          <Route path="/sessions/:sessionId" element={<BaytoRoom />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByText(/ended because it reached its round limit/i, undefined, { timeout: 5000 })).toBeInTheDocument()
  })

  it('has plain wording for each stop reason and passes unknown ones through', () => {
    expect(stopReasonText('staleness')).toMatch(/new arguments/)
    expect(stopReasonText('user_stop')).toMatch(/stopped/)
    expect(stopReasonText('some_new_reason')).toBe('some new reason')
    expect(stopReasonText(null)).toBeNull()
  })
})
