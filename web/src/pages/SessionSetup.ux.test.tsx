import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SessionSetup } from './SessionSetup'
import { AgentDialog } from '../components/AgentDialog'

const MODES = [
  { id: 'm1', name: 'brainstorm', floor_policy: 'round_robin' },
  { id: 'm2', name: 'debate', floor_policy: 'round_robin' },
  { id: 'm4', name: 'Raise Hand', floor_policy: 'raise-hand' },
]
const AGENTS = [
  { id: 'a1', name: 'Skeptic', role: 'Challenges claims', system_prompt: '', stance: 'critical', model: 'sonnet' },
  { id: 'a2', name: 'Builder', role: 'Proposes designs', system_prompt: '', stance: 'constructive', model: 'sonnet' },
]
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/tasks')) return json([{ id: 'task-1', title: 'Pick a pricing model', brief: 'Seats or usage?', output_type: 'x', workspace: null }])
    if (url.endsWith('/modes')) return json(MODES)
    if (url.endsWith('/agents/templates')) return json(AGENTS)
    return json({})
  }))
})
afterEach(() => { vi.unstubAllGlobals() })

function renderSetup() {
  render(
    <MemoryRouter initialEntries={['/setup/task-1']}>
      <Routes><Route path="/setup/:taskId" element={<SessionSetup />} /></Routes>
    </MemoryRouter>,
  )
}

describe('UX-12 setup start', () => {
  it('shows the task, preselects Debate, names Open Chat plainly and blocks Start with a reason', async () => {
    renderSetup()
    expect(await screen.findByText('Pick a pricing model')).toBeInTheDocument()
    expect(screen.getByText('Seats or usage?')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /debate/i })).toBeChecked()
    expect(screen.getByRole('radio', { name: /open chat/i })).toBeInTheDocument()
    expect(screen.getByText('Agents speak when they have something to add.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start session' })).toBeDisabled()
    expect(screen.getAllByText(/choose at least two agents/i).length).toBeGreaterThan(0)
    const user = userEvent.setup()
    await user.click(screen.getAllByRole('button', { name: /add to roster/i })[0])
    await user.click(screen.getAllByRole('button', { name: /add to roster/i })[0])
    expect(screen.getByRole('button', { name: 'Start session' })).toBeEnabled()
  })
})

describe('UX-13 mobile bar and filter', () => {
  it('shows seats and cost in a bar and filters the agent list', async () => {
    const user = userEvent.setup()
    renderSetup()
    await screen.findByText('Pick a pricing model')
    await user.click(screen.getAllByRole('button', { name: /add to roster/i })[0])
    expect(screen.getByText(/1 seat, about \$0\.50 \/ \$5 cap/)).toBeInTheDocument()
    await user.type(screen.getByLabelText('Filter agents'), 'build')
    expect(screen.queryByText('Skeptic', { selector: 'strong' })).not.toBeInTheDocument()
    expect(screen.getByText('Builder', { selector: 'strong' })).toBeInTheDocument()
    await user.clear(screen.getByLabelText('Filter agents'))
    await user.type(screen.getByLabelText('Filter agents'), 'zzz')
    expect(screen.getByText(/no agents match/i)).toBeInTheDocument()
  })
})

describe('UX-14 roster rows', () => {
  it('renders the full name with no truncation wrapper next to the select', async () => {
    const user = userEvent.setup()
    renderSetup()
    await screen.findByText('Pick a pricing model')
    await user.click(screen.getAllByRole('button', { name: /add to roster/i })[0])
    const row = document.querySelector('.roster-list li') as HTMLElement
    expect(row.querySelector('.roster-main .roster-name')).toHaveTextContent('Skeptic')
  })
})

describe('UX-15 budget guidance', () => {
  it('highlights the preset and offers a suggested cap when the estimate exceeds it', async () => {
    const user = userEvent.setup()
    renderSetup()
    await screen.findByText('Pick a pricing model')
    expect(screen.getByRole('button', { name: /standard/i })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /quick/i })).toHaveAttribute('aria-pressed', 'false')
    await user.click(screen.getAllByRole('button', { name: /add to roster/i })[0])
    await user.click(screen.getAllByRole('button', { name: /add to roster/i })[0])
    const cap = screen.getByLabelText(/dollar cap/i)
    await user.clear(cap); await user.type(cap, '0.2')
    await user.click(await screen.findByRole('button', { name: /use suggested \$2/i }))
    expect(cap).toHaveValue(2)
    expect(screen.getByRole('button', { name: 'Start session' })).toBeEnabled()
  })
})

describe('UX-29 agent dialog', () => {
  it('states that custom agents cannot be Build seats', () => {
    render(<AgentDialog submitting={false} error={null} onSubmit={() => {}} onCancel={() => {}} />)
    expect(screen.getByText('Custom agents join discussions only; they cannot be Build seats.')).toBeInTheDocument()
  })
})
