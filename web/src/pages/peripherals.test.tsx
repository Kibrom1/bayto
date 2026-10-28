import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppShell } from '../components/AppShell'
import { BaytoRoom } from './BaytoRoom'
import { SessionSetup } from './SessionSetup'
import { Landing } from './Landing'
import { Services } from './Services'

afterEach(() => { vi.unstubAllGlobals(); document.title = 'Bayto' })

describe('Services (developer tools)', () => {
  it('explains the page and offers Retry when the control script is down', async () => {
    const fetchMock = vi.fn(async () => new Response('no', { status: 502 }))
    vi.stubGlobal('fetch', fetchMock)
    render(<MemoryRouter><Services /></MemoryRouter>)
    expect(screen.getByRole('heading', { level: 1, name: 'Services & logs' })).toBeInTheDocument()
    expect(screen.getByText(/only need this if you run Bayto yourself/)).toBeInTheDocument()
    expect(document.title).toBe('Services & logs · Bayto')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("The control panel isn't running")
    const before = fetchMock.mock.calls.length
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before))
  })
})

describe('navigation', () => {
  it('labels the services link "Services & logs"', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })))
    render(<MemoryRouter><Routes><Route element={<AppShell />}><Route path="/" element={<p>home</p>} /></Route></Routes></MemoryRouter>)
    expect(await screen.findByRole('link', { name: 'Services & logs' })).toHaveAttribute('href', '/services')
    expect(screen.queryByRole('link', { name: 'Services' })).toBeNull()
  })
})

describe('missing session and task reuse the NotFound page', () => {
  it('room', async () => {
    vi.stubGlobal('EventSource', class { addEventListener() {} close() {} onerror = null; onmessage = null; onopen = null })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'nope' }), { status: 404 })))
    render(<MemoryRouter initialEntries={['/room/zzz']}><Routes><Route path="/room/:sessionId" element={<BaytoRoom />} /></Routes></MemoryRouter>)
    expect(await screen.findByRole('heading', { level: 1, name: 'Session not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Tasks/ })).toHaveAttribute('href', '/')
    expect(document.title).toBe('Session not found · Bayto')
  })

  it('setup', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) =>
      String(input).endsWith('/tasks')
        ? new Response('[]', { status: 200 })
        : new Response(JSON.stringify({ detail: 'nope' }), { status: 404 })))
    render(<MemoryRouter initialEntries={['/setup/zzz']}><Routes><Route path="/setup/:taskId" element={<SessionSetup />} /></Routes></MemoryRouter>)
    expect(await screen.findByRole('heading', { level: 1, name: 'Task not found' })).toBeInTheDocument()
  })
})

describe('landing', () => {
  it('names the tab and marks a screenshot loaded or failed so its skeleton stops', () => {
    render(<MemoryRouter><Landing /></MemoryRouter>)
    expect(document.title).toBe('Welcome · Bayto')
    const img = document.querySelector('.ld-shots img') as HTMLImageElement
    expect(img).not.toBeNull()
    expect(img.dataset.loaded).toBeUndefined()
    img.dispatchEvent(new Event('load'))
    expect(img.dataset.loaded).toBe('1')
  })
})
