// "Local model" badge for a message a local model wrote because Claude had run out of usage. Comes from the message's
// meta (`local_fallback`, set by the orchestrator, also sent live as a `message_meta` event). Nothing renders otherwise.
export function localFallbackOf(meta: unknown): { model: string; reason?: string } | null {
  const lf = (meta as { local_fallback?: unknown } | null | undefined)?.local_fallback
  if (!lf || typeof lf !== 'object' || typeof (lf as { model?: unknown }).model !== 'string') return null
  return lf as { model: string; reason?: string }
}

export function LocalBadge({ meta }: { meta: unknown }) {
  const lf = localFallbackOf(meta)
  if (!lf) return null
  return (
    <span className="chip warn local-model" title={`Written by ${lf.model} because Claude had run out of usage. Quality may differ from Claude.`}>
      Local model
    </span>
  )
}
