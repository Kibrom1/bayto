import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TaskBoard } from './TaskBoard'

const TASKS = [
  {
    id: 't1',
    title: 'Write a launch memo',
    brief: 'Draft it',
    output_type: 'memo',
    status: 'completed',
    last_session_id: 's1',
    output_artifact_id: 'a1',
  },
  {
    id: 't2',
    title: 'Review the pricing page',
    brief: 'Review it',
    output_type: 'review',
    status: null,
    last_session_id: null,
    output_artifact_id: null,
  },
]

function jsonResponse(body: unknown, init?: ResponseInit) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('TaskBoard', () => {
  it('lists tasks from GET /tasks with status, last session and output artifact', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(TASKS))

    render(<MemoryRouter><TaskBoard /></MemoryRouter>)

    expect(await screen.findByText('Write a launch memo')).toBeInTheDocument()
    expect(screen.getByText('Finished', { selector: '.chip' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /view result/i })).toHaveAttribute('href', '/room/s1')

    expect(screen.getByText('Review the pricing page')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /start session/i })).toHaveAttribute('href', '/setup/t2')

    expect(fetch).toHaveBeenCalledWith('/tasks')
  })

  it('archives or deletes a task\'s last session from the row menu, asking before it deletes', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation(async (url, init) => {
      if (url === '/tasks') return jsonResponse(TASKS)
      return jsonResponse({}, { status: (init as RequestInit | undefined)?.method === 'DELETE' ? 204 : 200 })
    })
    render(<MemoryRouter><TaskBoard /></MemoryRouter>)
    await screen.findByText('Write a launch memo')
    expect(screen.getAllByLabelText(/More actions for/)).toHaveLength(2)  // every task that is not running

    await userEvent.click(screen.getAllByLabelText(/More actions for/)[0])
    await userEvent.click(screen.getByRole('button', { name: 'Archive' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/sessions/s1/archive', { method: 'POST' }))

    await userEvent.click(screen.getAllByLabelText(/More actions for/)[0])
    await userEvent.click(screen.getByRole('button', { name: 'Delete session' }))
    expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'DELETE')).toBe(false)
    const confirmBtns = await screen.findAllByRole('button', { name: 'Delete session' })
    await userEvent.click(confirmBtns[confirmBtns.length - 1])
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/sessions/s1', { method: 'DELETE' }))
  })

  it('deletes a task that never ran, after asking', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation(async (url, init) => {
      if (url === '/tasks') return jsonResponse(TASKS)
      return jsonResponse({}, { status: (init as RequestInit | undefined)?.method === 'DELETE' ? 204 : 200 })
    })
    render(<MemoryRouter><TaskBoard /></MemoryRouter>)
    await screen.findByText('Write a launch memo')
    const more = screen.getAllByLabelText(/More actions for/)
    await userEvent.click(more[more.length - 1])
    expect(screen.getAllByRole('button', { name: 'Archive' })).toHaveLength(1) // only the task with a session
    const menuItems = screen.getAllByRole('button', { name: 'Delete task' })
    await userEvent.click(menuItems[menuItems.length - 1])
    const confirm = await screen.findAllByRole('button', { name: 'Delete task' })
    await userEvent.click(confirm[confirm.length - 1])
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/tasks/t2', { method: 'DELETE' }))
  })

  it('shows an empty state when there are no tasks', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse([]))

    render(<MemoryRouter><TaskBoard /></MemoryRouter>)

    expect(await screen.findByText(/no tasks yet/i)).toBeInTheDocument()
  })

  it('shows an error when GET /tasks fails', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ detail: 'db down' }, { status: 500, statusText: 'Internal Server Error' }),
    )

    render(<MemoryRouter><TaskBoard /></MemoryRouter>)

    expect(await screen.findByRole('alert')).toHaveTextContent('db down')
  })

  it('opens the new task editor and creates a task via POST /tasks', async () => {
    const user = userEvent.setup()
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse([]))

    render(<MemoryRouter><TaskBoard /></MemoryRouter>)
    await screen.findByText(/no tasks yet/i)

    await user.click(screen.getByRole('button', { name: /new task/i }))

    await user.type(screen.getByLabelText(/title/i), 'Ship the newsletter')
    await user.type(screen.getByLabelText(/brief/i), 'Write and send it')
    await user.type(screen.getByLabelText(/output type/i), 'email')
    await user.type(screen.getByLabelText(/success criteria/i), 'Opens above 30%')

    const created = {
      id: 't3',
      title: 'Ship the newsletter',
      brief: 'Write and send it',
      output_type: 'email',
      status: null,
      last_session_id: null,
      output_artifact_id: null,
    }
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ task: created }, { status: 201 }))

    await user.click(screen.getByRole('button', { name: /create task/i }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByText('Ship the newsletter')).toBeInTheDocument()

    expect(fetch).toHaveBeenLastCalledWith(
      '/tasks',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          title: 'Ship the newsletter',
          brief: 'Write and send it',
          output_type: 'email',
          success_criteria: 'Opens above 30%',
        }),
      }),
    )
  })

  it('does not build any file/context upload UI (text-only briefs in v1)', async () => {
    const user = userEvent.setup()
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse([]))

    render(<MemoryRouter><TaskBoard /></MemoryRouter>)
    await screen.findByText(/no tasks yet/i)
    await user.click(screen.getByRole('button', { name: /new task/i }))

    expect(screen.queryByLabelText(/file|upload|attachment/i)).not.toBeInTheDocument()
    expect(document.querySelector('input[type="file"]')).toBeNull()
  })

  it('moves through the row menu with the arrow keys and closes it with Escape', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(TASKS))
    render(<MemoryRouter><TaskBoard /></MemoryRouter>)
    const more = (await screen.findAllByLabelText(/More actions for/))[0]
    await userEvent.click(more)
    const archive = screen.getByRole('button', { name: 'Archive' })
    const del = screen.getByRole('button', { name: 'Delete session' })
    archive.focus()
    await userEvent.keyboard('{ArrowDown}')
    expect(del).toHaveFocus()
    await userEvent.keyboard('{ArrowUp}')
    expect(archive).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    expect(more.closest('details')).not.toHaveAttribute('open')
    expect(more).toHaveFocus()
  })

  it('offers a sample discussion on an empty board and opens its setup', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation(async (url, init) => {
      if (url === '/tasks' && (init as RequestInit | undefined)?.method === 'POST') {
        return jsonResponse({ task: { id: 'sample-1', title: 'Should we ship weekly or every two weeks?', brief: 'b', output_type: 'decision', status: null, last_session_id: null, output_artifact_id: null } }, { status: 201 })
      }
      return jsonResponse([])
    })
    render(<MemoryRouter><TaskBoard /></MemoryRouter>)
    await userEvent.click(await screen.findByRole('button', { name: /try a sample discussion/i }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([u, i]) => u === '/tasks' && (i as RequestInit | undefined)?.method === 'POST')).toBe(true))
  })
})

