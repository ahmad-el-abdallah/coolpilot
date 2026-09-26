import type { Setting } from '../api'
import { useSettings } from '../hooks'
import { Segmented, Slider, Toggle } from './ui'

const PROFILE_LABELS: Record<string, string> = { quiet: '🌙 Silent', balanced: '⚖️ Balanced', performance: '🚀 Turbo' }

function formatter(s: Setting) {
  if (s.unit === 'MHz') return (v: number) => `${(v / 1000).toFixed(2)} GHz`
  if (s.unit) return (v: number) => `${v} ${s.unit}`
  return (v: number) => String(v)
}

function marksFor(s: Setting): number[] | undefined {
  if (s.key === 'cpu_max_mhz') return [2000, 2500, 3000, 3500, s.max!].filter((m) => m >= s.min! && m <= s.max!)
  if (s.key === 'charge_limit') return [60, 80, 100]
  if (s.key.startsWith('ppt_')) return [s.min!, 35, 45, 60, s.max!].filter((m, i, a) => m >= s.min! && a.indexOf(m) === i)
  return undefined
}

/** Renders the right control for any backend setting, wrapped in a searchable row. */
export function SettingControl({ k, extra }: { k: string; extra?: React.ReactNode }) {
  const { byKey, set } = useSettings()
  const s = byKey[k]
  if (!s) return null
  let control: React.ReactNode
  if (!s.available) {
    control = <span className="muted">Not supported on this laptop</span>
  } else if (s.locked) {
    control = (
      <span className="muted" title="ASUS firmware pins this limit on battery; plug in the charger to change it">
        🔒 Fixed at <b>{formatter(s)(Number(s.value))}</b> right now (firmware limit — on battery)
      </span>
    )
  } else if (s.kind === 'bool') {
    control = <Toggle checked={!!s.value} onChange={(v) => set(k, v)} label={s.label} />
  } else if (s.kind === 'choice') {
    control = (
      <Segmented
        value={s.value as string} options={s.choices ?? []} onChange={(v) => set(k, v)}
        labels={k === 'platform_profile' ? PROFILE_LABELS : undefined}
      />
    )
  } else {
    const step = s.unit === 'MHz' ? 100 : 1
    control = (
      <Slider
        value={Number(s.value)} min={s.min ?? 0} max={s.max ?? 100} step={step}
        format={formatter(s)} marks={marksFor(s)} onCommit={(v) => set(k, v)}
      />
    )
  }
  return (
    <div className="setting-row" id={`setting-${k}`}>
      <div className="setting-text">
        <label>{s.label}</label>
        <p className="muted">{s.help}</p>
        {s.default !== undefined && s.available && !s.locked && s.kind === 'int' && s.value !== s.default && (
          <button type="button" className="link" onClick={() => set(k, s.default)}>
            reset to default ({formatter(s)(Number(s.default))})
          </button>
        )}
      </div>
      <div className="setting-control">{control}{extra}</div>
    </div>
  )
}
