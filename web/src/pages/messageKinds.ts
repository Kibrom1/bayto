// The transcript is a conversation, not the wire log. The orchestrator also sends protocol traffic
// through the same channel -- the moderator's turn assignment (the whole prompt, including a copy of
// the recent transcript), hand-raises, resume and report markers, and `x-*` extension records such as
// the stop request (raw JSON). Showing those made every turn repeat the previous ones and dumped JSON
// into the room, so they are kept out of the transcript.
const INTERNAL_KINDS = new Set(['assignment', 'hand-raise', 'resume', 'report'])

export function isConversational(message: { kind?: string }): boolean {
  const kind = message.kind ?? ''
  return !INTERNAL_KINDS.has(kind) && !kind.startsWith('x-')
}
