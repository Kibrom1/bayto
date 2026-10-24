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

describe('NewTaskForm semantic search', () => {
  it('is off unless ticked, and the label says document text leaves the machine', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<NewTaskForm submitting={false} error={null} onSubmit={onSubmit} onCancel={() => {}} />)
    await fillBasics(user)
    await user.type(screen.getByLabelText('Output type'), 'decision')
    await user.click(screen.getByRole('button', { name: 'Create task' }))
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('semantic_retrieval')
    cleanup()
    onSubmit.mockClear()
    render(<NewTaskForm submitting={false} error={null} onSubmit={onSubmit} onCancel={() => {}} />)
    await fillBasics(user)
    await user.type(screen.getByLabelText('Output type'), 'decision')
    await user.click(screen.getByLabelText('Search long documents by meaning'))
    await user.click(screen.getByRole('button', { name: 'Create task' }))
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ semantic_retrieval: true })
  })
})

describe('NewTaskForm layout and wording', () => {
  it('offers output types as chips that fill the field', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<NewTaskForm submitting={false} error={null} onSubmit={onSubmit} onCancel={() => {}} />)
    const group = screen.getByRole('group', { name: 'Quick picks' })
    for (const name of ['Decision', 'Plan', 'List', 'Document', 'Code']) {
      expect(group).toHaveTextContent(name)
    }
    await user.click(screen.getByRole('button', { name: 'Plan' }))
    expect(screen.getByRole('button', { name: 'Plan' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('Output type')).toHaveValue('plan')
    await fillBasics(user)
    await user.click(screen.getByRole('button', { name: 'Create task' }))
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ output_type: 'plan' }))
  })

  it('keeps Cancel and Create in a footer outside the scrolling body', () => {
    render(<NewTaskForm submitting={false} error={null} onSubmit={() => {}} onCancel={() => {}} />)
    const footer = screen.getByRole('button', { name: 'Create task' }).closest('.modal-actions')
    expect(footer).not.toBeNull()
    expect(footer!.closest('.modal-body')).toBeNull()
    expect(footer!.previousElementSibling).toHaveClass('modal-body')
  })

  it('does not show environment variable names in help text', () => {
    render(<NewTaskForm submitting={false} error={null} onSubmit={() => {}} onCancel={() => {}} />)
    expect(document.body.textContent).not.toMatch(/OPENAI_API_KEY|OLLAMA_HOST|BAYTO_[A-Z_]+/)
  })

  it('reveals the repository fields inline when the Build box is ticked', async () => {
    render(<NewTaskForm submitting={false} error={null} onSubmit={() => {}} onCancel={() => {}} />)
    await userEvent.setup().click(screen.getByLabelText(/this task changes code/i))
    expect(screen.getByLabelText('Repository path').closest('.modal-body')).not.toBeNull()
  })
})

describe('UX-11 retrieval switch in New task', () => {
  it('uses the plain label at regular weight and still says the text leaves the machine', () => {
    render(<NewTaskForm submitting={false} error={null} onSubmit={() => {}} onCancel={() => {}} />)
    const box = screen.getByLabelText('Search long documents by meaning')
    expect(box.closest('.field')).toHaveClass('semantic-toggle')
    expect(screen.getByText(/text is sent to an embedding provider/i)).toBeInTheDocument()
  })
})
