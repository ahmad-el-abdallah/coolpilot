import type { ReactNode } from 'react'
import { Button } from './ui'

// laptop top view: [area key, grid column, grid row]
const LAYOUT: [string, number, number][] = [
  ['7', 1, 1], ['8', 3, 1],
  ['1', 1, 2], ['2', 2, 2], ['3', 3, 2],
  ['4', 1, 3], ['5', 2, 3], ['6', 3, 3],
]

/** Clickable laptop diagram: tell the backend which area you're pressing right now. */
export function AreaPad({ areas, onMark, active, extra }: {
  areas: Record<string, string>; onMark: (key: string) => void; active?: string | null; extra?: ReactNode
}) {
  return (
    <div className="laptop">
      <div className="screen">screen</div>
      <div className="deck">
        {LAYOUT.map(([k, c, r]) => (
          <button
            key={k} type="button" style={{ gridColumn: c, gridRow: r }} onClick={() => onMark(k)}
            className={active === areas[k] ? 'active' : ''}
          >
            <b>{k}</b> {areas[k]}
          </button>
        ))}
      </div>
      <div className="row">
        <Button kind={active === areas['9'] ? 'primary' : 'default'} onClick={() => onMark('9')}>9 · lifting / tilting</Button>
        <Button kind={active === areas['0'] ? 'primary' : 'default'} onClick={() => onMark('0')}>0 · not touching</Button>
        <span className="spacer" />
        {extra}
      </div>
    </div>
  )
}
