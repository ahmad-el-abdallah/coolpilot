import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'

/* Charts follow the dataviz method: one y-axis per chart, fixed series order
 * (series-1 blue, series-2 orange - validated for both themes), 2px lines,
 * recessive grid, legend for >= 2 series plus direct end labels, crosshair +
 * tooltip on hover, crash markers in the reserved critical color with an icon
 * and label, and a table view for every chart. */

export type Series = { key: string; label: string; color: string; values: (number | null)[] }
export type Marker = { t: number; label: string }
export type Band = { from: number; to: number }

const PAD = { l: 44, r: 64, t: 14, b: 26 }

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null)
  const [w, setW] = useState(600)
  useLayoutEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, Math.floor(e.contentRect.width))))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

function niceTicks(lo: number, hi: number, n = 4): number[] {
  const span = hi - lo || 1
  const raw = span / n
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) ?? raw
  const out: number[] = []
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6))
  return out
}

/** Time-axis ticks on clock-friendly steps (15 min ... 1 week), aligned to local time. */
function timeTicks(from: number, to: number, max: number): number[] {
  const steps = [30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800]
  const step = steps.find((st) => (to - from) / st <= max) ?? steps[steps.length - 1]
  const tz = new Date(from * 1000).getTimezoneOffset() * 60
  const out: number[] = []
  for (let t = Math.ceil((from - tz) / step) * step + tz; t < to; t += step) if (t > from) out.push(t)
  return out
}

export function fmtTime(t: number, span: number): string {
  const d = new Date(t * 1000)
  if (span <= 600) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  if (span <= 2 * 86400) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}
export const fmtDateTime = (t: number) =>
  new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })

function Legend({ series, extra }: { series: Series[]; extra?: ReactNode }) {
  return (
    <div className="chart-legend">
      {series.length > 1 && series.map((s) => (
        <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>
      ))}
      {extra}
    </div>
  )
}

function Tooltip({ x, width, children }: { x: number; width: number; children: ReactNode }) {
  const left = x > width * 0.6 ? undefined : x + 12
  const right = x > width * 0.6 ? width - x + 12 : undefined
  return <div className="chart-tip" style={{ left, right }}>{children}</div>
}

