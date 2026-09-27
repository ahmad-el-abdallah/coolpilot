import { useEffect, useState } from 'react'
import { api, type PcieBoot, type PcieSample, type PcieState } from '../api'
import { AreaPad } from '../components/AreaPad'
import { Badge, Button, Card, Stat, Toggle } from '../components/ui'
import { usePoll, useToast } from '../hooks'

const AREAS: Record<string, string> = {
  '1': 'top-left', '2': 'top-middle', '3': 'top-right', '4': 'palm-left', '5': 'touchpad',
  '6': 'palm-right', '7': 'hinge-left', '8': 'hinge-right', '9': 'lift/tilt', '0': 'not-touching',
}

/** One bar per second: height = new errors that second; label where the pressed area changed. */
function ErrorBars({ samples }: { samples: PcieSample[] }) {
  const W = 600, H = 120, n = Math.max(samples.length, 60)
  const max = Math.max(5, ...samples.map((s) => s.delta))
  const bw = W / n
  return (
    <svg viewBox={`0 0 ${W} ${H + 18}`} className="error-bars" preserveAspectRatio="none">
      <line x1={0} x2={W} y1={H} y2={H} className="axis" />
      {samples.map((s, i) => {
        const x = (n - samples.length + i) * bw
        const changed = i > 0 && samples[i - 1].area !== s.area
        const h = s.delta ? Math.max(3, (s.delta / max) * (H - 4)) : 1.5
        return (
          <g key={s.t}>
            {changed && (
              <>
                <line x1={x} x2={x} y1={0} y2={H} className="mark" />
                <text x={x + 3} y={H + 13} className="mark-label">{s.area}</text>
              </>
            )}
            <rect x={x + 0.5} y={H - h} width={Math.max(bw - 1, 1)} height={h} className={s.delta ? 'bad' : 'ok'} />
          </g>
        )
      })}
      <text x={W - 4} y={12} textAnchor="end" className="scale">max {max}/s</text>
    </svg>
  )
}

