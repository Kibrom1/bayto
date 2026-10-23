import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SessionSetup } from './SessionSetup'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const MODELS = {
  default: 'claude-sonnet-5-5',
  models: [
    { id: 'claude-opus-5-5', label: 'Opus 5.5', tier: 'large', usd_per_turn: 0.9, default: false },
    { id: 'claude-sonnet-5-5', label: 'Sonnet 5.5', tier: 'standard', usd_per_turn: 0.5, default: true },
    { id: 'claude-haiku-5-5', label: 'Haiku 5.5', tier: 'small', usd_per_turn: 0.1, default: false },
  ],
}
const AGENT = (id: string, name: string) => ({ id, name, role: name.toLowerCase(), system_prompt: 'x', stance: 's', model: 'claude-sonnet-5-5', kind: 'native', custom: false })
const calls: { method: string; url: string; body: any }[] = []

beforeEach(() => {
  calls.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ method: init?.method ?? 'GET', url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (url.endsWith('/models')) return json(MODELS)
    if (url.endsWith('/tasks')) return json([{ id: 'task-1', title: 't', brief: null, output_type: 'memo', workspace: null }])
    if (url.endsWith('/modes')) return json([{ id: 'm1', name: 'debate', floor_policy: 'round-robin' }])
    if (url.endsWith('/agents/templates')) return json([AGENT('a1', 'Skeptic'), AGENT('a2', 'Optimist')])
    if (url.endsWith('/sessions') && init?.method === 'POST') return json({ session: { id: 's9', status: 'created' } }, 201)
    if (url.endsWith('/start')) return json({ session: { id: 's9', status: 'starting' } }, 202)
    return json({ detail: 'nope' }, 404)
  }))
})
afterEach(() => vi.unstubAllGlobals())

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

describe('per-seat models on the setup screen', () => {
  it('lets each seat pick a model, updates the estimate and sends the choice', async () => {
    const user = userEvent.setup()
    renderSetup()
    await screen.findByRole('heading', { name: /session setup/i })
    await user.click(await screen.findByRole('radio', { name: /^debate/i }))
    for (let i = 0; i < 2; i++) await user.click(screen.getAllByRole('button', { name: /add to roster/i })[0])

    const estimate = () => screen.getByText(/^~\$/).textContent
    expect(estimate()).toBe('~$1.00') // two Sonnet seats at the usual $0.50

    const skeptic = await screen.findByLabelText('Model for Skeptic')
    expect(within(skeptic).getByRole('option', { name: /default \(sonnet 5\.5\)/i })).toBeInTheDocument()
    await user.selectOptions(skeptic, 'claude-haiku-5-5') // 0.1 / 0.5 of the default
    expect(estimate()).toBe('~$0.60')
    await user.selectOptions(screen.getByLabelText('Model for Optimist'), 'claude-opus-5-5') // 1.8x
    expect(estimate()).toBe('~$1.00') // 0.1 + 0.9

    await user.click(screen.getByRole('button', { name: /start session/i }))
    await screen.findByText('room page')
    const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/sessions'))!
    expect(post.body.roster).toEqual([
      { agent_id: 'a1', seat_order: 0, model: 'claude-haiku-5-5' },
      { agent_id: 'a2', seat_order: 1, model: 'claude-opus-5-5' },
    ])
  })

  it('sends no model for a seat left on its default', async () => {
    const user = userEvent.setup()
    renderSetup()
    await user.click(await screen.findByRole('radio', { name: /^debate/i }))
    for (let i = 0; i < 2; i++) await user.click(screen.getAllByRole('button', { name: /add to roster/i })[0])
    await user.click(screen.getByRole('button', { name: /start session/i }))
    await screen.findByText('room page')
    const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/sessions'))!
    expect(post.body.roster.every((r: any) => !('model' in r))).toBe(true)
  })
})
