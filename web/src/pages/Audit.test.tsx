import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Audit } from './Audit'

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}
const ev = (seq: number, action: string, summary: string, extra: object = {}) => ({
  seq, at: '2026-10-21T01:00:00Z', actor: 'person', action, summary, task_id: null, session_id: null,
  subject_type: null, subject_id: null, detail: {}, ...extra,
})
let calls: string[]
let verifyReply: object

function mockApi(rows: any[]) {
  calls = []
  verifyReply = { ok: true, checked: 3, first_bad_seq: null, head_hash: 'abcdef0123456789' }
  vi.mocked(fetch).mockImplementation(async (input) => {
    const url = String(input)
    calls.push(url)
    if (url.startsWith('/audit/verify')) return json(verifyReply)
    if (url.startsWith('/audit')) return json(rows)
    return json({ detail: 'nope' }, 404)
  })
}
const renderAt = (path = '/activity') => render(<MemoryRouter initialEntries={[path]}><Audit /></MemoryRouter>)

beforeEach(() => { vi.stubGlobal('fetch', vi.fn()) })
afterEach(() => { vi.unstubAllGlobals() })

describe('Audit page', () => {
  it('lists events with who did them and says what is never recorded', async () => {
    mockApi([ev(2, 'session.started', 'Session started with 1 seats', { actor: 'system' }), ev(1, 'agent.created', 'Created custom agent Auditor')])
    renderAt()
    expect(await screen.findByText('Created custom agent Auditor')).toBeInTheDocument()
    expect(screen.getByText('Bayto')).toBeInTheDocument()
    expect(screen.getByText('You')).toBeInTheDocument()
    expect(screen.getByText(/never message text or attached content/i)).toBeInTheDocument()
  })

  it("shows each seat's allowed and denied tools in a session start", async () => {
    const user = userEvent.setup()
    mockApi([ev(1, 'session.started', 'Session started with 1 seats', {
      actor: 'system', detail: { seats: [{ role: 'architect', name: 'Architect', allowed: ['Read', 'Grep'], denied: ['Write', 'Bash'] }] },
    })])
    renderAt()
    await user.click(await screen.findByText('Details'))
    expect(screen.getByText('Architect')).toBeInTheDocument()
    expect(screen.getByText(/Allowed: Read, Grep/)).toBeInTheDocument()
    expect(screen.getByText(/Denied: Write, Bash/)).toBeInTheDocument()
  })

  it('filters by kind and scopes to one session from the link', async () => {
    const user = userEvent.setup()
    mockApi([ev(1, 'agent.created', 'x')])
    renderAt('/activity?session=s-1')
    await screen.findByText('x')
    expect(calls[0]).toContain('session_id=s-1')
    expect(screen.getByText(/Showing one session/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Agents' }))
    await waitFor(() => expect(calls.some((c) => c.includes('action=agent'))).toBe(true))
  })

  it('verifies the log and reports a broken chain plainly', async () => {
    const user = userEvent.setup()
    mockApi([ev(1, 'agent.created', 'x')])
    renderAt()
    await user.click(await screen.findByRole('button', { name: /verify the log/i }))
    expect(await screen.findByRole('status')).toHaveTextContent(/intact: 3 events chain correctly/)
    verifyReply = { ok: false, checked: 1, first_bad_seq: 2, head_hash: 'ab' }
    await user.click(screen.getByRole('button', { name: /verify the log/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/does not match at event #2/)
  })

  it('offers a CSV download for the current filter', async () => {
    mockApi([ev(1, 'agent.created', 'x')])
    renderAt('/activity?action=agent')
    const link = await screen.findByRole('link', { name: /download csv/i })
    expect(link).toHaveAttribute('href', '/audit/export?action=agent')
  })
})
