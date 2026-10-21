import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SessionSetup } from './SessionSetup'

const MODES = [{ id: 'm1', name: 'debate', floor_policy: 'round_robin', stop_rules: {} }]
const BUILD_MODE = { id: 'm3', name: 'build', floor_policy: 'pipeline', stop_rules: {} }
const BUILT_IN = { id: 'a1', name: 'Architect', role: 'architect', system_prompt: '', stance: 'pragmatist', model: 'sonnet', custom: false }

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

let agents: any[]
let calls: { method: string; url: string; body?: any }[]

function mockApi() {
  agents = [BUILT_IN]
  calls = []
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ method, url, body })
    if (url.endsWith('/tasks')) return json([{ id: 'task-1', title: 't', brief: null, output_type: 'plan', workspace: null }])
    if (url.endsWith('/attachments')) return json([])
    if (url.endsWith('/modes')) return json([...MODES, BUILD_MODE])
    if (url.endsWith('/agents/templates')) return json(agents)
    if (url.endsWith('/agents') && method === 'POST') {
      if (body.name === 'Moderator') return json({ detail: { error: { code: 'reserved_name', detail: "'Moderator' is reserved; pick another name" } } }, 422)
      const row = { id: 'c1', name: body.name, role: body.name.toLowerCase(), system_prompt: body.instructions, stance: body.stance, model: 'sonnet', custom: true, preset: 'discussion' }
      agents = [...agents, row]
      return json(row, 201)
    }
    if (url.endsWith('/agents/c1') && method === 'PATCH') {
      agents = agents.map((a) => (a.id === 'c1' ? { ...a, name: body.name, stance: body.stance, system_prompt: body.instructions } : a))
      return json(agents.find((a) => a.id === 'c1'))
    }
    if (url.endsWith('/agents/c1') && method === 'DELETE') {
      agents = agents.filter((a) => a.id !== 'c1')
      return new Response(null, { status: 204 })
    }
    return json({ detail: 'nope' }, 404)
  })
}

function renderSetup() {
  render(
    <MemoryRouter initialEntries={['/setup/task-1']}>
      <Routes><Route path="/setup/:taskId" element={<SessionSetup />} /></Routes>
    </MemoryRouter>,
  )
}


beforeEach(() => { vi.stubGlobal('fetch', vi.fn()) })
afterEach(() => { vi.unstubAllGlobals() })

describe('Custom agents on the setup screen', () => {
  it('creates an agent, shows it as Custom and seats it on the roster', async () => {
    const user = userEvent.setup()
    mockApi()
    renderSetup()
    await user.click(await screen.findByRole('button', { name: /^create agent$/i }))
    const dialog = screen.getByRole('dialog', { name: /create agent/i })
    expect(within(dialog).getByText(/discussion only/i)).toBeInTheDocument()
    await user.type(within(dialog).getByLabelText('Name'), 'Compliance')
    await user.type(within(dialog).getByLabelText('Stance'), 'risk-averse')
    await user.type(within(dialog).getByLabelText('Instructions'), 'Check every plan against the rules.')
    await user.click(within(dialog).getByRole('button', { name: /^create agent$/i }))

    expect(await screen.findByText('Custom')).toBeInTheDocument()
    const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/agents'))!
    expect(post.body).toEqual({ name: 'Compliance', stance: 'risk-averse', instructions: 'Check every plan against the rules.', preset: 'discussion' })
    expect(screen.getAllByText('Compliance').length).toBeGreaterThan(1) // in the explorer and on the roster
  })

  it('shows the server reason when a name is refused and keeps the dialog open', async () => {
    const user = userEvent.setup()
    mockApi()
    renderSetup()
    await user.click(await screen.findByRole('button', { name: /^create agent$/i }))
    const dialog = screen.getByRole('dialog', { name: /create agent/i })
    await user.type(within(dialog).getByLabelText('Name'), 'Moderator')
    await user.type(within(dialog).getByLabelText('Stance'), 's')
    await user.type(within(dialog).getByLabelText('Instructions'), 'x')
    await user.click(within(dialog).getByRole('button', { name: /^create agent$/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/reserved/i)
    expect(screen.getByRole('dialog', { name: /create agent/i })).toBeInTheDocument()
  })

  it('edits and archives a custom agent, and built-ins have neither button', async () => {
    const user = userEvent.setup()
    mockApi()
    agents = [BUILT_IN, { id: 'c1', name: 'Auditor', role: 'auditor', system_prompt: 'Audit.', stance: 'strict', model: 'sonnet', custom: true }]
    renderSetup()
    await screen.findByText('Auditor')
    expect(screen.queryByRole('button', { name: /edit architect/i })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /edit auditor/i }))
    const name = screen.getByLabelText('Name')
    await user.clear(name); await user.type(name, 'Chief Auditor')
    await user.click(screen.getByRole('button', { name: /save changes/i }))
    expect(await screen.findByText('Chief Auditor')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /archive chief auditor/i }))
    await user.click(screen.getByRole('button', { name: /^archive$/i }))
    await waitFor(() => expect(screen.queryByText('Chief Auditor')).not.toBeInTheDocument())
    expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/agents/c1'))).toBe(true)
  })

  it('does not offer Create agent in Build, where the four roles are fixed', async () => {
    const user = userEvent.setup()
    mockApi()
    renderSetup()
    await screen.findByRole('button', { name: /^create agent$/i })
    await user.click(screen.getByRole('radio', { name: /build/i }))
    expect(screen.queryByRole('button', { name: /^create agent$/i })).not.toBeInTheDocument()
  })
})
