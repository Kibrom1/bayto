import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NewTaskForm } from './NewTaskForm'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => { vi.stubGlobal('fetch', vi.fn()) })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

async function fillBasics(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Title'), 'Add a queue')
  await user.type(screen.getByLabelText('Brief'), 'Replace the cron job with a queue.')
}

describe('NewTaskForm in Build mode', () => {
  it('hides the repository fields until the task is marked as changing code', async () => {
    render(<NewTaskForm submitting={false} error={null} onSubmit={() => {}} onCancel={() => {}} />)
    expect(screen.queryByLabelText('Repository path')).not.toBeInTheDocument()
    await userEvent.setup().click(screen.getByLabelText(/this task changes code/i))
    expect(screen.getByLabelText('Repository path')).toBeInTheDocument()
    expect(screen.getByLabelText('Output type')).toHaveValue('code change')
  })

  it('needs a repository path before it can be created', async () => {
    const user = userEvent.setup()
    render(<NewTaskForm submitting={false} error={null} onSubmit={() => {}} onCancel={() => {}} />)
    await fillBasics(user)
    await user.type(screen.getByLabelText('Output type'), 'decision')
    expect(screen.getByRole('button', { name: 'Create task' })).toBeEnabled()
    await user.click(screen.getByLabelText(/this task changes code/i))
    expect(screen.getByRole('button', { name: 'Create task' })).toBeDisabled()
  })

  it('checks the repository and tells you when the working tree is dirty', async () => {
    vi.mocked(fetch).mockResolvedValue(json({
      ok: true, problems: [], repo_root: '/r', current_branch: 'main', dirty: true, dirty_files: ['a.py'],
    }))
    const user = userEvent.setup()
    render(<NewTaskForm submitting={false} error={null} onSubmit={() => {}} onCancel={() => {}} />)
    await user.click(screen.getByLabelText(/this task changes code/i))
    await user.type(screen.getByLabelText('Repository path'), '/r')
    await user.click(screen.getByRole('button', { name: 'Check repository' }))
    expect(await screen.findByRole('status')).toHaveTextContent(/uncommitted changes \(1 file\)/)
    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(String(url)).toBe('/workspaces/preflight')
    expect(JSON.parse(init!.body as string)).toEqual({ repo_path: '/r', base_branch: 'main', test_command: null, allowed_commands: [] })
  })

  it('shows the reason a repository cannot be used', async () => {
    vi.mocked(fetch).mockResolvedValue(json({
      ok: false, problems: [{ code: 'not_a_git_repo', detail: '/x is not inside a git repository.' }],
      repo_root: null, current_branch: null, dirty: false, dirty_files: [],
    }))
    const user = userEvent.setup()
    render(<NewTaskForm submitting={false} error={null} onSubmit={() => {}} onCancel={() => {}} />)
    await user.click(screen.getByLabelText(/this task changes code/i))
    await user.type(screen.getByLabelText('Repository path'), '/x')
    await user.click(screen.getByRole('button', { name: 'Check repository' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('/x is not inside a git repository.')
  })

  it('submits the workspace with the task', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    render(<NewTaskForm submitting={false} error={null} onSubmit={onSubmit} onCancel={() => {}} />)
    await fillBasics(user)
    await user.click(screen.getByLabelText(/this task changes code/i))
    await user.type(screen.getByLabelText('Repository path'), ' /code/app ')
    await user.type(screen.getByLabelText('Test command'), 'npm test')
    await user.click(screen.getByRole('button', { name: 'Create task' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalled())
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      output_type: 'code change',
      workspace: { repo_path: '/code/app', base_branch: 'main', test_command: 'npm test' },
    })
  })

  it('sends the extra allowed commands, one per line', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    render(<NewTaskForm submitting={false} error={null} onSubmit={onSubmit} onCancel={() => {}} />)
    await fillBasics(user)
    await user.click(screen.getByLabelText(/this task changes code/i))
    await user.type(screen.getByLabelText('Repository path'), '/code/app')
    await user.type(screen.getByLabelText('Test command'), 'uv run pytest -q')
    await user.type(screen.getByLabelText(/other allowed commands/i), 'uv sync{enter}  npm run lint  {enter}')
    await user.click(screen.getByRole('button', { name: 'Create task' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalled())
    expect(onSubmit.mock.calls[0][0].workspace.allowed_commands).toEqual(['uv sync', 'npm run lint'])
  })

  it('sends no workspace for a discussion task', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    render(<NewTaskForm submitting={false} error={null} onSubmit={onSubmit} onCancel={() => {}} />)
    await fillBasics(user)
    await user.type(screen.getByLabelText('Output type'), 'decision')
    await user.click(screen.getByRole('button', { name: 'Create task' }))
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('workspace')
  })
})
