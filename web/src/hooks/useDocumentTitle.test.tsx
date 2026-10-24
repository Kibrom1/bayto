import { render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppShell } from '../components/AppShell'
import { NotFound } from '../pages/NotFound'
import { formatTitle, useDocumentTitle, useFallbackTitle } from './useDocumentTitle'

afterEach(() => { document.title = 'Bayto'; vi.unstubAllGlobals() })

function Page({ title }: { title: string }) { useDocumentTitle(title); return <p>x</p> }
function Fallback({ title }: { title: string }) { useFallbackTitle(title); return <p>y</p> }

describe('document title', () => {
  it('formats as "<page> · Bayto"', () => {
    expect(formatTitle('Tasks')).toBe('Tasks · Bayto')
    expect(formatTitle('')).toBe('Bayto')
  })

  it('sets the title and restores it on unmount', () => {
    const { unmount } = render(<Page title="Developer tools" />)
    expect(document.title).toBe('Developer tools · Bayto')
    unmount()
    expect(document.title).toBe('Bayto')
  })

  it('a fallback never overrides a page that named itself', () => {
    render(<><Page title="Fix the login bug" /><Fallback title="Room" /></>)
    expect(document.title).toBe('Fix the login bug · Bayto')
  })

  it('a fallback applies when the page did not name itself', () => {
    render(<Fallback title="Room" />)
    expect(document.title).toBe('Room · Bayto')
  })

  it('the not-found page and the shell route titles', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })))
    const { unmount } = render(<MemoryRouter><NotFound title="Session not found" /></MemoryRouter>)
    expect(document.title).toBe('Session not found · Bayto')
    unmount()
    render(
      <MemoryRouter initialEntries={['/room/s1']}>
        <Routes><Route element={<AppShell />}><Route path="/room/:sessionId" element={<p>room</p>} /></Route></Routes>
      </MemoryRouter>,
    )
    expect(document.title).toBe('Room · Bayto')
  })
})
