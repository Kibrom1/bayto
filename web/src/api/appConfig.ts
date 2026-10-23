// Mirrors orchestrator/src/orchestrator/api/app_config.py (GET /app-config).
export interface AppConfig {
  feedback_url: string | null
}

/** The server's settings for the UI. Never throws: on any failure there is simply no feedback link. */
export async function getAppConfig(): Promise<AppConfig> {
  try {
    const res = await fetch('/app-config')
    if (!res.ok) return { feedback_url: null }
    const data = await res.json()
    return { feedback_url: typeof data?.feedback_url === 'string' ? data.feedback_url : null }
  } catch {
    return { feedback_url: null }
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