function Table({ times, series, format, span }: {
  times: number[]; series: Series[]; format: (v: number) => string; span: number
}) {
  return (
    <details className="chart-table">
      <summary>Show as table</summary>
      <div className="chart-table-scroll">
        <table className="table">
          <thead><tr><th>Time</th>{series.map((s) => <th key={s.key}>{s.label}</th>)}</tr></thead>
          <tbody>
            {times.map((t, i) => (
              <tr key={t}>
                <td>{span > 2 * 86400 ? fmtDateTime(t) : fmtTime(t, span)}</td>
                {series.map((s) => <td key={s.key}>{s.values[i] == null ? '—' : format(s.values[i] as number)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

/** Time series with a crosshair tooltip. Lines break where the laptop was off. */
export function LineChart({ times, series, from, to, bucket, format, markers = [], bands = [], height = 180,
  zeroBased = false, extraTip, legendExtra }: {
  times: number[]; series: Series[]; from: number; to: number; bucket: number; format: (v: number) => string
  markers?: Marker[]; bands?: Band[]; height?: number; zeroBased?: boolean
  extraTip?: (i: number) => ReactNode; legendExtra?: ReactNode
}) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const all = series.flatMap((s) => s.values).filter((v): v is number => v != null)
  let lo = zeroBased ? 0 : Math.min(...all)
  let hi = Math.max(...all)
  if (!all.length) { lo = 0; hi = 1 }
  const padY = (hi - lo) * 0.12 || 1
  if (!zeroBased) lo = Math.max(0, lo - padY)
  hi += padY
  const ticks = niceTicks(lo, hi)
  const iw = width - PAD.l - PAD.r
  const ih = height - PAD.t - PAD.b
  const x = (t: number) => PAD.l + ((t - from) / (to - from || 1)) * iw
  const y = (v: number) => PAD.t + ih - ((v - lo) / (hi - lo || 1)) * ih
  const span = to - from

  const path = (vals: (number | null)[]) => {
    let d = ''
    let prevT: number | null = null
    vals.forEach((v, i) => {
      if (v == null) { prevT = null; return }
      const gap = prevT != null && times[i] - prevT > bucket * 2.5
      d += `${d && !gap && prevT != null ? 'L' : 'M'}${x(times[i]).toFixed(1)},${y(v).toFixed(1)}`
      prevT = times[i]
    })
    return d
  }

  // direct labels at each line's last point, nudged apart so they never overlap
  const endLabels = () => {
    const out = series.flatMap((s) => {
      const i = s.values.map((v, k) => (v == null ? -1 : k)).filter((k) => k >= 0).pop()
      return i == null ? [] : [{ key: s.key, label: s.label, x: x(times[i]) + 6, y: y(s.values[i] as number) + 4 }]
    }).sort((a, b) => a.y - b.y)
    for (let k = 1; k < out.length; k++) if (out[k].y - out[k - 1].y < 12) out[k].y = out[k - 1].y + 12
    return out
  }

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!times.length) return
    const r = e.currentTarget.getBoundingClientRect()
    const t = from + ((e.clientX - r.left - PAD.l) / iw) * (to - from)
    let best = 0
    times.forEach((tt, i) => { if (Math.abs(tt - t) < Math.abs(times[best] - t)) best = i })
    setHover(Math.abs(times[best] - t) < Math.max(bucket * 3, span / 40) ? best : null)
  }

  const xticks = timeTicks(from, to, Math.max(2, Math.floor(iw / 90)))
  const hoverT = hover != null ? times[hover] : null
  const nearMarkers = hoverT != null ? markers.filter((m) => Math.abs(m.t - hoverT) <= bucket) : []

  return (
    <div className="chart" ref={ref}>
      <Legend series={series} extra={legendExtra} />
      <div className="chart-plot" style={{ height }}>
        <svg width={width} height={height} onPointerMove={onMove} onPointerLeave={() => setHover(null)} role="img">
          {bands.map((b, i) => (
            <rect key={i} className="chart-band" x={x(Math.max(b.from, from))} y={PAD.t}
              width={Math.max(1, x(Math.min(b.to, to)) - x(Math.max(b.from, from)))} height={ih} />
          ))}
          {ticks.map((v) => (
            <g key={v}>
              <line className="chart-grid" x1={PAD.l} x2={width - PAD.r} y1={y(v)} y2={y(v)} />
              <text className="chart-axis" x={PAD.l - 6} y={y(v) + 4} textAnchor="end">{format(v)}</text>
            </g>
          ))}
          <line className="chart-baseline" x1={PAD.l} x2={width - PAD.r} y1={PAD.t + ih} y2={PAD.t + ih} />
          {xticks.map((t) => (
            <text key={t} className="chart-axis" x={x(t)} y={height - 6} textAnchor="middle">{fmtTime(t, span)}</text>
          ))}
          {markers.map((m) => (
            <g key={m.t} className="chart-crash">
              <line x1={x(m.t)} x2={x(m.t)} y1={PAD.t} y2={PAD.t + ih} />
              <text x={x(m.t)} y={PAD.t - 3} textAnchor="middle">⚠</text>
            </g>
          ))}
          {series.map((s) => <path key={s.key} d={path(s.values)} className="chart-line" style={{ stroke: s.color }} />)}
          {series.length <= 4 && endLabels().map((l) => (
            <text key={l.key} className="chart-endlabel" x={l.x} y={l.y}>{l.label}</text>
          ))}
          {hover != null && (
            <g>
              <line className="chart-cross" x1={x(times[hover])} x2={x(times[hover])} y1={PAD.t} y2={PAD.t + ih} />
              {series.map((s) => s.values[hover] != null && (
                <circle key={s.key} cx={x(times[hover])} cy={y(s.values[hover] as number)} r={4.5}
                  className="chart-dot" style={{ fill: s.color }} />
              ))}
            </g>
          )}
        </svg>
        {hover != null && (
          <Tooltip x={x(times[hover])} width={width}>
            <b>{fmtDateTime(times[hover])}</b>
            {series.map((s) => (
              <div key={s.key}><i style={{ background: s.color }} />{s.label}<span>{s.values[hover] == null ? '—' : format(s.values[hover] as number)}</span></div>
            ))}
            {extraTip?.(hover)}
            {nearMarkers.map((m) => <div key={m.t} className="tip-crash">⚠ {m.label}</div>)}
          </Tooltip>
        )}
      </div>
      <Table times={times} series={series} format={format} span={span} />
    </div>
  )
}

/** Count per time bucket (e.g. new PCIe errors). Bars anchored to the baseline. */
export function BarChart({ times, values, label, color, from, to, bucket, markers = [], height = 140 }: {
  times: number[]; values: (number | null)[]; label: string; color: string
  from: number; to: number; bucket: number; markers?: Marker[]; height?: number
}) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(1, ...values.map((v) => v ?? 0))
  const ticks = niceTicks(0, max, 3)
  const hi = Math.max(max, ticks[ticks.length - 1] ?? max)
  const iw = width - PAD.l - PAD.r
  const ih = height - PAD.t - PAD.b
  const x = (t: number) => PAD.l + ((t - from) / (to - from || 1)) * iw
  const y = (v: number) => PAD.t + ih - (v / hi) * ih
  const bw = Math.max(1.5, (bucket / (to - from || 1)) * iw - 2)
  const span = to - from
  const xticks = timeTicks(from, to, Math.max(2, Math.floor(iw / 90)))
  const series: Series[] = [{ key: 'v', label, color, values }]
  return (
    <div className="chart" ref={ref}>
      <div className="chart-plot" style={{ height }}>
        <svg width={width} height={height} onPointerLeave={() => setHover(null)} role="img">
          {ticks.map((v) => (
            <g key={v}>
              <line className="chart-grid" x1={PAD.l} x2={width - PAD.r} y1={y(v)} y2={y(v)} />
              <text className="chart-axis" x={PAD.l - 6} y={y(v) + 4} textAnchor="end">{v}</text>
            </g>
          ))}
          <line className="chart-baseline" x1={PAD.l} x2={width - PAD.r} y1={PAD.t + ih} y2={PAD.t + ih} />
          {xticks.map((t) => <text key={t} className="chart-axis" x={x(t)} y={height - 6} textAnchor="middle">{fmtTime(t, span)}</text>)}
          {markers.map((m) => (
            <g key={m.t} className="chart-crash">
              <line x1={x(m.t)} x2={x(m.t)} y1={PAD.t} y2={PAD.t + ih} />
              <text x={x(m.t)} y={PAD.t - 3} textAnchor="middle">⚠</text>
            </g>
          ))}
          {values.map((v, i) => {
            if (!v) return null
            const h = Math.max(2, PAD.t + ih - y(v))
            const bx = x(times[i]) + 1
            return (
              <path key={times[i]} style={{ fill: color }} className={hover === i ? 'chart-bar hover' : 'chart-bar'}
                d={`M${bx},${PAD.t + ih} v${-(h - Math.min(4, bw / 2))} q0,${-Math.min(4, bw / 2)} ${Math.min(4, bw / 2)},${-Math.min(4, bw / 2)}
                    h${Math.max(0, bw - 2 * Math.min(4, bw / 2))} q${Math.min(4, bw / 2)},0 ${Math.min(4, bw / 2)},${Math.min(4, bw / 2)} v${h - Math.min(4, bw / 2)} z`} />
            )
          })}
          {/* wide invisible hit targets, one per bucket */}
          {times.map((t, i) => (
            <rect key={t} x={x(t) - 2} y={PAD.t} width={Math.max(bw + 4, 8)} height={ih} fill="transparent"
              onPointerEnter={() => setHover(i)} />
          ))}
        </svg>
        {hover != null && (
          <Tooltip x={x(times[hover])} width={width}>
            <b>{fmtDateTime(times[hover])}</b>
            <div><i style={{ background: color }} />{label}<span>{values[hover] ?? 0}</span></div>
          </Tooltip>
        )}
      </div>
      <Table times={times} series={series} format={(v) => String(v)} span={span} />
    </div>
  )
}
