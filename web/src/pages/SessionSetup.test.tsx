import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SessionSetup } from './SessionSetup'

const MODES = [
  { id: 'm1', name: 'brainstorm', floor_policy: 'round_robin', stop_rules: {} },
  { id: 'm2', name: 'debate', floor_policy: 'round_robin', stop_rules: {} },
]
const AGENTS = [
  { id: 'a1', name: 'Skeptic', role: 'Challenges claims', system_prompt: '', stance: 'critical', model: 'sonnet' },
  { id: 'a2', name: 'Builder', role: 'Proposes designs', system_prompt: '', stance: 'constructive', model: 'sonnet' },
]

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mockApi() {
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/modes')) return json(MODES)
    if (url.endsWith('/agents/templates')) return json(AGENTS)
    if (url.endsWith('/sessions') && init?.method === 'POST') return json({ session: { id: 's9', status: 'created' } }, 201)
    if (url.endsWith('/start')) return json({ session: { id: 's9', status: 'running' } })
    return json({ detail: 'nope' }, 404)
  })
}

function renderSetup() {
  render(
    <MemoryRouter initialEntries={['/setup/task-1']}>
      <Routes>
        <Route path="/setup/:taskId" element={<SessionSetup />} />
        <Route path="/room/:id" element={<p>room page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => { vi.stubGlobal('fetch', vi.fn()) })
afterEach(() => { vi.unstubAllGlobals() })

describe('SessionSetup', () => {
  it('shows the explorer, builds a roster and updates the estimate', async () => {
    const user = userEvent.setup()
    mockApi()
    renderSetup()

    expect(await screen.findByRole('heading', { name: /session setup/i })).toBeInTheDocument()
    expect(screen.getByText(/agent explorer/i)).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: /add to roster/i })[0])
    await user.click(screen.getAllByRole('button', { name: /add to roster/i })[0])

    expect(screen.getByText(/selected roster/i)).toBeInTheDocument()
    expect(screen.getByText(/estimated cost/i)).toBeInTheDocument()
    expect(screen.getAllByText(/% of budget cap/i).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /start session/i })).toBeEnabled()
  })

  it('explains what is missing instead of submitting an incomplete setup', async () => {
    const user = userEvent.setup()
    mockApi()
    renderSetup()
    await screen.findByRole('heading', { name: /session setup/i })

    await user.click(screen.getByRole('button', { name: /start session/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/choose a mode/i)
    expect(fetch).not.toHaveBeenCalledWith('/sessions', expect.anything())
  })

  it('creates and starts the session, then opens the room', async () => {
    const user = userEvent.setup()
    mockApi()
    renderSetup()
    await screen.findByRole('heading', { name: /session setup/i })

    await user.click(screen.getByRole('radio', { name: /debate/i }))
    await user.click(screen.getAllByRole('button', { name: /add to roster/i })[0])
    await user.click(screen.getAllByRole('button', { name: /add to roster/i })[0])
    await user.click(screen.getByRole('button', { name: /start session/i }))

    await waitFor(() => expect(screen.getByText('room page')).toBeInTheDocument())
    const post = vi.mocked(fetch).mock.calls.find(([u, i]) => String(u).endsWith('/sessions') && i?.method === 'POST')!
    expect(JSON.parse(post[1]!.body as string)).toMatchObject({
      task_id: 'task-1',
      mode_id: 'm2',
      roster: [{ agent_id: 'a1', seat_order: 0 }, { agent_id: 'a2', seat_order: 1 }],
    })
  })
})
