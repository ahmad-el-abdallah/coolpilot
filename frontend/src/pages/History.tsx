import { useState } from 'react'
import { api, type BlackboxSample, type BlackboxStatus, type CrashEvent, type HistoryData } from '../api'
import { BarChart, fmtDateTime, LineChart, type Band } from '../components/Charts'
import { Badge, Button, Card, Segmented, Toggle } from '../components/ui'
import { usePoll, useToast } from '../hooks'

const RANGES = ['6h', '24h', '7d', '30d', '90d'] as const
const S1 = 'var(--series-1)'
const S2 = 'var(--series-2)'
const deg = (v: number) => `${Math.round(v)}°`
const watts = (v: number) => `${v === 0 ? 0 : v.toFixed(v < 10 ? 1 : 0)} W`
const rpm = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : `${Math.round(v)}`)

function bytes(n: number) {
  return n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1e3)} kB`
}

/** Consecutive buckets where Stability mode was on -> shaded bands. */
function stabilityBands(points: HistoryData['points'], bucket: number): Band[] {
  const out: Band[] = []
  for (const p of points) {
    if ((p.stab ?? 0) < 0.5) continue
    const last = out[out.length - 1]
    if (last && p.t - last.to <= bucket * 1.5) last.to = p.t + bucket
    else out.push({ from: p.t, to: p.t + bucket })
  }
  return out
}

function Chip({ label, value, tone }: { label: string; value: string; tone?: 'good' | 'warn' | 'bad' }) {
  return <span className={`chip ${tone ?? ''}`}><small>{label}</small>{value}</span>
}

function CrashCard({ c }: { c: CrashEvent }) {
  const [samples, setSamples] = useState<BlackboxSample[] | null>(null)
  const [open, setOpen] = useState(false)
  const toast = useToast()
  const s = c.summary
  const toggle = async () => {
    if (!open && !samples) {
      try { setSamples((await api.get<{ samples: BlackboxSample[] }>(`/blackbox/crashes/${c.boot}`)).samples) }
      catch (e) { toast('error', (e as Error).message); return }
    }
    setOpen(!open)
  }
  const times = samples?.map((x) => x.ts) ?? []
  const from = times[0] ?? c.end - 120
  const marker = [{ t: c.end, label: 'Froze / reset here' }]
  return (
    <div className="crash-card" id={`crash-${c.boot}`}>
      <div className="crash-head">
        <span className="crash-icon" aria-hidden>⚠</span>
        <div>
          <b>{fmtDateTime(c.end)}</b>
          <small className="muted"> · after {c.minutes < 60 ? `${Math.round(c.minutes)} min` : `${(c.minutes / 60).toFixed(1)} h`} of use</small>
        </div>
        <span className="spacer" />
        {c.recorded
          ? <Button kind="ghost" onClick={toggle}>{open ? 'Hide' : 'Last 2 minutes'}</Button>
          : <Badge>no recording (before the black box)</Badge>}
      </div>
      {c.recorded && (
        <div className="chips">
          <Chip label="CPU peak" value={s.cpu_temp_max != null ? `${Math.round(s.cpu_temp_max)}°C` : '—'}
            tone={(s.cpu_temp_max ?? 0) >= 90 ? 'bad' : (s.cpu_temp_max ?? 0) >= 80 ? 'warn' : undefined} />
          <Chip label="CPU load" value={s.cpu_usage_avg != null ? `${Math.round(s.cpu_usage_avg)}%` : '—'} />
          <Chip label="GPU peak" value={s.gpu_temp_max != null ? `${Math.round(s.gpu_temp_max)}°C` : s.gpu_state === 'suspended' ? 'asleep' : '—'} />
          <Chip label="Power" value={s.ac ? 'charger' : 'battery'} />
          <Chip label="Mode" value={`${s.profile ?? '?'}${s.stability ? ' + Stability' : ''}`} />
          <Chip label="New GPU link errors" value={String(s.pcie_err_delta ?? 0)} tone={(s.pcie_err_delta ?? 0) > 0 ? 'warn' : undefined} />
          {s.gap_to_end != null && s.gap_to_end > 10 && <Chip label="Last reading" value={`${Math.round(s.gap_to_end)} s before the end`} />}
        </div>
      )}
      {open && samples && (
        <div className="crash-charts">
          <LineChart times={times} from={from} to={c.end} bucket={2} height={130} format={deg} markers={marker}
            series={[{ key: 'cpu', label: 'CPU °C', color: S1, values: samples.map((x) => x.cpu_temp) },
                     { key: 'gpu', label: 'GPU °C', color: S2, values: samples.map((x) => x.gpu_temp) }]} />
          <LineChart times={times} from={from} to={c.end} bucket={2} height={110} zeroBased markers={marker}
            format={(v) => `${Math.round(v)}%`}
            series={[{ key: 'use', label: 'CPU load', color: S1, values: samples.map((x) => x.cpu_usage) }]} />
          <BarChart times={times} from={from} to={c.end} bucket={2} height={100} markers={marker}
            label="New GPU link errors" color={S2} values={samples.map((x) => x.pcie_new ?? 0)} />
        </div>
      )}
    </div>
  )
}

export function History() {
  // a link like #/history?range=30d opens that range
  const [range, setRange] = useState<(typeof RANGES)[number]>(() => {
    const r = new URLSearchParams(location.hash.split('?')[1]).get('range')
    return (RANGES as readonly string[]).includes(r ?? '') ? (r as (typeof RANGES)[number]) : '24h'
  })
  const [showAll, setShowAll] = useState(false)
  const { data: h } = usePoll<HistoryData>(`/history?range=${range}`, 60000)
  const { data: bb, setData: setBb } = usePoll<BlackboxStatus>('/blackbox', 15000)
  const { data: cr } = usePoll<{ crashes: CrashEvent[] }>('/blackbox/crashes', 60000)
  const toast = useToast()

  const pts = h?.points ?? []
  const times = pts.map((p) => p.t)
  const common = h ? { times, from: h.from, to: h.to, bucket: h.bucket } : null
  const markers = (h?.crashes ?? []).map((c) => ({ t: c.t, label: `Crash · ${fmtDateTime(c.t)}` }))
  const bands = h ? stabilityBands(pts, h.bucket) : []
  const legendExtra = (
    <>
      {bands.length > 0 && <span><i className="band-swatch" />Stability mode on</span>}
      {markers.length > 0 && <span><b className="crash-swatch">⚠</b>Crash</span>}
    </>
  )
  const crashes = cr?.crashes ?? []
  const recent = showAll ? crashes : crashes.slice(0, 8)

  return (
    <div className="page">
      <Card
        title={<>Black box {bb?.enabled ? (bb.running ? <Badge tone="good">● recording</Badge> : <Badge tone="warn">not running</Badge>) : <Badge>off</Badge>}</>}
        subtitle="Records temperatures, load, fans, power and GPU link errors every few seconds, straight to disk — so the moments before a freeze are never lost."
        actions={bb && <Toggle checked={bb.enabled} label="Black box recording"
          onChange={async (v) => { try { setBb(await api.post<BlackboxStatus>('/blackbox', { enabled: v })); toast('ok', v ? 'Black box on' : 'Black box off') } catch (e) { toast('error', (e as Error).message) } }} />}
      >
        {bb && (
          <p className="muted small">
            Every {bb.interval} s · {bb.samples.toLocaleString()} readings kept (3 days) · charts kept for a year ·{' '}
            {bb.crashes_kept} crash{bb.crashes_kept === 1 ? '' : 'es'} captured · {bytes(bb.db_bytes)}
            {bb.last && <> · last reading {new Date(bb.last.ts * 1000).toLocaleTimeString()}</>}
          </p>
        )}
        {bb?.error && <p className="banner bad">Recorder error: {bb.error}</p>}
        {bb && !bb.running && bb.enabled && (
          <p className="banner warn">The recorder isn't running — it starts with the installed service (reinstall with <span className="mono">sudo ./install.sh</span>).</p>
        )}
      </Card>

      <Card title="Crashes" subtitle={crashes.length ? `${crashes.length} session${crashes.length === 1 ? '' : 's'} ended in a freeze or reset (from the system log). Sessions since the black box started include what the laptop was doing.` : undefined}>
        {!crashes.length && <p className="muted">No crashes found. 🎉</p>}
        {recent.map((c) => <CrashCard key={c.boot} c={c} />)}
        {crashes.length > 8 && (
          <Button kind="ghost" onClick={() => setShowAll(!showAll)}>{showAll ? 'Show fewer' : `Show all ${crashes.length}`}</Button>
        )}
      </Card>

      <div className="history-filters">
        <Segmented value={range} options={[...RANGES]} onChange={setRange} labels={{ '6h': '6 hours', '24h': '24 hours', '7d': '7 days', '30d': '30 days', '90d': '90 days' }} />
        {h?.oldest && <span className="muted small">History since {fmtDateTime(h.oldest)}</span>}
      </div>

      {common && !pts.length && (
        <Card><p className="muted">No history in this range yet — the black box started recording{h?.oldest ? ` ${fmtDateTime(h.oldest)}` : ' recently'}. Charts fill in after a few minutes.</p></Card>
      )}
      {common && pts.length > 0 && (
        <>
          <Card title="Temperatures" subtitle="Average per interval · hover for peaks">
            <LineChart {...common} format={deg} markers={markers} bands={bands} legendExtra={legendExtra}
              series={[{ key: 'cpu', label: 'CPU', color: S1, values: pts.map((p) => p.cpu) },
                       { key: 'gpu', label: 'GPU', color: S2, values: pts.map((p) => p.gpu) }]}
              extraTip={(i) => (
                <div className="tip-extra">Peak CPU {pts[i].cpu_max != null ? deg(pts[i].cpu_max as number) : '—'} · GPU {pts[i].gpu_max != null ? deg(pts[i].gpu_max as number) : '—'} · load {pts[i].usage != null ? `${Math.round(pts[i].usage as number)}%` : '—'}</div>
              )} />
          </Card>
          <Card title="Battery power" subtitle="Draw while on battery, charge rate while plugged in">
            <LineChart {...common} format={watts} zeroBased markers={markers} bands={bands} height={150}
              series={[{ key: 'bat', label: 'Battery', color: S1, values: pts.map((p) => p.bat_w) }]}
              extraTip={(i) => <div className="tip-extra">{(pts[i].ac ?? 0) >= 0.5 ? '🔌 on charger' : '🔋 on battery'}</div>} />
          </Card>
          <Card title="Fan speed" subtitle="Average of both fans, rpm">
            <LineChart {...common} format={rpm} zeroBased markers={markers} bands={bands} height={150}
              series={[{ key: 'fan', label: 'Fans', color: S1, values: pts.map((p) => p.fan) }]} />
          </Card>
          <Card title="GPU link errors" subtitle="New corrected errors on the CPU ↔ GPU PCIe link per interval (0 is healthy)">
            <BarChart {...common} label="New errors" color={S2} markers={markers}
              values={pts.map((p) => p.pcie ?? 0)} />
          </Card>
        </>
      )}
    </div>
  )
}
