import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BaytoRoom, endedBanner, friendlyFailure, splitVerdict, stopReasonText } from './BaytoRoom'

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

    expect(await screen.findByRole('region', { name: 'Bayto room' }, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.getByText(/transcript/i)).toBeInTheDocument()
    expect(screen.getByText(/roster/i)).toBeInTheDocument()
    expect(screen.queryByText(/moderator summary/i)).toBeNull() // hidden until the moderator has written one
    expect(screen.queryByText(/appears after the first turns/i)).toBeNull()
    expect(screen.getByText(/usage vs budget/i)).toBeInTheDocument()
  })

  it('shows under a turn which attachment parts the seat read, and nothing when none were attached', async () => {
    const body = JSON.stringify({ session: {
      id: 'session-123', task_id: 't', mode_id: 'debate', status: 'running', started_at: null, ended_at: null,
      round: 1, budget: null, usage: { tokens_in: 1, tokens_out: 1, cost: 0 },
      turn_counts: [{ participant: 'lawyer', agent_id: 'a1', turns: 2 }] } })
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () =>
      new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } })))
    render(
      <MemoryRouter initialEntries={['/sessions/session-123']}>
        <Routes>
          <Route path="/sessions/:sessionId" element={<BaytoRoom />} />
        </Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('region', { name: 'Bayto room' }, { timeout: 5000 })
    Element.prototype.scrollIntoView = vi.fn() // jsdom has none
    const source = (globalThis as any).EventSource.instances.at(-1)
    const send = (m: object) => act(() => source.onmessage({ data: JSON.stringify({ kind: 'answer', to: ['moderator'], from: 'lawyer', ...m }) }))
    send({ message_id: 'm1', seq: 1, body: 'first view', meta: { tokens_in: 3, attachments: [
      { name: 'contract.txt', mode: 'excerpt', parts: [3, 7], total: 41 }, { name: 'notes.md', mode: 'whole', parts: [], total: 2 }] } })
    send({ message_id: 'm2', seq: 2, body: 'second view', meta: { tokens_in: 3 } })
    expect(await screen.findByText('first view')).toBeInTheDocument()
    expect(screen.getByText('Read: contract.txt parts 3, 7 of 41 · notes.md (whole)')).toBeInTheDocument()
    expect(screen.getByText('second view')).toBeInTheDocument()
    expect(screen.getAllByText(/^Read:/)).toHaveLength(1)
  })

  it('shows the Read note live when it arrives after the message, with no reload', async () => {
    const body = JSON.stringify({ session: {
      id: 'session-123', task_id: 't', mode_id: 'debate', status: 'running', started_at: null, ended_at: null,
      round: 1, budget: null, usage: { tokens_in: 1, tokens_out: 1, cost: 0 },
      turn_counts: [{ participant: 'lawyer', agent_id: 'a1', turns: 1 }] } })
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () =>
      new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } })))
    render(
      <MemoryRouter initialEntries={['/sessions/session-123']}>
        <Routes>
          <Route path="/sessions/:sessionId" element={<BaytoRoom />} />
        </Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('region', { name: 'Bayto room' }, { timeout: 5000 })
    Element.prototype.scrollIntoView = vi.fn()
    const source = (globalThis as any).EventSource.instances.at(-1)
    const named = (name: string, data: object) => {
      const call = source.addEventListener.mock.calls.filter((c: any[]) => c[0] === name).at(-1)
      act(() => call[1]({ data: JSON.stringify(data) }))
    }
    act(() => source.onmessage({ data: JSON.stringify({ kind: 'answer', to: ['moderator'], from: 'lawyer',
      message_id: 'm1', seq: 1, body: 'first view', meta: { tokens_in: 3 } }) }))
    expect(await screen.findByText('first view')).toBeInTheDocument()
    expect(screen.queryByText(/^Read:/)).not.toBeInTheDocument()
    named('message_meta', { message_id: 'm1', from: 'lawyer', meta: { attachments: [
      { name: 'contract.txt', mode: 'excerpt', parts: [3, 7], total: 41 }] } })
    expect(await screen.findByText('Read: contract.txt parts 3, 7 of 41')).toBeInTheDocument()
    expect(screen.getByText('first view')).toBeInTheDocument()
    // a report for a message that has not arrived yet is held and applied when it does
    named('message_meta', { message_id: 'm2', from: 'lawyer', meta: { attachments: [{ name: 'notes.md', mode: 'whole', parts: [], total: 2 }] } })
    act(() => source.onmessage({ data: JSON.stringify({ kind: 'answer', to: ['moderator'], from: 'lawyer',
      message_id: 'm2', seq: 2, body: 'second view', meta: {} }) }))
    expect(await screen.findByText('Read: notes.md (whole)')).toBeInTheDocument()
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
    const banner = await screen.findByText(/ended because it reached its round limit/i, undefined, { timeout: 5000 })
    expect(banner).toHaveClass('notice')
    expect(banner).not.toHaveClass('warn') // a normal ending is not a caution
    expect(screen.queryByRole('button', { name: 'Export decision record' })).toBeInTheDocument() // the bar, not the banner
  })

  it('shows a successful Build ending as success, and a budget stop as a caution', () => {
    expect(endedBanner('build_complete')).toEqual({ text: 'The build finished: tests passed and the reviewer approved.', tone: 'ok' })
    expect(endedBanner('budget_exceeded')?.tone).toBe('warn')
    expect(endedBanner('max_rounds')?.tone).toBe('')
    expect(endedBanner('user_stop')?.tone).toBe('')
    expect(endedBanner(null)).toBeNull()
  })

  it('titles the breadcrumb with the task, labels the cost, and shows one status', async () => {
    const task = { id: 'task-1', title: 'Pick a vendor' }
    const body = JSON.stringify({ session: {
      id: 'session-123', task_id: 'task-1', mode_id: 'debate', status: 'running', started_at: null, ended_at: null,
      round: 1, budget: { max_cost: 5 }, usage: { tokens_in: 1, tokens_out: 1, cost: 0.01 }, turn_counts: [] } })
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) =>
      new Response(String(url).includes('/tasks') ? JSON.stringify([task]) : body, { status: 200, headers: { 'Content-Type': 'application/json' } })))
    render(<MemoryRouter initialEntries={['/sessions/session-123']}><Routes><Route path="/sessions/:sessionId" element={<BaytoRoom />} /></Routes></MemoryRouter>)
    expect(await screen.findByText('Spent $0.01 of $5.00 cap', undefined, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.queryByText('Bayto room')).toBeNull()
    expect(screen.queryByText('Live')).toBeNull()
    await waitFor(() => expect(document.querySelector('.room-kicker')?.textContent).toBe('Tasks / Pick a vendor'))
  })

  it('has a Team & context button that toggles the rail panel', async () => {
    await renderWithStatus('active')
    const btn = screen.getByRole('button', { name: 'Team & context' })
    expect(btn).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(btn)
    expect(btn).toHaveAttribute('aria-expanded', 'true')
    expect(document.querySelector('.room-body')).toHaveAttribute('data-rail-open', 'true')
  })

  it('puts the export choices in one menu, and confirms with a toast', async () => {
    await renderWithStatus('finished')
    expect(screen.queryByRole('button', { name: 'Download transcript' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Export' }))
    expect(screen.getByRole('menuitem', { name: /decision record/i })).toBeInTheDocument()
  })

  it('counts new turns on a pill anchored above the composer that is announced politely', async () => {
    await renderWithStatus('active')
    Element.prototype.scrollIntoView = vi.fn()
    const scroller = screen.getByRole('region', { name: 'Conversation' })
    Object.defineProperties(scroller, { scrollHeight: { value: 2000, configurable: true }, clientHeight: { value: 500, configurable: true }, scrollTop: { value: 0, configurable: true } })
    fireEvent.scroll(scroller)
    const source = (globalThis as any).EventSource.instances.at(-1)
    const say = (n: number) => act(() => source.onmessage({ data: JSON.stringify({ kind: 'answer', to: ['moderator'], from: 'lawyer', message_id: `m${n}`, seq: n, body: `turn ${n}`, meta: {} }) }))
    say(1); say(2)
    const pill = await screen.findByRole('button', { name: '2 new turns' })
    expect(pill.parentElement).toHaveAttribute('aria-live', 'polite')
  })

  it('shows the cause and a primary retry on a failed session, and the cause for an API key error', async () => {
    expect(friendlyFailure('401 Unauthorized: invalid x-api-key')).toMatch(/rejected the API key/)
    expect(friendlyFailure('KeyError: foo\n  at x')).toBe('It stopped with an error: KeyError: foo')
    await renderWithStatus('failed')
    expect(screen.getByRole('button', { name: 'Retry with same team' })).toBeInTheDocument()
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
    return screen.findByRole('region', { name: 'Bayto room' }, { timeout: 5000 })
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

  it('has an Export button that downloads the decision record from the export endpoint', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    await renderWithStatus('active')
    const fetchMock = vi.mocked(fetch)
    const base = fetchMock.getMockImplementation()!
    fetchMock.mockImplementation(async (url, init) => String(url).includes('/export')
      ? new Response('# Decision record', { status: 200, headers: { 'Content-Disposition': 'attachment; filename="bayto-t-1234.md"' } })
      : base(url, init))
    await userEvent.click(screen.getByRole('button', { name: 'Export' }))
    await userEvent.click(screen.getByRole('menuitem', { name: /decision record/i }))
    await waitFor(() => expect(click).toHaveBeenCalled())
    expect(fetchMock.mock.calls.some(([u]) => u === '/sessions/session-123/export?format=md')).toBe(true)
    click.mockRestore()
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
    await renderWithStatus('active')
    expect(screen.getByLabelText('Interject')).toBeEnabled()
  })

  it('replaces the message box with a one-line bar once the session has ended', async () => {
    await renderWithStatus('finished')
    expect(screen.queryByLabelText('Interject')).toBeNull()
    expect(screen.getByText('Session ended.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /start a new session/i })).toHaveAttribute('href', '/setup/task-1')
    expect(screen.getByRole('button', { name: 'Export decision record' })).toBeInTheDocument()
    expect(screen.getByText('No turns were recorded.')).toBeInTheDocument()
  })

  it('explains an interrupted session and offers no controls that would fail', async () => {
    await renderWithStatus('orphaned')
    expect(screen.queryByLabelText('Interject')).toBeNull()
    expect(screen.getByText(/this session was interrupted/i)).toBeInTheDocument()
    expect(screen.getByText('Interrupted')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /continue with the same team/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull()
    expect(screen.queryByRole('button', { name: /end & synthesize/i })).toBeNull()
  })

  it('lets you answer a paused session and says the message waits for Resume', async () => {
    await renderWithStatus('needs_human')
    expect(screen.getByLabelText('Interject')).toBeEnabled()
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

describe('UX round 2 follow-ups', () => {
  function mount(status: string, extra: object = {}, modeName = 'debate') {
    const body = JSON.stringify({ session: {
      id: 'session-123', task_id: 'task-1', mode_id: 'm1', status, started_at: null, ended_at: null,
      round: 1, budget: null, usage: { tokens_in: 1, tokens_out: 1, cost: 0 }, turn_counts: [], ...extra } })
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => {
      const u = String(url)
      const out = u.includes('/tasks') ? [{ id: 'task-1', title: 'Pick a vendor' }] : u.includes('/modes') ? [{ id: 'm1', name: modeName }] : null
      return new Response(out ? JSON.stringify(out) : body, { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    render(<MemoryRouter initialEntries={['/sessions/session-123']}><Routes><Route path="/sessions/:sessionId" element={<BaytoRoom />} /></Routes></MemoryRouter>)
  }

  it('UX-08: a Build ending is success even when the reason is spelled with a space', () => {
    expect(endedBanner('build complete')?.tone).toBe('ok')
    expect(endedBanner('Build-Complete')?.text).toMatch(/build finished/i)
    expect(stopReasonText('build complete')).toMatch(/tests passed/)
  })

  it('UX-19: the tab title is the task title', async () => {
    mount('running')
    await waitFor(() => expect(document.title).toBe('Pick a vendor · Bayto'))
  })

  it('N3: Continue as Build is a plain button, not amber', async () => {
    mount('finished')
    const link = await screen.findByRole('link', { name: 'Continue as Build' })
    expect(link).not.toHaveClass('accent')
    expect(link).not.toHaveClass('primary')
  })

  it('N6: an ended room never counts old turns as new', async () => {
    mount('finished')
    await screen.findByRole('link', { name: 'Start a new session' })
    expect(screen.queryByText(/new turns?/)).toBeNull()
  })

  it('N1/N2/N4: the rail toggle points at a real, keyboard-focusable rail with no duplicate landmark', async () => {
    mount('running')
    const btn = await screen.findByRole('button', { name: 'Team & context' })
    const rail = document.getElementById(btn.getAttribute('aria-controls')!)
    expect(rail).not.toBeNull()
    expect(rail).toHaveAttribute('tabindex', '0')
    const labels = Array.from(document.querySelectorAll('[role="region"],aside,section[aria-label],section[aria-labelledby]'))
      .map((e) => e.getAttribute('aria-label') ?? e.getAttribute('aria-labelledby')).filter(Boolean)
    expect(new Set(labels).size).toBe(labels.length)
  })

  it('UX-27: no filler summary in the rail', async () => {
    mount('failed', { failure_reason: 'boom' })
    await screen.findByText(/This session failed/)
    expect(screen.queryByText(/appears after the first turns/i)).toBeNull()
  })
})
