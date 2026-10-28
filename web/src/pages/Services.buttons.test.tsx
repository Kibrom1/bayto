import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Services } from './Services'

afterEach(() => vi.unstubAllGlobals())

const state = (services: Record<string, string>) => ({
  services,
  config: { streaming: true, llm_backend: 'subscription', summary_model: 'a', synthesis_model: 'b', hand_raise_model: 'c', api_key: '' },
  sessions: [],
})

function stub(services: Record<string, string>) {
  const actions: unknown[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes('/control/api/state')) return new Response(JSON.stringify(state(services)))
    if (String(url).includes('/control/api/log')) return new Response('')
    if (String(url).includes('/control/api/action')) { actions.push(JSON.parse(String(init?.body))); return new Response('ok') }
    return new Response('{}')
  }))
  return actions
}

const row = (title: string) => screen.getByText(title, { selector: '.svc-row-title' }).closest('.svc-row') as HTMLElement

describe('Services buttons', () => {
  it('offers Start for a stopped service and Restart + Stop for a running one', async () => {
    stub({ postgres: 'running', observability: 'stopped', orchestrator: 'stopped', web: 'running' })
    render(<MemoryRouter><Services /></MemoryRouter>)
    await waitFor(() => expect(document.querySelectorAll('.svc-row-title').length).toBeGreaterThan(3))
    expect(within(row('Postgres')).getByRole('button', { name: 'Restart' })).toBeEnabled()
    expect(within(row('Postgres')).getByRole('button', { name: 'Stop' })).toBeEnabled()
    expect(within(row('Postgres')).queryByRole('button', { name: 'Start' })).toBeNull()
    expect(within(row('Orchestrator')).getByRole('button', { name: 'Start' })).toBeEnabled()
    expect(within(row('Orchestrator')).queryByRole('button', { name: 'Restart' })).toBeNull()
  })

  it('never offers to stop or restart the web server that serves the page', async () => {
    stub({ postgres: 'running', observability: 'running', orchestrator: 'running', web: 'running' })
    render(<MemoryRouter><Services /></MemoryRouter>)
    await waitFor(() => expect(document.querySelectorAll('.svc-row-title').length).toBeGreaterThan(3))
    expect(within(row('Web app')).queryByRole('button')).toBeNull()
    expect(within(row('Web app')).getByText(/Ctrl\+C/)).toBeInTheDocument()
  })

  it('sends a restart action', async () => {
    const actions = stub({ postgres: 'running', observability: 'running', orchestrator: 'running', web: 'running' })
    render(<MemoryRouter><Services /></MemoryRouter>)
    await waitFor(() => expect(document.querySelectorAll('.svc-row-title').length).toBeGreaterThan(3))
    await userEvent.click(within(row('Orchestrator')).getByRole('button', { name: 'Restart' }))
    await waitFor(() => expect(actions).toContainEqual(expect.objectContaining({ action: 'service', name: 'orchestrator', op: 'restart' })))
  })
})