describe('TaskBoard transcript search', () => {
  it('searches past discussions, shows snippets as text with the match marked, and links to the room', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(TASKS)).mockResolvedValueOnce(jsonResponse({
      query: 'indemnity',
      results: [{ task_id: 't1', task_title: 'Write a launch memo', session_id: 's9', message_id: 'm1', speaker: 'lawyer',
        kind: 'answer', snippet: 'push for <img src=x> [[indemnity]] limits', created_at: null, rank: 1 }],
    }))
    render(<MemoryRouter><TaskBoard /></MemoryRouter>)
    await screen.findByText('Review the pricing page')
    await userEvent.type(screen.getByRole('searchbox', { name: /search past discussions/i }), 'indemnity')
    await userEvent.click(screen.getByRole('button', { name: /^search$/i }))
    const results = await screen.findByRole('region', { name: /search results/i })
    expect(results.querySelector('mark')?.textContent).toBe('indemnity')
    expect(results.querySelector('img')).toBeNull()
    expect(results).toHaveTextContent('<img src=x>')
    expect(screen.getAllByRole('link', { name: 'Write a launch memo' }).some((a) => a.getAttribute('href') === '/room/s9')).toBe(true)
    expect(vi.mocked(fetch).mock.calls[1][0]).toBe('/search?q=indemnity')
  })

  it('does not call the server for a too-short query, and shows a server error', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(TASKS))
    render(<MemoryRouter><TaskBoard /></MemoryRouter>)
    await screen.findByText('Review the pricing page')
    const box = screen.getByRole('searchbox', { name: /search past discussions/i })
    await userEvent.type(box, 'a')
    await userEvent.click(screen.getByRole('button', { name: /^search$/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/at least 2/i)
    expect(fetch).toHaveBeenCalledTimes(1)
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ detail: 'boom' }, { status: 500 }))
    await userEvent.type(box, 'b')
    await userEvent.click(screen.getByRole('button', { name: /^search$/i }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/boom/))
  })

  it('says so when nothing matches', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(TASKS)).mockResolvedValueOnce(jsonResponse({ query: 'zz', results: [] }))
    render(<MemoryRouter><TaskBoard /></MemoryRouter>)
    await screen.findByText('Review the pricing page')
    await userEvent.type(screen.getByRole('searchbox', { name: /search past discussions/i }), 'zzz')
    await userEvent.click(screen.getByRole('button', { name: /^search$/i }))
    expect(await screen.findByText(/no past discussion matches/i)).toBeInTheDocument()
  })
})

