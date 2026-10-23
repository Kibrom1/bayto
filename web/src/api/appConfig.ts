import { ApiError, parseError } from './errors'

// Mirrors orchestrator/src/orchestrator/api/app_config.py (GET /app-config).
export interface AppConfig {
  feedback_url: string | null
  /** BAYTO_API_TOKEN is set on the server. */
  auth_required: boolean
  /** This browser holds a valid session (always true when auth is off). */
  authenticated: boolean
}

const NO_CONFIG: AppConfig = { feedback_url: null, auth_required: false, authenticated: true }

// Same-origin requests (the page, the Vite proxy, EventSource) carry the HttpOnly `bayto_session` cookie by default.
export async function login(token: string): Promise<void> {
  const res = await fetch('/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) })
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
}

export async function logout(): Promise<void> {
  await fetch('/auth/logout', { method: 'POST' })
}

/** The server's settings for the UI. Never throws: on any failure there is simply no feedback link. */
export async function getAppConfig(): Promise<AppConfig> {
  try {
    const res = await fetch('/app-config')
    if (!res.ok) return NO_CONFIG
    const data = await res.json()
    return {
      feedback_url: typeof data?.feedback_url === 'string' ? data.feedback_url : null,
      auth_required: data?.auth_required === true,
      authenticated: data?.authenticated !== false,
    }
  } catch {
    return NO_CONFIG
  }
}

/**
 * The href for the "Send feedback" link, or null when the URL is not a plain https or mailto URL (defence in
 * depth: the server already checks). https gets `session=<id>` when a room is open, and only that id, never task
 * text or attachment content. mailto gets a prefilled subject and nothing else.
 */
export function feedbackHref(base: string | null | undefined, sessionId?: string | null): string | null {
  if (!base) return null
  let url: URL
  try {
    url = new URL(base)
  } catch {
    return null
  }
  if (url.protocol === 'https:') {
    if (sessionId && /^[A-Za-z0-9-]{1,64}$/.test(sessionId)) {
      url.searchParams.set('session', sessionId)
      return url.toString()
    }
    return base
  }
  if (url.protocol === 'mailto:') {
    if (/[?&]subject=/i.test(base)) return base
    return base + (base.includes('?') ? '&' : '?') + 'subject=' + encodeURIComponent('Bayto pilot feedback')
  }
  return null
}
