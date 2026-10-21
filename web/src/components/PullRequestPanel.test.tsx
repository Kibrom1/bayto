import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PullRequestPanel } from './PullRequestPanel'

const state = (over: object = {}) => ({
  branch: 'bayto/build-1234', base_branch: 'main', ready: true, opened: null,
  draft: { title: 'Add queue', body: '## Changes\n- add queue' }, ...over,
})
const res = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('PullRequestPanel', () => {
  it('shows the draft, says what will be pushed, and opens the pull request on click', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(res(state()))
      .mockResolvedValueOnce(res({ url: 'https://github.com/a/b/pull/7', head_sha: 'x', draft: false, title: 'Add queue' }, 201))
    vi.stubGlobal('fetch', fetchMock)
    render(<PullRequestPanel sessionId="s1" hasCommits />)
    await userEvent.click(screen.getByRole('button', { name: /open pull request/i }))
    expect(await screen.findByDisplayValue('Add queue')).toBeInTheDocument()
    expect(screen.getByText(/pushes/i)).toHaveTextContent('bayto/build-1234')
    await userEvent.click(screen.getByRole('button', { name: /push branch and open pull request/i }))
    const link = await screen.findByRole('link', { name: /pull\/7/ })
    expect(link).toHaveAttribute('href', 'https://github.com/a/b/pull/7')
    const sent = JSON.parse(fetchMock.mock.calls[1][1].body)
    expect(sent).toMatchObject({ title: 'Add queue', draft: false, open_unfinished: false })
  })

  it('forces a draft when the team did not finish cleanly', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(res(state({ ready: false })))
      .mockResolvedValueOnce(res({ url: 'https://github.com/a/b/pull/8', head_sha: 'x', draft: true, title: 't' }, 201))
    vi.stubGlobal('fetch', fetchMock)
    render(<PullRequestPanel sessionId="s1" hasCommits />)
    await userEvent.click(screen.getByRole('button', { name: /open pull request/i }))
    const box = await screen.findByRole('checkbox')
    expect(box).toBeChecked(); expect(box).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: /push branch/i }))
    await waitFor(() => expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ draft: true, open_unfinished: true }))
  })

  it('shows the error and stays open so the person can retry', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(res(state()))
      .mockResolvedValueOnce(res({ detail: { error: { code: 'gh_failed', detail: 'gh could not open it: not logged in' } } }, 502)))
    render(<PullRequestPanel sessionId="s1" hasCommits />)
    await userEvent.click(screen.getByRole('button', { name: /open pull request/i }))
    await userEvent.click(await screen.findByRole('button', { name: /push branch/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/not logged in/)
    expect(screen.getByRole('button', { name: /push branch/i })).toBeEnabled()
  })

  it('shows the existing link when one was already opened, and nothing without commits', () => {
    const { rerender } = render(<PullRequestPanel sessionId="s1" hasCommits opened="https://github.com/a/b/pull/3" />)
    expect(screen.getByRole('link')).toHaveAttribute('href', 'https://github.com/a/b/pull/3')
    rerender(<PullRequestPanel sessionId="s1" hasCommits={false} />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})
