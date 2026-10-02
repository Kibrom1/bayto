import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SessionSetup } from './SessionSetup'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('SessionSetup', () => {
  it('shows the agent explorer and updates the estimate when agents are added', async () => {
    const user = userEvent.setup()
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify([
          {
            id: 'task-1',
            title: 'Write a launch memo',
            brief: 'Draft it',
            output_type: 'memo',
            status: null,
            last_session_id: null,
            output_artifact_id: null,
          },
        ]),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    )

    render(
      <MemoryRouter initialEntries={['/tasks/task-1/session-setup']}>
        <Routes>
          <Route path="/tasks/:taskId/session-setup" element={<SessionSetup />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/session setup/i)).toBeInTheDocument()
    expect(screen.getByText(/agent explorer/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /add to roster/i }))

    expect(screen.getByText(/selected roster/i)).toBeInTheDocument()
    expect(screen.getByText(/estimated cost/i)).toBeInTheDocument()
    expect(screen.getByText(/% of budget cap/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start session/i })).toBeEnabled()
  })
})
