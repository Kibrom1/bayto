import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ContextCard } from '../components/ContextCard'

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}
const NOTES = { id: 'a1', kind: 'file', name: 'notes.md', source: null, chars: 1200, truncated: false, created_at: '2026-10-21T00:00:00Z' }

let items: any[]
let calls: { method: string; url: string; body?: any }[]

function mockApi(refuse?: { url: string; detail: string }) {
  items = []
  calls = []
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    calls.push({ method, url, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body })
    if (url.endsWith('/tasks/t1/attachments') && method === 'GET') return json(items)
    if (refuse && url.includes(refuse.url)) return json({ detail: { error: { code: 'attachment_refused', detail: refuse.detail } } }, 422)
    if (url.includes('/attachments/file')) { const a = { ...NOTES, id: 'f' + items.length, name: 'notes.md' }; items = [...items, a]; return json(a, 201) }
    if (url.includes('/attachments/folder')) { const a = { ...NOTES, id: 'd1', kind: 'folder', name: 'docs', source: '/p/docs', chars: 30000 }; items = [...items, a]; return json(a, 201) }
    if (url.includes('/attachments/url')) { const a = { ...NOTES, id: 'u1', kind: 'url', name: 'Vendor terms', source: 'https://e.com', chars: 800 }; items = [...items, a]; return json(a, 201) }
    if (method === 'PATCH') { const id = url.split('/attachments/')[1].split('?')[0]; const roles = JSON.parse(String(init?.body)).roles; items = items.map((a) => (a.id === id ? { ...a, roles } : a)); return json(items.find((a) => a.id === id)) }
    if (method === 'DELETE') { items = items.filter((a) => !url.includes('/' + a.id)); return new Response(null, { status: 204 }) }
    return json({ detail: 'nope' }, 404)
  })
}

beforeEach(() => { vi.stubGlobal('fetch', vi.fn()) })
afterEach(() => { vi.unstubAllGlobals() })

describe('ContextCard passages', () => {
  it('shows how many passages a long attachment is split into, but not for a small one', async () => {
    mockApi()
    items = [{ ...NOTES, passages: 1 }, { ...NOTES, id: 'a2', name: 'contract.txt', chars: 60000, passages: 41 }]
    render(<ContextCard taskId="t1" sessionId="s1" />)
    expect(await screen.findByText(/60k characters · 41 passages/)).toBeInTheDocument()
    expect(screen.getByText('1.2k characters')).toBeInTheDocument()
  })
})

describe('ContextCard', () => {
  it('says what the agents can and cannot reach, and starts empty', async () => {
    mockApi()
    render(<ContextCard taskId="t1" sessionId="s1" />)
    expect(await screen.findByRole('heading', { name: /context/i })).toBeInTheDocument()
    expect(screen.getByText(/can't reach the folder or the web/i)).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  it('uploads a file, lists it and shows the per-turn cost', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<ContextCard taskId="t1" sessionId="s1" />)
    await screen.findByRole('heading', { name: /context/i })
    await user.upload(screen.getByLabelText('Choose files to attach'), new File(['# hi'], 'notes.md', { type: 'text/markdown' }))
    expect(await screen.findByText('notes.md')).toBeInTheDocument()
    expect(screen.getByText(/1\.2k characters/)).toBeInTheDocument()
    expect(screen.getByText(/about 300 tokens each time/)).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'PUT' && c.url.includes('/attachments/file?name=notes.md&session_id=s1'))).toBe(true)
  })

  it('adds a folder by path and warns when more is attached than fits', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<ContextCard taskId="t1" sessionId="s1" />)
    await user.click(await screen.findByRole('button', { name: /add folder/i }))
    await user.type(screen.getByLabelText(/folder path/i), '/p/docs')
    await user.click(screen.getByRole('button', { name: /^add$/i }))
    expect(await screen.findByText('docs')).toBeInTheDocument()
    expect(calls.find((c) => c.url.includes('/attachments/folder'))?.body).toEqual({ path: '/p/docs' })
    expect(screen.getByText(/more attached than fits|about 7,500 tokens/)).toBeInTheDocument()
  })

  it('adds a link, then removes it', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<ContextCard taskId="t1" sessionId="s1" />)
    await user.click(await screen.findByRole('button', { name: /add link/i }))
    await user.type(screen.getByLabelText(/web address/i), 'https://e.com')
    await user.click(screen.getByRole('button', { name: /^add$/i }))
    const row = (await screen.findByText('Vendor terms')).closest('li')!
    await user.click(within(row).getByRole('button', { name: /remove vendor terms/i }))
    await waitFor(() => expect(screen.queryByText('Vendor terms')).not.toBeInTheDocument())
  })

  it('shows the reason an attachment was refused and keeps the form open', async () => {
    const user = userEvent.setup()
    mockApi({ url: '/attachments/url', detail: "that address is not on the public internet, so it can't be attached" })
    render(<ContextCard taskId="t1" sessionId="s1" />)
    await user.click(await screen.findByRole('button', { name: /add link/i }))
    await user.type(screen.getByLabelText(/web address/i), 'http://127.0.0.1:8000')
    await user.click(screen.getByRole('button', { name: /^add$/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/not on the public internet/)
    expect(screen.getByLabelText(/web address/i)).toBeInTheDocument()
  })
})

