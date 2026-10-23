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
const OTHER = [
  { id: 'openai/gpt-x', label: 'GPT X', tier: 'small', usd_per_turn: 0.05, default: false, provider: 'openai', provider_label: 'OpenAI', ready: true, needs: null },
  { id: 'gemini/gem-1', label: 'Gem 1', tier: 'small', usd_per_turn: 0.05, default: false, provider: 'gemini', provider_label: 'Google Gemini', ready: false, needs: 'GEMINI_API_KEY' },
]
let models: any = MODELS
const calls: { method: string; url: string; body: any }[] = []

beforeEach(() => {
  calls.length = 0
  models = MODELS
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ method: init?.method ?? 'GET', url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (url.endsWith('/models')) return json(models)
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

    await user.selectOptions(screen.getByLabelText('Final write-up model'), 'claude-opus-5-5')
    await user.click(screen.getByRole('button', { name: /start session/i }))
    await screen.findByText('room page')
    const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/sessions'))!
    expect(post.body.synthesis_model).toBe('claude-opus-5-5')
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
    expect('synthesis_model' in post.body).toBe(false)
  })

  it('names other providers, disables one that is not configured, and keeps the final write-up on Claude', async () => {
    models = { ...MODELS, models: [...MODELS.models, ...OTHER] }
    const user = userEvent.setup()
    renderSetup()
    await screen.findByRole('heading', { name: /session setup/i })
    await user.click(await screen.findByRole('radio', { name: /^debate/i }))
    await user.click(screen.getAllByRole('button', { name: /add to roster/i })[0])
    const seat = await screen.findByLabelText('Model for Skeptic')
    expect(within(seat).getByRole('option', { name: 'GPT X (OpenAI)' })).toBeEnabled()
    expect(within(seat).getByRole('option', { name: /Gem 1.*set GEMINI_API_KEY first/ })).toBeDisabled()
    const writer = screen.getByLabelText('Final write-up model')
    expect(within(writer).queryByRole('option', { name: /GPT X/ })).toBeNull()
    expect(within(writer).getByRole('option', { name: 'Opus 5.5' })).toBeInTheDocument()
  })

  it('warns when the Build developer is on a small model', async () => {
    const BUILD = { id: 'm3', name: 'build', floor_policy: 'pipeline', stop_rules: {} }
    const dev = { ...AGENT('b1', 'Developer'), role: 'build-developer' }
    const base = globalThis.fetch as any
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/modes')) return json([BUILD])
      if (url.endsWith('/agents/templates')) return json([dev])
      return base(input, init)
    }))
    const user = userEvent.setup()
    renderSetup()
    await screen.findByRole('heading', { name: /session setup/i })
    await user.click(await screen.findByRole('radio', { name: /^build/i }))
    await user.click(await screen.findByRole('button', { name: /add to roster/i }))
    expect(screen.queryByRole('note')).toBeNull()
    await user.selectOptions(await screen.findByLabelText('Model for Developer'), 'claude-haiku-5-5')
    expect(await screen.findByRole('note')).toHaveTextContent(/developer is on a small model/i)
  })

  it('offers a gateway model to a Build seat only when the server allows it, with a plain note once chosen', async () => {
    const BUILD = { id: 'm3', name: 'build', floor_policy: 'pipeline', stop_rules: {} }
    const dev = { ...AGENT('b1', 'Developer'), role: 'build-developer' }
    const gw = (build_ok: boolean) => ({ id: 'gateway/qwen3', label: 'Qwen3', tier: 'small', usd_per_turn: 0, default: false,
      provider: 'gateway', provider_label: 'Gateway', ready: true, needs: null, build_ok })
    const base = globalThis.fetch as any
    for (const allowed of [false, true]) {
      models = { ...MODELS, models: [...MODELS.models, gw(allowed)] }
      vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.endsWith('/modes')) return json([BUILD])
        if (url.endsWith('/agents/templates')) return json([dev])
        return base(input, init)
      }))
      const user = userEvent.setup()
      const { unmount } = render(
        <MemoryRouter initialEntries={['/setup/task-1']}><Routes><Route path="/setup/:taskId" element={<SessionSetup />} /></Routes></MemoryRouter>)
      await screen.findByRole('heading', { name: /session setup/i })
      await user.click(await screen.findByRole('radio', { name: /^build/i }))
      await user.click(await screen.findByRole('button', { name: /add to roster/i }))
      const seat = await screen.findByLabelText('Model for Developer')
      if (!allowed) {
        expect(within(seat).queryByRole('option', { name: /Qwen3/ })).toBeNull()
      } else {
        expect(screen.queryByRole('note')).toBeNull()
        await user.selectOptions(seat, 'gateway/qwen3')
        const notes = (await screen.findAllByRole('note')).map((n) => n.textContent).join(' ')
        expect(notes).toMatch(/tool use through a gateway varies/i)
        expect(notes).toMatch(/same allowed and denied commands/i)
      }
      unmount()
    }
  })
})
