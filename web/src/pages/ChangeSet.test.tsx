import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ArtifactOut } from '../api/sessions'
import { ChangeSet } from './ChangeSet'
import { stopReasonText } from './BaytoRoom'

const base = {
  branch: 'bayto/build-12345678', base_branch: 'main', switch_command: 'git switch bayto/build-12345678',
  status: 'ready_to_merge', commits: [{ sha: 'abc123def456', subject: 'feat: add queue' }],
  files: [{ status: 'A', path: 'src/queue.py' }, { status: 'M', path: 'src/app.py' }],
  files_changed: 2, insertions: 40, deletions: 3, fix_cycles: 1, open_issues: [],
  tests: { command: 'pytest -q', exit_code: 0, tail: '12 passed in 1.1s' },
  review: { verdict: 'approve', notes: 'Reads well.' },
}
const art = (over: object = {}): ArtifactOut =>
  ({ id: 'a', type: 'change_set', content_json: { ...base, ...over }, source_turn_ids: [] }) as ArtifactOut

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('ChangeSet', () => {
  it('renders nothing when the session has no change set', () => {
    const { container } = render(<ChangeSet artifacts={[{ id: 's', type: 'synthesis', content_json: {}, source_turn_ids: [] }]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows a finished build: branch, tests with exit code, review, commits and counts', () => {
    render(<ChangeSet artifacts={[art()]} />)
    expect(screen.getByText('Ready to review and merge')).toBeInTheDocument()
    expect(screen.getByText('git switch bayto/build-12345678')).toBeInTheDocument()
    expect(screen.getByText('exit 0')).toBeInTheDocument()
    expect(screen.getByText('pytest -q')).toBeInTheDocument()
    expect(screen.getByText('12 passed in 1.1s')).toBeInTheDocument()
    expect(screen.getByText('approve')).toBeInTheDocument()
    expect(screen.getByText(/feat: add queue/)).toBeInTheDocument()
    expect(screen.getByText('+40')).toBeInTheDocument()
    expect(screen.getByText('−3')).toBeInTheDocument()
  })

  it('says plainly when a build is not finished, and what is open', () => {
    render(<ChangeSet artifacts={[art({
      status: 'needs_you', commits: [], tests: null, review: null,
      open_issues: ['The session ended early (user_stop); review the branch before trusting it.', 'No commits landed on the branch.'],
    })]} />)
    expect(screen.getByText('Needs your attention')).toBeInTheDocument()
    expect(screen.getByText(/ended early/)).toBeInTheDocument()
    expect(screen.getByText('The tester did not report a test run.')).toBeInTheDocument()
    expect(screen.getByText('The reviewer did not report.')).toBeInTheDocument()
  })

  it('marks a failing test run as failed, not as passed', () => {
    render(<ChangeSet artifacts={[art({ tests: { command: 'pytest', exit_code: 1, tail: '1 failed' } })]} />)
    expect(screen.getByText('exit 1')).toHaveClass('bad')
  })

  it('copies the git switch command', async () => {
    const user = userEvent.setup() // installs its own clipboard stub; spy on that one
    const writeText = vi.spyOn(navigator.clipboard, 'writeText')
    render(<ChangeSet artifacts={[art()]} />)
    await user.click(screen.getByRole('button', { name: 'Copy' }))
    expect(writeText).toHaveBeenCalledWith('git switch bayto/build-12345678')
  })
})

describe('stop reason', () => {
  it('explains a completed build', () => {
    expect(stopReasonText('build_complete')).toMatch(/tests passed/)
  })
})
