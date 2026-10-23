// What a seat was shown of the attached material for one turn. Comes from the message's meta (set by the
// orchestrator); names and passage numbers only. Nothing renders when nothing was attached.
export interface ReadEntry {
  name: string
  mode: 'whole' | 'excerpt' | 'start' | 'not_shown'
  parts: number[]
  total: number
}

export function readEntries(meta: unknown): ReadEntry[] {
  const list = (meta as { attachments?: unknown } | null | undefined)?.attachments
  if (!Array.isArray(list)) return []
  return list.filter((e): e is ReadEntry => !!e && typeof e.name === 'string' && typeof e.mode === 'string')
}

export function describeRead(e: ReadEntry): string {
  const total = e.total === 1 ? '1 passage' : `${e.total} passages`
  switch (e.mode) {
    case 'whole': return `${e.name} (whole)`
    case 'excerpt': return `${e.name} ${e.parts.length === 1 ? 'part' : 'parts'} ${e.parts.join(', ')} of ${e.total}`
    case 'start': return `${e.name} (start only, of ${total})`
    default: return `${e.name} (not shown, ${total})`
  }
}

export function ReadNote({ meta }: { meta: unknown }) {
  const entries = readEntries(meta)
  if (entries.length === 0) return null
  const line = entries.map(describeRead)
  return (
    <details className="read-note">
      <summary>Read: {line.join(' · ')}</summary>
      <ul>
        {line.map((l, i) => <li key={i}>{l}</li>)}
      </ul>
    </details>
  )
}
