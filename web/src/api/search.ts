// Mirrors orchestrator/src/orchestrator/api/search.py (GET /search).
import { ApiError, parseError } from './errors'

export interface SearchHit {
  task_id: string
  task_title: string
  session_id: string
  message_id: string | null
  speaker: string
  kind: string
  // Plain text; the matched words are wrapped in [[ and ]].
  snippet: string
  created_at: string | null
  rank: number
}

export async function searchTranscripts(q: string, opts: { taskId?: string; includeArchived?: boolean } = {}): Promise<SearchHit[]> {
  const p = new URLSearchParams({ q })
  if (opts.taskId) p.set('task_id', opts.taskId)
  if (opts.includeArchived) p.set('include_archived', 'true')
  const res = await fetch(`/search?${p}`)
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  return (await res.json()).results as SearchHit[]
}

/** Splits a snippet at its [[ ]] markers so the caller can render matches as text (never as HTML). */
export function snippetParts(s: string): { text: string; match: boolean }[] {
  const out: { text: string; match: boolean }[] = []
  const re = /\[\[([\s\S]*?)\]\]/g
  let last = 0
  for (let m = re.exec(s); m; m = re.exec(s)) {
    if (m.index > last) out.push({ text: s.slice(last, m.index), match: false })
    out.push({ text: m[1], match: true })
    last = m.index + m[0].length
  }
  if (last < s.length) out.push({ text: s.slice(last), match: false })
  return out
}