describe('ContextCard in a room', () => {
  it('sends the session with each change so the team is told', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      if (!init?.method) return new Response('[]', { status: 200 })
      if (init.method === 'DELETE') return new Response(null, { status: 204 })
      return new Response(JSON.stringify({ id: 'a1', kind: 'url', name: 'Terms', source: 'https://x.test/t', chars: 10, truncated: false, created_at: '' }), { status: 201 })
    }))
    render(<ContextCard taskId="t1" sessionId="s1" />)
    expect(screen.getByText(/the team is told/i)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Add link' }))
    await userEvent.type(screen.getByLabelText(/web address/i), 'https://x.test/t')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    await userEvent.click(await screen.findByRole('button', { name: /remove terms/i }))
    expect(calls).toContain('POST /tasks/t1/attachments/url?session_id=s1')
    await vi.waitFor(() => expect(calls).toContain('DELETE /tasks/t1/attachments/a1?session_id=s1'))
  })
})

describe('ContextCard visibility', () => {
  async function withFile(user: ReturnType<typeof userEvent.setup>) {
    mockApi()
    render(<ContextCard taskId="t1" sessionId="s1" roles={['lawyer', 'developer']} />)
    await user.upload(await screen.findByLabelText('Choose files to attach'), new File(['# hi'], 'notes.md', { type: 'text/markdown' }))
    await screen.findByText('notes.md')
  }

  it('shows an open attachment as Everyone and limits it to chosen roles', async () => {
    const user = userEvent.setup()
    await withFile(user)
    const toggle = screen.getByRole('button', { name: /who can read notes\.md/i })
    expect(toggle).toHaveTextContent('Everyone')
    await user.click(toggle)
    await user.click(screen.getByRole('radio', { name: /only chosen roles/i }))
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false) // nothing is saved until a role is ticked
    await user.click(screen.getByRole('checkbox', { name: 'lawyer' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /who can read notes\.md/i })).toHaveTextContent('Only lawyer'))
    const patch = calls.find((c) => c.method === 'PATCH')!
    expect(patch.url).toBe('/tasks/t1/attachments/f0?session_id=s1')
    expect(patch.body).toEqual({ roles: ['lawyer'] })
    await user.click(screen.getByRole('checkbox', { name: 'developer' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /who can read notes\.md/i })).toHaveTextContent('Only lawyer, developer'))
  })

  it('goes back to Everyone, and will not save an empty choice', async () => {
    const user = userEvent.setup()
    await withFile(user)
    await user.click(screen.getByRole('button', { name: /who can read notes\.md/i }))
    await user.click(screen.getByRole('radio', { name: /only chosen roles/i }))
    await user.click(screen.getByRole('checkbox', { name: 'lawyer' }))
    await screen.findByText('Only lawyer')
    await user.click(screen.getByRole('checkbox', { name: 'lawyer' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/at least one role/i)
    expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(1)
    await user.click(screen.getByRole('radio', { name: 'Everyone' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /who can read notes\.md/i })).toHaveTextContent('Everyone'))
    expect(calls.filter((c) => c.method === 'PATCH').pop()!.body).toEqual({ roles: null })
  })

  it('shows a restricted attachment returned by the server', async () => {
    mockApi()
    items = [{ ...NOTES, id: 'c1', name: 'contract.md', roles: ['lawyer'] }]
    render(<ContextCard taskId="t1" sessionId="s1" roles={['lawyer', 'developer']} />)
    expect(await screen.findByRole('button', { name: /who can read contract\.md/i })).toHaveTextContent('Only lawyer')
  })
})

