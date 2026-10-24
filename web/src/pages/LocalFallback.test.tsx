import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SessionSetup } from './SessionSetup'
import { LocalBadge, localFallbackOf } from '../components/LocalBadge'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const MODELS = {
  default: 'claude-sonnet-5-5',
  models: [
    { id: 'claude-sonnet-5-5', label: 'Sonnet 5.5', tier: 'standard', usd_per_turn: 0.5, default: true },
    { id: 'ollama/llama3', label: 'Llama 3', tier: 'small', usd_per_turn: 0, default: false, provider: 'ollama', provider_label: 'Ollama (local)', ready: true, needs: null },
  ],
}
const AGENT = (id: string, name: string) => ({ id, name, role: name.toLowerCase(), system_prompt: 'x', stance: 's', model: 'claude-sonnet-5-5', kind: 'native', custom: false })
const calls: { method: string; url: string; body: any }[] = []
let task: any

beforeEach(() => {
  calls.length = 0
  task = { id: 'task-1', title: 't', brief: null, output_type: 'memo', workspace: null }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ method: init?.method ?? 'GET', url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (url.endsWith('/models')) return json(MODELS)
    if (url.endsWith('/tasks')) return json([task])
    if (url.endsWith('/modes')) return json([{ id: 'm1', name: 'debate', floor_policy: 'round-robin' }])
    if (url.endsWith('/agents/templates')) return json([AGENT('a1', 'Skeptic'), AGENT('a2', 'Optimist')])
    if (url.endsWith('/local-fallback')) return json({ enabled: true, model: 'ollama/llama3', effective_model: 'ollama/llama3', default_model: null, ready: true, needs: null, build: false })
    if (url.endsWith('/sessions') && init?.method === 'POST') return json({ session: { id: 's9', status: 'created' } }, 201)
    if (url.endsWith('/start')) return json({ session: { id: 's9', status: 'starting' } }, 202)
    return json({ detail: 'nope' }, 404)
  }))
})
afterEach(() => vi.unstubAllGlobals())

function setup() {
  return render(
    <MemoryRouter initialEntries={['/setup/task-1']}>
      <Routes>
        <Route path="/setup/:taskId" element={<SessionSetup />} />
        <Route path="/room/:sessionId" element={<p>room</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('local model fallback option on the setup page', () => {
  it('is off by default, explains the quality trade-off, and sends nothing unless chosen', async () => {
    const user = userEvent.setup()
    setup()
    const box = await screen.findByRole('checkbox', { name: /let a local model answer instead/i })
    expect(box).not.toBeChecked()
    expect(screen.getByText(/much weaker than Claude/i)).toBeInTheDocument()
    expect(screen.getByText(/Build seats never fall back/i)).toBeInTheDocument()
    await user.click(await screen.findByRole('radio', { name: /^debate/i }))
    for (let i = 0; i < 2; i++) await user.click(screen.getAllByRole('button', { name: /add to roster/i })[0])
    await user.click(screen.getByRole('button', { name: /start session/i }))
    await screen.findByText('room')
    expect(calls.some((c) => c.url.endsWith('/local-fallback'))).toBe(false)
  })

  it('saves the choice and the model before the session is created', async () => {
    const user = userEvent.setup()
    setup()
    await user.click(await screen.findByRole('checkbox', { name: /let a local model answer instead/i }))
    await user.selectOptions(await screen.findByLabelText('Local model'), 'ollama/llama3')
    await user.click(await screen.findByRole('radio', { name: /^debate/i }))
    for (let i = 0; i < 2; i++) await user.click(screen.getAllByRole('button', { name: /add to roster/i })[0])
    await user.click(screen.getByRole('button', { name: /start session/i }))
    await screen.findByText('room')
    const put = calls.find((c) => c.method === 'PUT' && c.url.endsWith('/local-fallback'))
    expect(put?.body).toEqual({ enabled: true, model: 'ollama/llama3' })
    expect(calls.indexOf(put!)).toBeLessThan(calls.findIndex((c) => c.method === 'POST' && c.url.endsWith('/sessions')))
  })

  it('starts with the task\'s saved choice', async () => {
    task = { ...task, local_fallback: { enabled: true, model: 'ollama/llama3' } }
    setup()
    expect(await screen.findByRole('checkbox', { name: /let a local model answer instead/i })).toBeChecked()
    expect((await screen.findByLabelText('Local model') as HTMLSelectElement).value).toBe('ollama/llama3')
  })
})

describe('Local model badge', () => {
  it('shows for a message written by the fallback, with the model in its title', () => {
    render(<LocalBadge meta={{ local_fallback: { model: 'ollama/llama3', reason: 'limit' } }} />)
    const badge = screen.getByText('Local model')
    expect(badge).toHaveAttribute('title', expect.stringContaining('ollama/llama3'))
  })

  it('renders nothing for an ordinary message or odd meta', () => {
    const { container } = render(<><LocalBadge meta={{ tokens_in: 3 }} /><LocalBadge meta={null} /><LocalBadge meta={{ local_fallback: 'yes' }} /></>)
    expect(container).toBeEmptyDOMElement()
    expect(localFallbackOf(undefined)).toBeNull()
  })
})
