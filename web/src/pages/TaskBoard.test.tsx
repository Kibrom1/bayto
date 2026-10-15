import { render, screen, waitFor } from '@testing-library/react'
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
    expect(screen.getByText('Done')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /view result/i })).toHaveAttribute('href', '/room/s1')

    expect(screen.getByText('Review the pricing page')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /start session/i })).toHaveAttribute('href', '/setup/t2')

    expect(fetch).toHaveBeenCalledWith('/tasks')
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
})
