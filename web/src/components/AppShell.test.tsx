import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { feedbackHref } from '../api/appConfig'
import { AppShell } from './AppShell'

function mockConfig(feedback_url: string | null | 'fail') {
  vi.stubGlobal('fetch', vi.fn(async () =>
    feedback_url === 'fail' ? new Response('x', { status: 500 }) : new Response(JSON.stringify({ feedback_url }), { status: 200 })))
}
function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<p>home</p>} />
          <Route path="/room/:sessionId" element={<p>room</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}
afterEach(() => vi.unstubAllGlobals())

describe('feedback link', () => {
  it('is absent when nothing is configured or the config cannot be read', async () => {
    mockConfig(null)
    const { unmount } = renderAt('/')
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/app-config'))
    expect(screen.queryByText('Send feedback')).toBeNull()
    unmount()
    mockConfig('fail')
    renderAt('/')
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByText('Send feedback')).toBeNull()
  })

  it('opens in a new tab with noopener noreferrer', async () => {
    mockConfig('https://forms.example.com/bayto')
    renderAt('/')
    const a = await screen.findByRole('link', { name: 'Send feedback' })
    expect(a).toHaveAttribute('href', 'https://forms.example.com/bayto')
    expect(a).toHaveAttribute('target', '_blank')
    expect(a).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('adds only the session id to an https URL when a room is open', async () => {
    mockConfig('https://forms.example.com/bayto?src=app')
    renderAt('/room/abc-123')
    const a = await screen.findByRole('link', { name: 'Send feedback' })
    expect(a).toHaveAttribute('href', 'https://forms.example.com/bayto?src=app&session=abc-123')
  })

  it('adds a subject, and nothing from the room, to a mailto URL', async () => {
    mockConfig('mailto:pilot@example.com')
    renderAt('/room/abc-123')
    const a = await screen.findByRole('link', { name: 'Send feedback' })
    expect(a).toHaveAttribute('href', 'mailto:pilot@example.com?subject=Bayto%20pilot%20feedback')
  })
})

describe('feedbackHref', () => {
  it('leaves https alone outside a room and ignores odd session ids', () => {
    expect(feedbackHref('https://f.example/x', null)).toBe('https://f.example/x')
    expect(feedbackHref('https://f.example/x', 'a b&c=1')).toBe('https://f.example/x')
  })
  it('never appends the session id to mailto, and keeps an existing subject', () => {
    expect(feedbackHref('mailto:a@b.co', 'abc')).not.toContain('abc')
    expect(feedbackHref('mailto:a@b.co?subject=Hi', 'abc')).toBe('mailto:a@b.co?subject=Hi')
    expect(feedbackHref('mailto:a@b.co?cc=c@d.co', null)).toBe('mailto:a@b.co?cc=c@d.co&subject=Bayto%20pilot%20feedback')
  })
  it('refuses other schemes and junk', () => {
    for (const bad of ['javascript:alert(1)', 'data:text/html,x', 'http://x.example', 'nope', '', null, undefined])
      expect(feedbackHref(bad as string, 'abc')).toBeNull()
  })
})
