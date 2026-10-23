import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BaytoRoom, friendlyFailure, splitVerdict, stopReasonText } from './BaytoRoom'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => (
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
    )),
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
  cleanup() // unmount first: a mounted room would reopen its EventSource after the stub is gone
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

  it('offers Start, not Pause, for a session that was never started', async () => {
    const body = JSON.stringify({ session: {
      id: 'session-123', task_id: 't', mode_id: 'debate', status: 'created', started_at: null, ended_at: null,
      round: 0, budget: null, usage: { tokens_in: 0, tokens_out: 0, cost: 0 }, turn_counts: [] } })
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () =>
      new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } })))
    render(
      <MemoryRouter initialEntries={['/sessions/session-123']}>
        <Routes>
          <Route path="/sessions/:sessionId" element={<BaytoRoom />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByRole('button', { name: 'Start' }, { timeout: 5000 })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull()
  })

  it('explains a failed start in one plain line and keeps the raw output behind Technical details', async () => {
    expect(friendlyFailure('while creating the sandbox (sbx env create): SandboxProviderError: boom')).toMatch(/sandbox/)
    expect(friendlyFailure(null)).toMatch(/before it could start/)
    const body = JSON.stringify({ session: {
      id: 'session-123', task_id: 't', mode_id: 'debate', status: 'failed', started_at: null, ended_at: null,
      round: 0, budget: null, usage: { tokens_in: 0, tokens_out: 0, cost: 0 }, turn_counts: [],
      failure_reason: 'sbx env create: SandboxProviderError: exec format error' } })
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () =>
      new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } })))
    render(
      <MemoryRouter initialEntries={['/sessions/session-123']}>
        <Routes>
          <Route path="/sessions/:sessionId" element={<BaytoRoom />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByText(/couldn't be created/i, undefined, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.getByText('Technical details')).toBeInTheDocument()
    expect(screen.getByText(/exec format error/)).toBeInTheDocument()
  })

  it('lets you write a message while the session is paused', async () => {
    const body = JSON.stringify({ session: {
      id: 'session-123', task_id: 't', mode_id: 'build', status: 'needs_human', started_at: null, ended_at: null,
      round: 1, budget: null, usage: { tokens_in: 1, tokens_out: 1, cost: 0 }, turn_counts: [] } })
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () =>
      new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } })))
    render(
      <MemoryRouter initialEntries={['/sessions/session-123']}>
        <Routes>
          <Route path="/sessions/:sessionId" element={<BaytoRoom />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByRole('button', { name: 'Resume' }, { timeout: 5000 })).toBeEnabled()
    expect(screen.getByLabelText('Interject')).toBeEnabled()
    expect(screen.getByText(/answer below, then press resume/i)).toBeInTheDocument()
  })

  it('has plain wording for each stop reason and passes unknown ones through', () => {
    expect(stopReasonText('staleness')).toMatch(/new arguments/)
    expect(stopReasonText('user_stop')).toMatch(/stopped/)
    expect(stopReasonText('some_new_reason')).toBe('some new reason')
    expect(stopReasonText(null)).toBeNull()
  })

  async function renderWithStatus(status: string) {
    const body = JSON.stringify({ session: {
      id: 'session-123', task_id: 'task-1', mode_id: 'build', status, started_at: null, ended_at: null,
      round: 1, budget: null, usage: { tokens_in: 1, tokens_out: 1, cost: 0 }, turn_counts: [] } })
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () =>
      new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } })))
    render(
      <MemoryRouter initialEntries={['/sessions/session-123']}>
        <Routes>
          <Route path="/sessions/:sessionId" element={<BaytoRoom />} />
        </Routes>
      </MemoryRouter>,
    )
    return screen.findByLabelText('Interject', undefined, { timeout: 5000 })
  }

  it('offers Archive and Delete for an ended session but not a running one', async () => {
    await renderWithStatus('failed')
    expect(screen.getByRole('button', { name: 'Archive' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument()
    cleanup()
    await renderWithStatus('active')
    expect(screen.queryByRole('button', { name: 'Archive' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull()
  })

  it('asks before deleting a session, then deletes it', async () => {
    await renderWithStatus('finished')
    const fetchMock = vi.mocked(fetch)
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(screen.getByText(/removed for good/i)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Delete session' }))
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'DELETE')
      expect(call?.[0]).toBe('/sessions/session-123')
    })
  })

  it('lets you message a running session', async () => {
    const box = await renderWithStatus('active')
    expect(box).toBeEnabled()
  })

  it('turns the message box off, with the reason, once the session has ended', async () => {
    const box = await renderWithStatus('finished')
    expect(box).toBeDisabled()
    expect(screen.getByText(/this session has ended and can't take messages/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /start a new session/i })).toHaveAttribute('href', '/setup/task-1')
  })

  it('explains an interrupted session and offers no controls that would fail', async () => {
    const box = await renderWithStatus('orphaned')
    expect(box).toBeDisabled()
    expect(screen.getByText(/this session was interrupted/i)).toBeInTheDocument()
    expect(screen.getByText('Interrupted')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /continue with the same team/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull()
    expect(screen.queryByRole('button', { name: /end & synthesize/i })).toBeNull()
  })

  it('lets you answer a paused session and says the message waits for Resume', async () => {
    const box = await renderWithStatus('needs_human')
    expect(box).toBeEnabled()
    expect(screen.getByText(/the agents see it when you press resume/i)).toBeInTheDocument()
  })

  it('hides Pause and End once the session has ended', async () => {
    await renderWithStatus('finished')
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull()
    expect(screen.queryByRole('button', { name: /end & synthesize/i })).toBeNull()
    expect(screen.queryByText('Offline')).toBeNull()
  })
})

describe('splitVerdict', () => {
  it('takes the VERDICT protocol line out of the text', () => {
    expect(splitVerdict('Plan done.\n\nVERDICT: planned')).toEqual({ text: 'Plan done.', verdict: 'planned' })
  })
  it('leaves text without a verdict alone', () => {
    expect(splitVerdict('Just talk')).toEqual({ text: 'Just talk', verdict: null })
  })
})