export function Pcie() {
  const [fast, setFast] = useState(false)
  const { data, setData, refresh } = usePoll<PcieState>('/pcie', fast ? 1000 : 4000)
  const { data: hist } = usePoll<{ boots: PcieBoot[] }>('/pcie/history', 120000)
  const toast = useToast()
  const [load, setLoad] = useState(true)
  const [minutes, setMinutes] = useState(30)
  const w = data?.watch
  // poll every second only while a watch is running
  const running = !!w?.running
  useEffect(() => setFast(running), [running])

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    try { await fn(); if (ok) toast('ok', ok); await refresh() } catch (e) { toast('error', (e as Error).message) }
  }
  const gpu = data?.links.find((l) => l.label === 'NVIDIA GPU')
  const worst = w?.per_area.find((a) => a.area !== 'baseline' && a.errors > 0)
  const baseline = w?.per_area.find((a) => a.area === 'baseline')
  const maxHist = Math.max(1, ...(hist?.boots.map((b) => b.gpu_pcie_errors) ?? [1]))
  const remaining = w?.ends ? Math.max(0, Math.round((w.ends - Date.now() / 1000) / 60)) : null

  return (
    <div className="page">
      <p className="banner info">
        The NVIDIA GPU talks to the CPU over PCIe lanes that run through CPU solder balls, the board and GPU solder
        balls. When a packet arrives corrupted the hardware resends it and counts a <b>corrected error</b>. A healthy
        link shows ~0. If pressing one spot makes errors jump, that area has a weak connection.
      </p>

      <Card
        title={<>Live press test {w?.running && <Badge tone="warn">watching · {remaining} min left</Badge>}</>}
        subtitle="Samples the GPU link every second and writes each reading to disk (survives a freeze)."
      >
        {!data?.available && <p className="banner bad">GPU PCIe error counters aren't available.</p>}
        {w?.running ? (
          <>
            <div className="stats big">
              <Stat label="New errors" value={w.errors_since_start} sub="since start" tone={w.errors_since_start ? 'bad' : 'good'} />
              <Stat label="Last minute" value={w.errors_last_min} tone={w.errors_last_min ? 'bad' : 'good'} />
              <Stat label="Pressing now" value={w.area ?? '—'} />
              <Stat label="Link" value={gpu ? `Gen${gpu.gen} x${gpu.width}` : '—'} sub={w.load ? 'GPU load on' : 'no load'}
                tone={gpu?.lanes_lost ? 'bad' : undefined} />
            </div>
            <ErrorBars samples={w.samples} />
            <p className="muted">Hold still ~30 s first (baseline). Then click an area below and press that spot for ~15 s.</p>
            <AreaPad
              areas={AREAS} active={w.area}
              onMark={(k) => act(() => api.post('/pcie/watch/mark', { area: k }))}
              extra={<Button kind="danger" onClick={() => act(async () => setData({ ...data!, watch: await api.post('/pcie/watch/stop') }), 'Watch stopped')}>Stop</Button>}
            />
          </>
        ) : (
          <div className="row wrap">
            <label className="inline">Keep GPU busy (glmark2) <Toggle checked={load} onChange={setLoad} /></label>
            <label className="inline">Minutes
              <input type="number" min={1} max={240} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} style={{ width: 70 }} />
            </label>
            <Button kind="primary" disabled={!data?.available}
              onClick={() => act(async () => {
                const st = await api.post<PcieState['watch']>('/pcie/watch/start', { load, minutes })
                if (st.note) toast('error', st.note)
              }, 'Watching the GPU link')}
            >
              Start watching
            </Button>
            <span className="muted">Without load the GPU may sleep and the link switches off, so nothing is measured.</span>
          </div>
        )}
      </Card>

      {!!w?.per_area.length && (
        <Card title="Results by area" subtitle={w.log ? <>Log: <span className="mono">~/crashdiag/logs/{w.log}</span></> : undefined}>
          {worst && (baseline?.per_min ?? 0) < worst.per_min && (
            <p className="banner warn">
              Most errors while pressing <b>{worst.area}</b>: {worst.per_min}/min vs {baseline?.per_min ?? 0}/min at rest.
              Repeat it 2–3 times — if it stays the worst spot, tell the technician.
            </p>
          )}
          <table className="table">
            <thead><tr><th>Area</th><th>Time</th><th>Errors</th><th>Errors / min</th><th>Seconds with errors</th></tr></thead>
            <tbody>
              {w.per_area.map((a) => (
                <tr key={a.area} className={a === worst ? 'hot' : ''}>
                  <td>{a.area}</td><td>{a.seconds}s</td><td>{a.errors}</td><td><b>{a.per_min}</b></td><td>{a.bursts}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <Card title="All PCIe links" subtitle="Error counters since this boot. Only the CPU↔GPU link should be suspicious.">
        <table className="table">
          <thead><tr><th>Link</th><th>Speed now / max</th><th>Lanes</th><th>Corrected</th><th>Non-fatal</th><th>Fatal</th></tr></thead>
          <tbody>
            {data?.links.map((l) => (
              <tr key={l.bdf} title={l.name}>
                <td>{l.label} <span className="mono muted">{l.bdf.slice(5)}</span></td>
                <td>Gen{l.gen ?? '?'} / Gen{l.max_gen ?? '?'}</td>
                <td>{l.lanes_lost ? <Badge tone="bad">x{l.width} of x{l.max_width}</Badge> : `x${l.width}`}</td>
                <td>
                  {l.correctable ? <Badge tone={l.correctable > 100 ? 'bad' : 'warn'}>{l.correctable}</Badge> : <Badge tone="good">0</Badge>}
                  {' '}<span className="muted">{Object.entries(l.breakdown).map(([k, v]) => `${k} ${v}`).join(', ')}</span>
                </td>
                <td>{l.nonfatal || '0'}</td>
                <td>{l.fatal ? <Badge tone="bad">{l.fatal}</Badge> : '0'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted">
          A link running slower than its max at idle is normal (power saving). Fewer lanes than max, or any fatal
          errors, is a real hardware warning.
        </p>
      </Card>

      <Card title="GPU link errors per session" subtitle="From the kernel log (rate-limited, so it undercounts — use it to compare sessions).">
        <table className="table">
          <thead><tr><th>Session start</th><th>Length</th><th>GPU link errors logged</th><th>Ended</th></tr></thead>
          <tbody>
            {hist?.boots.map((b) => (
              <tr key={b.boot_id}>
                <td>{new Date(b.start * 1000).toLocaleString()}</td>
                <td>{b.minutes < 60 ? `${b.minutes} min` : `${(b.minutes / 60).toFixed(1)} h`}</td>
                <td>
                  <div className="hbar"><span style={{ width: `${(Math.max(b.gpu_pcie_errors, 0) / maxHist) * 100}%` }} /></div>
                  {b.gpu_pcie_errors < 0 ? '?' : b.gpu_pcie_errors}
                </td>
                <td>{b.ending === 'crash' ? <Badge tone="bad">crash</Badge> : b.ending === 'running' ? <Badge tone="info">now</Badge> : <Badge tone="good">{b.ending}</Badge>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  )
}
