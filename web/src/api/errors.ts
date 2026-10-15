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

export async function parseError(res: Response): Promise<string> {
  let body: any
  try {
    body = await res.json()
  } catch {
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
