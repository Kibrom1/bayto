import { useEffect, useState } from 'react'

export function useSessionEvents(sessionId: string) {
  const [messages, setMessages] = useState<any[]>([])
  const [rollingSummary, setRollingSummary] = useState<string | null>(null)

  useEffect(() => {
    const eventSource = new EventSource(`/sessions/${sessionId}/events`)

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        setMessages((prev) => [...prev, data])
      } catch (err) {
        console.error('Error parsing SSE message:', err)
      }
    }

    // Handle special events like rolling_summary
    // Note: sse-starlette sends custom events with 'event' field
    // The browser's EventSource only triggers onmessage for unnamed events.
    // For named events, we need to add listeners.
    eventSource.addEventListener('rolling_summary', (event: any) => {
      try {
        const data = JSON.parse(event.data)
        setRollingSummary(data.summary)
      } catch (err) {
        console.error('Error parsing rolling summary:', err)
      }
    })

    return () => eventSource.close()
  }, [sessionId])

  return { messages, rollingSummary }
}
