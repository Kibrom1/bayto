import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RoomRail, type RoomRailProps } from './RoomRail'
import { muteParticipant, removeParticipant, addParticipant } from '../api/sessions'

const json = (b: unknown, status = 201) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } })
let calls: { method: string; url: string; body?: any }[]

const detail = (over: object = {}) => ({
  id: 's1', task_id: 't1', mode_id: 'debate', status: 'running', started_at: null, ended_at: null, round: 2, budget: null,
  usage: { tokens_in: 10, tokens_out: 5, cost: 0.01 },
  turn_counts: [
    { participant: 'lawyer', agent_id: 'a1', turns: 2 },
    { participant: 'pm', agent_id: 'a2', turns: 1, muted: true },
  ],
  ...over,
}) as any

function props(over: Partial<RoomRailProps> = {}): RoomRailProps {
  return {
    detail: detail(), sessionId: 's1', canEdit: true, flash: vi.fn(), onChanged: vi.fn(), live: {}, catalog: null,
    hue: () => 10, display: (r) => ({ name: r === 'lawyer' ? 'Lawyer' : r === 'pm' ? 'Product Manager' : r, stance: null }),
    ...over,
  }
}

beforeEach(() => {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (input: any, init?: any) => {
    const url = String(input); const method = init?.method ?? 'GET'
    calls.push({ method, url, body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined })
    if (url.endsWith('/agents/templates')) return json([{ id: 'x1', role: 'lawyer', name: 'Lawyer' }, { id: 'x2', role: 'designer', name: 'Designer' }], 200)
    if (url.includes('/attachments')) return json([], 200)
    if (url.includes('/retrieval')) return json({ semantic_retrieval: false, ready: true, provider: 'openai', model: null, needs: null }, 200)
    return json({ session_agent: { agent_id: 'a1' } })
  }))
})
afterEach(() => vi.unstubAllGlobals())

describe('api wrappers', () => {
  it('post to the participant endpoints', async () => {
    await muteParticipant('s1', 'a1'); await removeParticipant('s1', 'a1'); await addParticipant('s1', 'x2')
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      'POST /sessions/s1/participants/a1/mute', 'POST /sessions/s1/participants/a1/remove', 'POST /sessions/s1/participants'])
    expect(calls[2].body).toEqual({ agent_id: 'x2' })
  })
})

describe('RoomRail round 2 (N1, N2, N4, N8, UX-27)', () => {
  it('is a focusable element with the id the toggle points at, and has no duplicate landmark names', () => {
    render(<RoomRail {...props()} />)
    const rail = document.getElementById('room-rail')!
    expect(rail.tagName).toBe('ASIDE')
    expect(rail).toHaveAttribute('tabindex', '0')
    const names = Array.from(rail.querySelectorAll('[role="region"],section[aria-label],section[aria-labelledby]')).map((e) => e.getAttribute('aria-label') ?? e.getAttribute('aria-labelledby'))
    expect(new Set(names).size).toBe(names.length)
  })

  it('keeps each roster meta phrase on one line and hides an empty moderator summary', () => {
    render(<RoomRail {...props({ display: () => ({ name: 'Lawyer', stance: 'scope-discipline' }) })} />)
    const parts = document.querySelectorAll('.roster-part')
    expect(parts.length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByText(/moderator summary/i)).toBeNull()
    expect(screen.queryByText(/appears after the first turns/i)).toBeNull()
  })

  it('shows the moderator summary once there is one', () => {
    render(<RoomRail {...props({ summary: 'They agree on price.' })} />)
    expect(screen.getByText('Moderator summary')).toBeInTheDocument()
    expect(screen.getByText('They agree on price.')).toBeInTheDocument()
  })
})

