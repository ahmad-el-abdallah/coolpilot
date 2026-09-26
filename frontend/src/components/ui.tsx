import { useEffect, useState, type ReactNode } from 'react'

export function Card({ title, subtitle, children, actions, id, className = '' }: {
  title?: ReactNode; subtitle?: ReactNode; children: ReactNode; actions?: ReactNode; id?: string; className?: string
}) {
  return (
    <section className={`card ${className}`} id={id}>
      {(title || actions) && (
        <header className="card-head">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p className="muted">{subtitle}</p>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function Toggle({ checked, onChange, disabled, label }: {
  checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string
}) {
  return (
    <button
      type="button" role="switch" aria-checked={checked} aria-label={label}
      className={`toggle ${checked ? 'on' : ''}`} disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span className="knob" />
    </button>
  )
}

export function Segmented<T extends string>({ value, options, onChange, disabled, labels }: {
  value: T | null; options: T[]; onChange: (v: T) => void; disabled?: boolean; labels?: Partial<Record<T, string>>
}) {
  return (
    <div className="segmented" role="radiogroup">
      {options.map((o) => (
        <button
          key={o} type="button" role="radio" aria-checked={value === o} disabled={disabled}
          className={value === o ? 'active' : ''} onClick={() => value !== o && onChange(o)}
        >
          {labels?.[o] ?? o.replace(/_/g, ' ')}
        </button>
      ))}
    </div>
  )
}

/** Slider that only commits when released (so we don't hammer sysfs while dragging). */
export function Slider({ value, min, max, step = 1, onCommit, format, disabled, marks }: {
  value: number; min: number; max: number; step?: number
  /** return false (or a promise of false) when the change failed, to snap back */
  onCommit: (v: number) => unknown
  format?: (v: number) => string; disabled?: boolean; marks?: number[]
}) {
  const [local, setLocal] = useState(value)
  useEffect(() => setLocal(value), [value])
  const send = (v: number) => {
    setLocal(v)
    Promise.resolve(onCommit(v)).then((ok) => { if (ok === false) setLocal(value) })
  }
  const commit = () => { if (local !== value) send(local) }
  const pct = ((local - min) / (max - min || 1)) * 100
  return (
    <div className="slider">
      <input
        type="range" min={min} max={max} step={step} value={local} disabled={disabled}
        style={{ '--pct': `${pct}%` } as React.CSSProperties}
        onChange={(e) => setLocal(Number(e.target.value))}
        onPointerUp={commit} onKeyUp={commit} onBlur={commit}
      />
      <output>{format ? format(local) : local}</output>
      {marks && (
        <div className="marks">
          {marks.map((m) => (
            <button key={m} type="button" disabled={disabled} onClick={() => send(m)}>
              {format ? format(m) : m}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function Stat({ label, value, sub, tone }: {
  label: string; value: ReactNode; sub?: ReactNode; tone?: 'good' | 'warn' | 'bad'
}) {
  return (
    <div className={`stat ${tone ?? ''}`}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  )
}

export function Sparkline({ values, max, color = 'var(--accent)', height = 40 }: {
  values: (number | null | undefined)[]; max?: number; color?: string; height?: number
}) {
  const pts = values.map((v) => v ?? 0)
  if (pts.length < 2) return <svg className="spark" height={height} />
  const hi = max ?? Math.max(...pts, 1)
  const w = 100
  const d = pts.map((v, i) => `${(i / (pts.length - 1)) * w},${height - (Math.min(v, hi) / hi) * (height - 2) - 1}`).join(' ')
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" height={height}>
      <polyline points={`0,${height} ${d} ${w},${height}`} fill={color} opacity="0.12" stroke="none" />
      <polyline points={d} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

export function Badge({ children, tone }: { children: ReactNode; tone?: 'good' | 'warn' | 'bad' | 'info' }) {
  return <span className={`badge ${tone ?? ''}`}>{children}</span>
}

export function Button({ children, onClick, kind = 'default', disabled, type = 'button', title }: {
  children: ReactNode; onClick?: () => void; kind?: 'default' | 'primary' | 'danger' | 'ghost'
  disabled?: boolean; type?: 'button' | 'submit'; title?: string
}) {
  return (
    <button type={type} className={`btn ${kind}`} onClick={onClick} disabled={disabled} title={title}>
      {children}
    </button>
  )
}
