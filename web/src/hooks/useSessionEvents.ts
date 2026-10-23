import { useEffect, useRef, useState } from 'react'

export interface LiveTurn {
  text: string
  tool: string | null
}

// The stream is reopened whenever `sessionStatus` (from the room's status polling) changes. That is what
// makes Pause then Resume work: a paused session's runner ends, so the server ends the stream, and Resume
// starts a new runner with a new event channel that the old connection would never hear from. Each
// connection resumes after the last message already seen. A stream that ends while the status is
// unchanged is NOT reopened, so a stale status can never cause a reconnect loop.
export function useSessionEvents(sessionId: string, sessionStatus?: string) {
  const [messages, setMessages] = useState<any[]>([])
  const [rollingSummary, setRollingSummary] = useState<string | null>(null)
  const [live, setLive] = useState<Record<string, LiveTurn>>({})
  const [connected, setConnected] = useState(false)
  const lastSeq = useRef(0)
  // `message_meta` (the attachment report added to a message after it was sent) can in principle beat the message
  // itself; keep it until the message arrives.
  const pendingMeta = useRef<Record<string, any>>({})

  useEffect(() => {
    // since_seq=0 (first connect) replays the stored transcript, so opening or reloading the room
    // mid-session shows the whole conversation; later connects resume after the last seq seen.
    const eventSource = new EventSource(`/sessions/${sessionId}/events?since_seq=${lastSeq.current}`)

    eventSource.onopen = () => setConnected(true)
    eventSource.onerror = () => setConnected(false)

    // Unnamed events are finished messages.
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (typeof data.seq === 'number') lastSeq.current = Math.max(lastSeq.current, data.seq)
        // A reconnect can replay a message we already have; message_id makes that harmless.
        const waiting = pendingMeta.current[data.message_id]
        if (waiting) delete pendingMeta.current[data.message_id]
        const full = waiting ? { ...data, meta: { ...(data.meta ?? {}), ...waiting } } : data
        setMessages((prev) => (prev.some((m) => m.message_id === data.message_id) ? prev : [...prev, full]))
        // The finished message replaces that speaker's in-progress bubble.
        setLive((prev) => {
          if (!(data.from in prev)) return prev
          const { [data.from]: _done, ...rest } = prev
          return rest
        })
      } catch (err) {
        console.error('Error parsing SSE message:', err)
      }
    }

    // Named events need their own listeners (EventSource only routes unnamed ones to onmessage).
    const named = (name: string, handler: (data: any) => void) =>
      eventSource.addEventListener(name, (event: any) => {
        try {
          handler(JSON.parse(event.data))
        } catch (err) {
          console.error(`Error parsing ${name}:`, err)
        }
      })

    // Nothing more will arrive (finished, failed or never started): stop, or the browser would
    // reconnect forever.
    eventSource.addEventListener('end', () => {
      eventSource.close()
      setConnected(false)
    })

    named('rolling_summary', (data) => setRollingSummary(data.summary))
    named('turn_delta', (data) =>
      setLive((prev) => ({
        ...prev,
        [data.from]: { text: (prev[data.from]?.text ?? '') + data.text, tool: prev[data.from]?.tool ?? null },
      })),
    )
    named('turn_tool', (data) =>
      setLive((prev) => ({ ...prev, [data.from]: { text: prev[data.from]?.text ?? '', tool: data.name } })),
    )
    named('message_meta', (data) => {
      setMessages((prev) => {
        if (!prev.some((m) => m.message_id === data.message_id)) {
          pendingMeta.current[data.message_id] = data.meta
          return prev
        }
        return prev.map((m) => (m.message_id === data.message_id ? { ...m, meta: { ...(m.meta ?? {}), ...data.meta } } : m))
      })
    })
    named('turn_error', (data) =>
      setLive((prev) => {
        const { [data.from]: _failed, ...rest } = prev
        return rest
      }),
    )

    return () => eventSource.close()
  }, [sessionId, sessionStatus])

  return { messages, rollingSummary, live, connected }
}