describe('RoomRail roster (UX-06)', () => {
  it('mutes a seat from its kebab menu and unmutes a muted one', async () => {
    const user = userEvent.setup(); const p = props()
    render(<RoomRail {...p} />)
    await user.click(screen.getByRole('button', { name: 'More actions for Lawyer' }))
    await user.click(screen.getByRole('menuitem', { name: 'Mute' }))
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/participants/a1/mute'))).toBe(true))
    expect(p.flash).toHaveBeenCalledWith(expect.stringMatching(/Lawyer muted/))
    await user.click(screen.getByRole('button', { name: 'More actions for Product Manager' }))
    await user.click(screen.getByRole('menuitem', { name: 'Unmute' }))
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/participants/a2/unmute'))).toBe(true))
  })

  it('asks before removing, and removes only after confirming', async () => {
    const user = userEvent.setup()
    render(<RoomRail {...props()} />)
    await user.click(screen.getByRole('button', { name: 'More actions for Lawyer' }))
    await user.click(screen.getByRole('menuitem', { name: 'Remove' }))
    expect(calls.some((c) => c.url.endsWith('/remove'))).toBe(false)
    await user.click(screen.getByRole('button', { name: 'Keep' }))
    expect(calls.some((c) => c.url.endsWith('/remove'))).toBe(false)
    await user.click(screen.getByRole('button', { name: 'More actions for Lawyer' }))
    await user.click(screen.getByRole('menuitem', { name: 'Remove' }))
    await user.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/participants/a1/remove'))).toBe(true))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'More actions for Lawyer' })).toBeNull())
  })

  it('adds an agent who is not yet at the table', async () => {
    const user = userEvent.setup(); const p = props()
    render(<RoomRail {...p} />)
    await user.click(screen.getByRole('button', { name: 'Add agent' }))
    const dlg = await screen.findByRole('dialog', { name: 'Add an agent' })
    expect(within(dlg).queryByText('Lawyer')).toBeNull()
    await user.click(await within(dlg).findByRole('button', { name: /Designer/ }))
    await waitFor(() => expect(calls.find((c) => c.method === 'POST' && c.url === '/sessions/s1/participants')?.body).toEqual({ agent_id: 'x2' }))
  })

  it('hides seat controls once the session is not live or paused', () => {
    render(<RoomRail {...props({ canEdit: false })} />)
    expect(screen.queryByRole('button', { name: 'Add agent' })).toBeNull()
    expect(screen.queryByRole('button', { name: /More actions/ })).toBeNull()
    expect(screen.queryByRole('tab', { name: /Context/ })).toBeNull()
  })
})

describe('RoomRail mobile sheet (UX-05, UX-24)', () => {
  it('offers Team, Context and Usage tabs and a Close button, and switches panels', async () => {
    const user = userEvent.setup(); const onClose = vi.fn()
    const { container } = render(<RoomRail {...props({ railOpen: true, onRailClose: onClose })} />)
    const rail = container.querySelector('aside')!
    expect(rail).toHaveClass('open')
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Team', 'Context', 'Usage'])
    expect(rail).toHaveAttribute('data-tab', 'team')
    await user.click(screen.getByRole('tab', { name: 'Usage' }))
    expect(rail).toHaveAttribute('data-tab', 'usage')
    expect(screen.getByRole('tab', { name: 'Usage' })).toHaveAttribute('aria-selected', 'true')
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('shows the attachment count on the Context tab', async () => {
    const files = [1, 2].map((i) => ({ id: 'f' + i, kind: 'file', name: `n${i}.md`, source: null, chars: 10, truncated: false, created_at: '' }))
    vi.mocked(fetch).mockImplementation(async (input: any) => {
      const url = String(input)
      if (url.endsWith('/attachments')) return json(files, 200)
      if (url.includes('/retrieval')) return json({ semantic_retrieval: false, ready: true, provider: null, model: null, needs: null }, 200)
      return json([], 200)
    })
    render(<RoomRail {...props({ railOpen: true, onRailClose: () => {} })} />)
    const tab = screen.getByRole('tab', { name: /Context/ })
    await waitFor(() => expect(tab).toHaveTextContent('Context2'))
  })

  it('does not render the sheet chrome as a Close button when there is no handler (desktop)', () => {
    render(<RoomRail {...props()} />)
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull()
  })
})
