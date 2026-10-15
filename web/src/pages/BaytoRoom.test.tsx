import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BaytoRoom } from './BaytoRoom'

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

    expect(await screen.findByText(/bayto room/i)).toBeInTheDocument()
    expect(screen.getByText(/transcript/i)).toBeInTheDocument()
    expect(screen.getByText(/roster/i)).toBeInTheDocument()
    expect(screen.getByText(/moderator summary/i)).toBeInTheDocument()
    expect(screen.getByText(/usage vs budget/i)).toBeInTheDocument()
  })
})