describe('TaskBoard wording and row actions', () => {
  const T = (over: object) => ({ id: 'x', title: 'X', brief: 'b', output_type: 'memo', status: null, last_session_id: null, output_artifact_id: null, ...over })
  async function board(tasks: object[]) {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(tasks))
    render(<MemoryRouter><TaskBoard /></MemoryRouter>)
    await screen.findByRole('list')
  }

  it('sets the tab title', async () => {
    await board([T({})])
    expect(document.title).toBe('Tasks · Bayto')
  })

  it('labels a created-but-unstarted task like its filter, and the filter finds it', async () => {
    await board([T({ id: 'a', title: 'Alpha', status: 'created', last_session_id: 's1' }), T({ id: 'b', title: 'Beta', status: 'finished', last_session_id: 's2' })])
    const row = screen.getByText('Alpha').closest('li')!
    expect(row).toHaveTextContent('Not started')
    await userEvent.click(screen.getByRole('button', { name: 'Not started' }))
    expect(screen.getByText('Alpha')).toBeInTheDocument()
    expect(screen.queryByText('Beta')).toBeNull()
  })

  it('gives each state the action that fits it', async () => {
    await board([
      T({ id: '1', title: 'Paused one', status: 'needs_human', last_session_id: 's1' }),
      T({ id: '2', title: 'Failed one', status: 'failed', last_session_id: 's2' }),
      T({ id: '3', title: 'Ready one', status: 'created', last_session_id: 's3' }),
    ])
    const links = (title: string) => within(screen.getByText(title).closest('li')!).getAllByRole('link').map((l) => l.textContent)
    expect(links('Paused one')).toContain('Resume')
    expect(links('Failed one')).toEqual(['View session', 'Retry'])
    expect(links('Ready one')).toContain('Open room')
    expect(within(screen.getByText('Failed one').closest('li')!).getByRole('link', { name: 'Retry' })).toHaveAttribute('href', '/setup/2')
  })

  it('renders More as a kebab with a per-row accessible name', async () => {
    await board([T({ id: '1', title: 'Alpha', status: 'finished', last_session_id: 's1' })])
    const kebab = screen.getByRole('group', { hidden: true, name: 'Task actions' }).closest('details')!.querySelector('summary')!
    expect(kebab).toHaveAttribute('aria-label', 'More actions for Alpha')
    expect(kebab.textContent).toBe('')
  })

  it('relabels the first search as a title filter', async () => {
    await board([T({})])
    expect(screen.getByLabelText('Filter by title')).toBeInTheDocument()
    expect(screen.queryByLabelText('Search tasks')).toBeNull()
  })

  it('names the query in the empty state and clears it', async () => {
    await board([T({ title: 'Alpha' })])
    await userEvent.type(screen.getByLabelText('Filter by title'), 'zzz')
    expect(screen.getByRole('status')).toHaveTextContent('No tasks match “zzz”')
    await userEvent.click(screen.getByRole('button', { name: 'Clear search' }))
    expect(screen.getByText('Alpha')).toBeInTheDocument()
  })

  it('shows a distinct "Change ready" chip and sorts it above plain results', async () => {
    await board([
      T({ id: '1', title: 'Plain result', status: 'finished', last_session_id: 's1', output_artifact_id: 'a1' }),
      T({ id: '2', title: 'Code task', status: 'finished', last_session_id: 's2', output_artifact_id: 'a2', workspace: { repo_path: '/r', base_branch: 'main' } }),
    ])
    const titles = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(titles).toEqual(['Code task', 'Plain result'])
    const change = screen.getByText('Change ready')
    expect(change).toHaveClass('change')
    expect(screen.getByText('Synthesis ready')).not.toHaveClass('change')
  })
})

describe('UX-18 status filters', () => {
  async function board(tasks: object[]) {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(tasks))
    render(<MemoryRouter><TaskBoard /></MemoryRouter>)
    await screen.findByRole('list')
  }
  const T = (o: object) => ({ id: 'x', title: 'T', brief: '', output_type: 'x', status: null, last_session_id: null, output_artifact_id: null, ...o })

  it('offers a filter for every status label and the chip text matches', async () => {
    await board([
      T({ id: '1', title: 'Paused one', status: 'needs_human', last_session_id: 's1' }),
      T({ id: '2', title: 'Failed one', status: 'failed', last_session_id: 's2' }),
      T({ id: '3', title: 'Cut one', status: 'orphaned', last_session_id: 's3' }),
      T({ id: '4', title: 'Done one', status: 'finished', last_session_id: 's4' }),
    ])
    const group = screen.getByRole('group', { name: 'Filter tasks' })
    for (const name of ['Paused', 'Failed', 'Interrupted', 'Finished']) expect(within(group).getByRole('button', { name })).toBeInTheDocument()
    await userEvent.click(within(group).getByRole('button', { name: 'Failed' }))
    expect(screen.getByText('Failed one')).toBeInTheDocument()
    expect(screen.queryByText('Paused one')).toBeNull()
    await userEvent.click(within(group).getByRole('button', { name: 'Interrupted' }))
    expect(screen.getByText('Cut one')).toBeInTheDocument()
    expect(screen.queryByText('Failed one')).toBeNull()
    await userEvent.click(within(group).getByRole('button', { name: 'Finished' }))
    expect(screen.getByText('Done one')).toBeInTheDocument()
    expect(screen.queryByText('Cut one')).toBeNull()
  })
})
