import { describe, expect, it } from 'vitest'
import { API_DOWN, ApiError, isApiDown, isNotFound, parseError } from './errors'

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
    expect(await parseError(new Response('<html>', { status: 400, statusText: 'Bad Request' }))).toBe('Bad Request')
  })
  it('says the API is not running for a bodyless proxy error', async () => {
    for (const status of [502, 503, 504]) {
      expect(await parseError(new Response('', { status, statusText: 'Bad Gateway' }))).toBe(API_DOWN)
    }
    expect(isApiDown(`GET /tasks failed: 502 ${API_DOWN}`)).toBe(true)
    expect(isApiDown('session not found')).toBe(false)
  })
  it('keeps the orchestrator own 503 message', async () => {
    expect(await parseError(res({ detail: 'The moderator cannot reach Claude' }, 503))).toBe('The moderator cannot reach Claude')
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
