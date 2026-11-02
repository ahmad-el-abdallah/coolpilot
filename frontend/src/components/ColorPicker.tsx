import { Slider } from './ui'

const SWATCHES: [number, number, number][] = [
  [255, 255, 255], [255, 0, 0], [255, 120, 0], [255, 220, 0], [0, 200, 60],
  [0, 180, 255], [40, 80, 255], [160, 40, 255], [255, 0, 160],
]

const toHex = (v: [number, number, number]) => `#${v.map((c) => c.toString(16).padStart(2, '0')).join('')}`
const fromHex = (h: string): [number, number, number] =>
  [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]

/** Swatch grid + R/G/B sliders. `onChange` fires once per pick/commit, like Slider's onCommit. */
export function ColorPicker({ value, onChange, disabled }: {
  value: [number, number, number]; onChange: (v: [number, number, number]) => void; disabled?: boolean
}) {
  const [r, g, b] = value
  return (
    <div className="color-picker">
      <div className="swatches">
        {SWATCHES.map((s) => (
          <button
            key={s.join(',')} type="button" disabled={disabled}
            className={`swatch ${s.every((c, i) => c === value[i]) ? 'active' : ''}`}
            style={{ background: `rgb(${s.join(',')})` }}
            onClick={() => onChange(s)}
            aria-label={`rgb(${s.join(',')})`}
          />
        ))}
        <label className="swatch custom" style={{ background: toHex(value) }} title="Custom color">
          <input type="color" value={toHex(value)} disabled={disabled} onChange={(e) => onChange(fromHex(e.target.value))} />
        </label>
      </div>
      <div className="color-channels">
        <Slider value={r} min={0} max={255} format={(v) => `R ${v}`} disabled={disabled} onCommit={(v) => onChange([v, g, b])} />
        <Slider value={g} min={0} max={255} format={(v) => `G ${v}`} disabled={disabled} onCommit={(v) => onChange([r, v, b])} />
        <Slider value={b} min={0} max={255} format={(v) => `B ${v}`} disabled={disabled} onCommit={(v) => onChange([r, g, v])} />
      </div>
    </div>
  )
}
