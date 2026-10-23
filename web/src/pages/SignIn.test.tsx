import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppShell } from '../components/AppShell'

function stub(config: object, loginStatus = 200) {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? 'GET'} ${url}`)
    if (url === '/app-config') return new Response(JSON.stringify(config), { status: 200 })
    if (url === '/auth/login') {
      return loginStatus === 200 ? new Response('{"authenticated":true}', { status: 200 })
        : new Response(JSON.stringify({ detail: { error: { code: 'invalid_token', detail: 'that token is not right' } } }), { status: loginStatus })
    }
    return new Response('{}', { status: 200 })
  }))
  return calls
}
const renderApp = () => render(
  <MemoryRouter><Routes><Route element={<AppShell />}><Route path="/" element={<p>home</p>} /></Route></Routes></MemoryRouter>,
)
afterEach(() => vi.unstubAllGlobals())

describe('sign-in gate', () => {
  it('shows the sign-in page, not the app, when auth is required and not authenticated', async () => {
    stub({ feedback_url: null, auth_required: true, authenticated: false })
    renderApp()
    expect(await screen.findByLabelText('Access token')).toBeInTheDocument()
    expect(screen.queryByText('home')).toBeNull()
  })

  it('shows the app with a sign-out button when authenticated, and no button when auth is off', async () => {
    stub({ feedback_url: null, auth_required: true, authenticated: true })
    const { unmount } = renderApp()
    expect(await screen.findByText('home')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
    unmount()
    stub({ feedback_url: null })
    renderApp()
    expect(await screen.findByText('home')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sign out' })).toBeNull()
  })

  it('signs in with the token and then shows the app', async () => {
    const calls = stub({ feedback_url: null, auth_required: true, authenticated: false })
    renderApp()
    await userEvent.type(await screen.findByLabelText('Access token'), 'tok')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('home')).toBeInTheDocument()
    expect(calls).toContain('POST /auth/login')
  })

  it('shows the server message on a wrong token and stays on the page', async () => {
    stub({ feedback_url: null, auth_required: true, authenticated: false }, 401)
    renderApp()
    await userEvent.type(await screen.findByLabelText('Access token'), 'bad')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('that token is not right')
    expect(screen.queryByText('home')).toBeNull()
  })

  it('signing out returns to the sign-in page', async () => {
    const calls = stub({ feedback_url: null, auth_required: true, authenticated: true })
    renderApp()
    await userEvent.click(await screen.findByRole('button', { name: 'Sign out' }))
    await waitFor(() => expect(screen.getByLabelText('Access token')).toBeInTheDocument())
    expect(calls).toContain('POST /auth/logout')
  })
})
