import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SessionSetup } from './SessionSetup'

const MODES = [
  { id: 'm1', name: 'brainstorm', floor_policy: 'round_robin', stop_rules: {} },
  { id: 'm2', name: 'debate', floor_policy: 'round_robin', stop_rules: {} },
]
const BUILD_MODE = { id: 'm3', name: 'build', floor_policy: 'pipeline', stop_rules: {} }
const BUILD_AGENTS = ['architect', 'developer', 'tester', 'reviewer'].map((r, i) => (
  { id: `b${i}`, name: `Build ${r}`, role: `build-${r}`, system_prompt: '', stance: 'x', model: 'sonnet' }
))
const AGENTS = [
  { id: 'a1', name: 'Skeptic', role: 'Challenges claims', system_prompt: '', stance: 'critical', model: 'sonnet' },
  { id: 'a2', name: 'Builder', role: 'Proposes designs', system_prompt: '', stance: 'constructive', model: 'sonnet' },
]

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mockApi(opts: { build?: boolean; workspace?: object | null } = {}) {
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/tasks')) {
      return json([{ id: 'task-1', title: 't', brief: null, output_type: 'code change', workspace: opts.workspace ?? null }])
    }
    if (url.endsWith('/workspaces/preflight')) {
      return json({ ok: true, problems: [], repo_root: '/code/app', current_branch: 'main', dirty: false, dirty_files: [],
        suggested_test_command: 'uv run pytest',
        permissions: [{ role: 'build-tester', can_run: ['git status'], can_run_tests: false, note: 'Runs the tests.' }] })
    }
    if (url.endsWith('/tasks/task-1/workspace') && init?.method === 'PUT') return json({ task: { id: 'task-1' } })
    if (url.endsWith('/modes')) return json(opts.build ? [...MODES, BUILD_MODE] : MODES)
    if (url.endsWith('/agents/templates')) return json(opts.build ? [...AGENTS, ...BUILD_AGENTS] : AGENTS)
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

    // Debate is preselected, Start is disabled until two agents are seated and says why.
    expect(screen.getByRole('button', { name: /start session/i })).toBeDisabled()
    expect(screen.getAllByText(/choose at least two agents/i).length).toBeGreaterThan(0)
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

describe('SessionSetup in Build mode', () => {
  const WS = { repo_path: '/code/app', base_branch: 'main', test_command: 'npm test' }

  it('seats the four build roles, hides discussion agents and raises the budget', async () => {
    const user = userEvent.setup()
    mockApi({ build: true, workspace: WS })
    renderSetup()
    await screen.findByRole('heading', { name: /session setup/i })
    expect(screen.queryByText('Build architect')).not.toBeInTheDocument() // build agents are hidden in discussion modes

    await user.click(screen.getByRole('radio', { name: /^build/i }))

    expect(screen.getAllByText(/^Build (architect|developer|tester|reviewer)$/).length).toBeGreaterThanOrEqual(4)
    expect(screen.queryByText('Skeptic')).not.toBeInTheDocument()
    expect(screen.getByLabelText(/dollar cap/i)).toHaveValue(15)
    expect(await screen.findByLabelText('Repository path')).toHaveValue('/code/app')
    expect(screen.getByRole('button', { name: /start session/i })).toBeEnabled()
  })

  it('asks for a repository when the task has none, and saves it before starting', async () => {
    const user = userEvent.setup()
    mockApi({ build: true })
    renderSetup()
    await screen.findByRole('heading', { name: /session setup/i })
    await user.click(screen.getByRole('radio', { name: /^build/i }))

    await user.click(screen.getByRole('button', { name: /start session/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/enter the repository path/i)

    await user.type(await screen.findByLabelText('Repository path'), '/code/app')
    await user.click(screen.getByRole('button', { name: /start session/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/set a test command/i)

    await user.click(screen.getByRole('button', { name: /check repository/i }))
    await user.click(await screen.findByRole('button', { name: /use uv run pytest/i }))
    expect(await screen.findByText(/cannot run tests/i)).toBeInTheDocument() // the table is from the check, before the command was set
    expect(screen.getByLabelText('Test command')).toHaveValue('uv run pytest')
    await user.click(screen.getByRole('button', { name: /start session/i }))

    await waitFor(() => expect(screen.getByText('room page')).toBeInTheDocument())
    const calls = vi.mocked(fetch).mock.calls.map(([u, i]) => `${i?.method ?? 'GET'} ${String(u)}`)
    expect(calls.indexOf('PUT /tasks/task-1/workspace')).toBeGreaterThan(-1)
    expect(calls.indexOf('PUT /tasks/task-1/workspace')).toBeLessThan(calls.indexOf('POST /sessions'))
  })

  it('goes back to discussion agents when you leave Build', async () => {
    const user = userEvent.setup()
    mockApi({ build: true, workspace: WS })
    renderSetup()
    await screen.findByRole('heading', { name: /session setup/i })
    await user.click(screen.getByRole('radio', { name: /^build/i }))
    await user.click(screen.getByRole('radio', { name: /debate/i }))
    expect(screen.getByText('Skeptic')).toBeInTheDocument()
    expect(screen.queryByText('Build architect')).not.toBeInTheDocument()
    expect(screen.getByLabelText(/dollar cap/i)).toHaveValue(5)
  })
})

describe('SessionSetup with an unknown task', () => {
  it('says the task does not exist instead of showing a setup form for it', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/tasks')) return json([{ id: 'other-task', title: 'x', brief: null, output_type: 'memo' }])
      if (url.endsWith('/modes')) return json(MODES)
      if (url.endsWith('/agents/templates')) return json(AGENTS)
      return json({ detail: 'nope' }, 404)
    })
    renderSetup()
    expect(await screen.findByText(/that task doesn't exist/i)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /session setup/i })).not.toBeInTheDocument()
  })
})
