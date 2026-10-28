// Turns the orchestrator's error bodies into a sentence a person can read, instead of raw JSON.

/** An HTTP failure from the orchestrator; `status` lets the UI choose a friendlier screen. */
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** What a 502/503/504 without a body means: nothing is listening on the API port. */
export const API_DOWN = "Bayto's API isn't running. Start it from Services & logs."

/** True when the message is the API-down sentence, so the UI can offer a link to the Services page. */
export const isApiDown = (message: string) => message.includes(API_DOWN)

export async function parseError(res: Response): Promise<string> {
  let body: any
  try {
    body = await res.json()
  } catch {
    // A proxy error (the dev server cannot reach the API) has no JSON body: say what it means, not "Bad Gateway".
    if (res.status === 502 || res.status === 503 || res.status === 504) return API_DOWN
    return res.statusText
  }
  const detail = body?.detail
  if (typeof detail === 'string') return detail
  // The orchestrator's own errors: {"detail": {"error": {"code", "detail", "field"}}}
  if (typeof detail?.error?.detail === 'string') return detail.error.detail
  // FastAPI validation errors: {"detail": [{"loc": [...], "msg": "..."}]}
  if (Array.isArray(detail)) {
    return detail
      .map((e: any) => {
        const field = (e?.loc ?? []).filter((p: unknown) => p !== 'body' && p !== 'path' && p !== 'query').join('.')
        return field ? `${field}: ${e?.msg}` : String(e?.msg)
      })
      .join('; ')
  }
  return JSON.stringify(body)
}

/** True for "that thing doesn't exist": a 404, or a 422 from a malformed id in the URL. */
export function isNotFound(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 404 || err.status === 422)
}
