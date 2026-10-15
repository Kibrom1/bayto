import { describe, expect, it } from 'vitest'
import { ApiError, isNotFound, parseError } from './errors'

const res = (body: unknown, status = 400) => new Response(JSON.stringify(body), { status })

describe('parseError', () => {
  it('reads the orchestrator error shape', async () => {
    expect(await parseError(res({ detail: { error: { code: 'not_found', detail: 'session not found' } } }))).toBe('session not found')
  })
  it('flattens FastAPI validation errors', async () => {
    const body = { detail: [{ loc: ['path', 'session_id'], msg: 'Input should be a valid UUID' }, { loc: ['body', 'title'], msg: 'too short' }] }
    expect(await parseError(res(body, 422))).toBe('session_id: Input should be a valid UUID; title: too short')
  })
  it('keeps plain string details and tolerates non-JSON', async () => {
    expect(await parseError(res({ detail: 'nope' }))).toBe('nope')
    expect(await parseError(new Response('<html>', { status: 502, statusText: 'Bad Gateway' }))).toBe('Bad Gateway')
  })
})

describe('isNotFound', () => {
  it('is true for 404 and malformed-id 422 only', () => {
    expect(isNotFound(new ApiError(404, 'x'))).toBe(true)
    expect(isNotFound(new ApiError(422, 'x'))).toBe(true)
    expect(isNotFound(new ApiError(500, 'x'))).toBe(false)
    expect(isNotFound(new Error('x'))).toBe(false)
  })
})
