import type { CSSProperties, ReactNode } from 'react'

export interface Seat {
  key: string
  initials: string
  hue: number
  active?: boolean
  label?: string
}

/** Seats around a round table with the question at the centre. Decorative: the roster list beside it carries the same information. */
export function TableRing({ seats, size = 220, children }: { seats: Seat[]; size?: number; children?: ReactNode }) {
  const seat = Math.round(size * 0.27)
  const r = size / 2 - seat / 2
  const n = Math.max(seats.length, 1)
  return (
    <div className="table-ring" style={{ width: size, height: size }} aria-hidden="true">
      <div className="table-ring-disc" />
      {children && <div className="table-ring-label">{children}</div>}
      {seats.map((s, i) => {
        const angle = -Math.PI / 2 + (2 * Math.PI * i) / n
        const style = {
          width: seat,
          height: seat,
          left: size / 2 + r * Math.cos(angle) - seat / 2,
          top: size / 2 + r * Math.sin(angle) - seat / 2,
          fontSize: Math.round(seat * 0.3),
          ['--agent-h' as string]: s.hue,
        } as CSSProperties
        return <div key={s.key} className={`table-seat${s.active ? ' active' : ''}`} style={style} title={s.label}>{s.initials}</div>
      })}
    </div>
  )
}