describe('ContextCard who can read, when adding', () => {
  const ROLES = ['lawyer', 'developer']
  async function restrictTo(user: ReturnType<typeof userEvent.setup>, ...ticked: string[]) {
    await user.click(screen.getByRole('button', { name: /new items/i }))
    await user.click(screen.getByRole('radio', { name: /only chosen roles/i }))
    for (const r of ticked) await user.click(screen.getByRole('checkbox', { name: r }))
  }
  const file = () => new File(['# hi'], 'notes.md', { type: 'text/markdown' })

  it('sends roles with a restricted file, folder and link, then goes back to Everyone', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<ContextCard taskId="t1" sessionId="s1" roles={ROLES} />)
    await screen.findByRole('heading', { name: /context/i })

    await restrictTo(user, 'lawyer', 'developer')
    await user.upload(screen.getByLabelText('Choose files to attach'), file())
    await screen.findByText('notes.md')
    expect(calls.find((c) => c.method === 'PUT')!.url).toBe('/tasks/t1/attachments/file?name=notes.md&session_id=s1&roles=lawyer&roles=developer')
    expect(screen.getByRole('button', { name: /new items/i })).toHaveTextContent('everyone can read') // reset after the add
    expect(screen.queryByRole('radio', { name: /only chosen roles/i })).not.toBeInTheDocument()

    await restrictTo(user, 'lawyer')
    await user.click(screen.getByRole('button', { name: /add folder/i }))
    await user.type(screen.getByLabelText(/folder path/i), '/p/docs')
    await user.click(screen.getByRole('button', { name: /^add$/i }))
    await screen.findByText('docs')
    expect(calls.find((c) => c.url.includes('/attachments/folder'))!.body).toEqual({ path: '/p/docs', roles: ['lawyer'] })

    await restrictTo(user, 'developer')
    await user.click(screen.getByRole('button', { name: /add link/i }))
    await user.type(screen.getByLabelText(/web address/i), 'https://e.com')
    await user.click(screen.getByRole('button', { name: /^add$/i }))
    await screen.findByText('Vendor terms')
    expect(calls.find((c) => c.url.includes('/attachments/url'))!.body).toEqual({ url: 'https://e.com', roles: ['developer'] })
  })

  it('sends no roles by default', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<ContextCard taskId="t1" sessionId="s1" roles={ROLES} />)
    await user.upload(await screen.findByLabelText('Choose files to attach'), file())
    await screen.findByText('notes.md')
    await user.click(screen.getByRole('button', { name: /add link/i }))
    await user.type(screen.getByLabelText(/web address/i), 'https://e.com')
    await user.click(screen.getByRole('button', { name: /^add$/i }))
    await screen.findByText('Vendor terms')
    expect(calls.find((c) => c.method === 'PUT')!.url).toBe('/tasks/t1/attachments/file?name=notes.md&session_id=s1')
    expect(calls.find((c) => c.url.includes('/attachments/url'))!.body).toEqual({ url: 'https://e.com' })
  })

  it('blocks an add when "Only chosen roles" has nothing ticked', async () => {
    const user = userEvent.setup()
    mockApi()
    render(<ContextCard taskId="t1" sessionId="s1" roles={ROLES} />)
    await screen.findByRole('heading', { name: /context/i })
    await restrictTo(user)
    await user.upload(screen.getByLabelText('Choose files to attach'), file())
    expect(await screen.findByRole('alert')).toHaveTextContent(/tick at least one role/i)
    await user.click(screen.getByRole('button', { name: /add link/i }))
    await user.type(screen.getByLabelText(/web address/i), 'https://e.com')
    await user.click(screen.getByRole('button', { name: /^add$/i }))
    expect(screen.getByRole('alert')).toHaveTextContent(/tick at least one role/i)
    expect(calls.filter((c) => c.method !== 'GET')).toHaveLength(0)
    await user.click(screen.getByRole('checkbox', { name: 'lawyer' }))
    await user.click(screen.getByRole('button', { name: /^add$/i }))
    await screen.findByText('Vendor terms')
    expect(calls.find((c) => c.url.includes('/attachments/url'))!.body).toEqual({ url: 'https://e.com', roles: ['lawyer'] })
  })
})
