import { useState } from 'react'
import type { ArtifactOut } from '../api/sessions'

const label = (key: string) => key.replace(/[_-]+/g, ' ').replace(/^\w/, (c) => c.toUpperCase())

// The synthesizer chooses the shape of content_json, so render whatever arrives.
function Value({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined || value === '') return null
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return <p className="syn-text">{String(value)}</p>
  }
  if (Array.isArray(value)) {
    return (
      <ul className="syn-list">
        {value.map((v, i) => (
          <li key={i}>{typeof v === 'object' && v !== null ? <Value value={v} depth={depth + 1} /> : String(v)}</li>
        ))}
      </ul>
    )
  }
  return (
    <div className="syn-group">
      {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
        <section key={k}>
          {depth === 0 ? <h3 className="syn-h">{label(k)}</h3> : <h4 className="syn-h2">{label(k)}</h4>}
          <Value value={v} depth={depth + 1} />
        </section>
      ))}
    </div>
  )
}

export function toMarkdown(value: unknown, depth = 0): string {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value !== 'object') return `${String(value)}\n\n`
  if (Array.isArray(value)) {
    return value.map((v) => (typeof v === 'object' && v !== null ? toMarkdown(v, depth + 1) : `- ${String(v)}\n`)).join('') + '\n'
  }
  return Object.entries(value as Record<string, unknown>)
    .map(([k, v]) => `${'#'.repeat(Math.min(depth + 2, 5))} ${label(k)}\n\n${toMarkdown(v, depth + 1)}`)
    .join('')
}

export function Synthesis({ title, artifacts }: { title: string; artifacts: ArtifactOut[] }) {
  const [copied, setCopied] = useState(false)
  const synthesis = artifacts.find((a) => a.type === 'synthesis')
  const minority = artifacts.find((a) => a.type === 'minority_report')
  if (!synthesis) return null

  const markdown = `# ${title}\n\n${toMarkdown(synthesis.content_json)}${
    minority ? `## Minority report\n\n${toMarkdown(minority.content_json, 1)}` : ''
  }`

  async function copy() {
    try {
      await navigator.clipboard.writeText(markdown)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard unavailable: the download button still works */
    }
  }

  function download() {
    const url = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `${title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'synthesis'}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <article className="synthesis" aria-label="Final synthesis">
      <header className="syn-head">
        <div>
          <p className="room-kicker">Final synthesis</p>
          <h2 className="syn-title">{title}</h2>
        </div>
        <div className="room-actions">
          <button type="button" className="btn" onClick={copy}>{copied ? 'Copied' : 'Copy Markdown'}</button>
          <button type="button" className="btn" onClick={download}>Download .md</button>
        </div>
      </header>
      <Value value={synthesis.content_json} />
      {synthesis.source_turn_ids.length > 0 && (
        <p className="syn-source">Based on {synthesis.source_turn_ids.length} turns in the transcript below.</p>
      )}
      {minority && (
        <section className="syn-minority" aria-label="Minority report">
          <h3 className="syn-h">Minority report</h3>
          <Value value={minority.content_json} depth={1} />
        </section>
      )}
    </article>
  )
}
