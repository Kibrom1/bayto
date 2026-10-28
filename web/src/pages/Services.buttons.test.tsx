import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Services } from './Services'

afterEach(() => vi.unstubAllGlobals())

const state = (services: Record<string, string>) => ({
  services,
  config: { streaming: true, llm_backend: 'subscription', summary_model: 'a', synthesis_model: 'b', hand_raise_model: 'c', api_key: '' },
})

function stub(services: Record<string, string>) {
  const actions: unknown[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes('/control/api/state')) return new Response(JSON.stringify(state(services)))
    if (String(url).includes('/control/api/sessions')) return new Response(JSON.stringify([{ task: 'T1', id: 'a'.repeat(8) + '-0000-0000-0000-000000000000', status: 'active' }, { task: 'T2', id: 'b'.repeat(8) + '-0000-0000-0000-000000000000', status: 'created' }]))
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

  it('shows only the valid actions for each session, with no free-text id box', async () => {
    stub({ postgres: 'running', observability: 'running', orchestrator: 'running', web: 'running' })
    render(<MemoryRouter><Services /></MemoryRouter>)
    await screen.findByText('T1')
    const t1 = row('T1'), t2 = row('T2')
    expect(within(t1).getByRole('button', { name: 'Pause' })).toBeEnabled()
    expect(within(t1).getByRole('button', { name: 'Stop' })).toBeEnabled()
    expect(within(t1).queryByRole('button', { name: 'Start' })).toBeNull()
    expect(within(t2).getByRole('button', { name: 'Start' })).toBeEnabled()
    expect(within(t2).queryByRole('button', { name: 'Pause' })).toBeNull()
    expect(screen.queryByLabelText('Session id')).toBeNull()
  })

  it('shows a health banner with Start all when something is down, and sends start_all', async () => {
    const actions = stub({ postgres: 'running', observability: 'stopped', orchestrator: 'stopped', web: 'running' })
    render(<MemoryRouter><Services /></MemoryRouter>)
    const banner = await screen.findByRole('status')
    expect(banner).toHaveTextContent('2 of 4 not running')
    await userEvent.click(within(banner).getByRole('button', { name: 'Start all' }))
    await waitFor(() => expect(actions).toContainEqual({ action: 'start_all', from_web: true }))
  })

  it('says everything is running when it is', async () => {
    stub({ postgres: 'running', observability: 'running', orchestrator: 'running', web: 'running' })
    render(<MemoryRouter><Services /></MemoryRouter>)
    expect(await screen.findByRole('status')).toHaveTextContent('Everything is running.')
    expect(screen.queryByRole('button', { name: 'Start all' })).toBeNull()
  })
})
