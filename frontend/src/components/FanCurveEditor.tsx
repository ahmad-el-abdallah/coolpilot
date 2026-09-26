import { useEffect, useRef, useState } from 'react'

type Pt = [number, number]
const W = 520, H = 240, PAD = { l: 40, r: 12, t: 12, b: 28 }
const T_MIN = 20, T_MAX = 110

const x = (t: number) => PAD.l + ((t - T_MIN) / (T_MAX - T_MIN)) * (W - PAD.l - PAD.r)
const y = (pwm: number) => H - PAD.b - (pwm / 255) * (H - PAD.t - PAD.b)
const toT = (px: number) => T_MIN + ((px - PAD.l) / (W - PAD.l - PAD.r)) * (T_MAX - T_MIN)
const toPwm = (py: number) => ((H - PAD.b - py) / (H - PAD.t - PAD.b)) * 255
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

/** Drag 8 points; temps and speeds are kept non-decreasing so the backend accepts them. */
export function FanCurveEditor({ points, onChange, currentTemp, disabled }: {
  points: Pt[]; onChange: (p: Pt[]) => void; currentTemp?: number | null; disabled?: boolean
}) {
  const svg = useRef<SVGSVGElement>(null)
  const [drag, setDrag] = useState<number | null>(null)
  const [pts, setPts] = useState<Pt[]>(points)
  useEffect(() => setPts(points), [points])

  const move = (e: React.PointerEvent) => {
    if (drag === null || !svg.current) return
    const r = svg.current.getBoundingClientRect()
    const px = ((e.clientX - r.left) / r.width) * W
    const py = ((e.clientY - r.top) / r.height) * H
    const next = pts.map((p) => [...p] as Pt)
    const lo = drag > 0 ? next[drag - 1] : ([T_MIN, 0] as Pt)
    const hi = drag < next.length - 1 ? next[drag + 1] : ([T_MAX, 255] as Pt)
    next[drag] = [
      Math.round(clamp(toT(px), lo[0], hi[0])),
      Math.round(clamp(toPwm(py), lo[1], hi[1])),
    ]
    setPts(next)
  }
  const end = () => {
    if (drag !== null) onChange(pts)
    setDrag(null)
  }

  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p[0])},${y(p[1])}`).join(' ')
  return (
    <svg
      ref={svg} viewBox={`0 0 ${W} ${H}`} className={`fan-curve ${disabled ? 'disabled' : ''}`}
      onPointerMove={move} onPointerUp={end} onPointerLeave={end}
    >
      {[0, 25, 50, 75, 100].map((pc) => (
        <g key={pc}>
          <line x1={PAD.l} x2={W - PAD.r} y1={y(pc * 2.55)} y2={y(pc * 2.55)} className="grid" />
          <text x={PAD.l - 6} y={y(pc * 2.55) + 4} textAnchor="end">{pc}%</text>
        </g>
      ))}
      {[30, 50, 70, 90, 110].map((t) => (
        <text key={t} x={x(t)} y={H - 8} textAnchor="middle">{t}°</text>
      ))}
      {currentTemp != null && (
        <line x1={x(currentTemp)} x2={x(currentTemp)} y1={PAD.t} y2={H - PAD.b} className="now" />
      )}
      <path d={`${path} L${x(pts[pts.length - 1][0])},${y(0)} L${x(pts[0][0])},${y(0)} Z`} className="area" />
      <path d={path} className="line" />
      {pts.map((p, i) => (
        <g key={i}>
          <circle
            cx={x(p[0])} cy={y(p[1])} r={drag === i ? 8 : 6} className="pt"
            onPointerDown={(e) => { if (disabled) return; (e.target as Element).setPointerCapture?.(e.pointerId); setDrag(i) }}
          />
          {drag === i && (
            <text x={x(p[0])} y={y(p[1]) - 12} textAnchor="middle" className="tip">
              {p[0]}° → {Math.round((p[1] / 255) * 100)}%
            </text>
          )}
        </g>
      ))}
    </svg>
  )
}
