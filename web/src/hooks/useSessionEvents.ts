import { useEffect, useState } from 'react'

export interface LiveTurn {
  text: string
  tool: string | null
}

export function useSessionEvents(sessionId: string) {
  const [messages, setMessages] = useState<any[]>([])
  const [rollingSummary, setRollingSummary] = useState<string | null>(null)
  const [live, setLive] = useState<Record<string, LiveTurn>>({})
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    const eventSource = new EventSource(`/sessions/${sessionId}/events`)

    eventSource.onopen = () => setConnected(true)
    eventSource.onerror = () => setConnected(false)

    // Unnamed events are finished messages.
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        setMessages((prev) => [...prev, data])
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
    named('turn_error', (data) =>
      setLive((prev) => {
        const { [data.from]: _failed, ...rest } = prev
        return rest
      }),
    )

    return () => eventSource.close()
  }, [sessionId])

  return { messages, rollingSummary, live, connected }
}
