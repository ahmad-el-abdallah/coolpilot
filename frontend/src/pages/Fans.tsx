import { useState } from 'react'
import { api, type FansState } from '../api'
import { FanCurveEditor } from '../components/FanCurveEditor'
import { Badge, Button, Card, Toggle } from '../components/ui'
import { fmt, summarize, usePoll, useSensorHistory, useToast } from '../hooks'

/** Small read-only curve drawing for the mode tiles. */
function MiniCurve({ points }: { points: [number, number][] }) {
  const W = 160, H = 50
  const x = (t: number) => ((t - 20) / 90) * W
  const y = (p: number) => H - 2 - (p / 255) * (H - 4)
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${x(p[0])},${y(p[1])}`).join(' ')
  return (
    <svg className="mini-curve" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      <path d={`${d} L${x(points[points.length - 1][0])},${H} L${x(points[0][0])},${H} Z`} className="area" />
      <path d={d} className="line" />
    </svg>
  )
}

export function Fans() {
  const { data, setData, refresh } = usePoll<FansState>('/fans', 10000)
  const { now } = useSensorHistory(2)
  const toast = useToast()
  const [draft, setDraft] = useState<Record<number, [number, number][]>>({})
  const [busy, setBusy] = useState(false)

  const send = async (fan: number, body: object, msg: string) => {
    try {
      setData(await api.post<FansState>(`/fans/${fan}`, body))
      setDraft((d) => { const n = { ...d }; delete n[fan]; return n })
      toast('ok', msg)
    } catch (e) {
      toast('error', (e as Error).message)
      refresh()
    }
  }
  const setMode = async (mode: 'default' | 'stability') => {
    setBusy(true)
    try {
      const r = await api.post<FansState & { results: Record<string, string> }>('/fans/mode', { mode })
      setData(r)
      setDraft({})
      const { failed } = summarize(r.results)
      if (failed.length) toast('error', `Couldn't set the fans: ${failed.join('; ')}`)
      else toast('ok', mode === 'default' ? 'Fans back to factory default' : 'Stability fan curve on — kept after reboot and mode changes')
    } catch (e) {
      toast('error', (e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!data) return <div className="page"><Card><p className="muted">Loading…</p></Card></div>
  if (!data.available) return <div className="page"><Card title="Fans"><p>Custom fan curves are not supported.</p></Card></div>

  const mode = data.mode ?? 'default'
  const byStabilityMode = data.controlled_by === 'stability_mode'
  const firmwareCurve = data.fans.find((f) => !f.custom)?.points
  const tiles = [
    {
      id: 'default' as const, icon: '🏭', title: 'Default',
      text: 'Factory ASUS behaviour — exactly like the first boot. The firmware picks the curve for Silent / Balanced / Turbo.',
      plus: 'Quietest at idle; fans can stop completely.', minus: 'Temperatures swing more between idle and load.',
      curve: mode === 'default' ? firmwareCurve : undefined,
    },
    {
      id: 'stability' as const, icon: '🛡', title: 'Stability',
      text: 'Made for the freeze problem: fans start early, ramp smoothly and never fully stop.',
      plus: 'Cooler and, above all, steadier chips — fewer heat swings stressing weak solder joints.',
      minus: 'A soft fan hum even at idle.',
      curve: data.stability_curve,
    },
  ]

  return (
    <div className="page">
      <Card title="Fan mode" subtitle="Your choice is kept after reboot, sleep and Silent / Balanced / Turbo changes.">
        {byStabilityMode && (
          <p className="banner info row">
            Stability mode's own fan option is controlling the fans right now. This choice takes over again when
            Stability mode is off or its fan option is excluded.
            <span className="spacer" />
            <button className="link" onClick={() => { location.hash = '#/stability' }}>Stability page →</button>
          </p>
        )}
        <div className="mode-grid">
          {tiles.map((t) => {
            const active = mode === t.id
            return (
              <button
                key={t.id} type="button" disabled={busy}
                className={`mode-tile ${active ? 'active' : ''}`} onClick={() => !active && setMode(t.id)}
              >
                <div className="mode-head">
                  <span className="mode-icon">{t.icon}</span>
                  <b>{t.title}</b>
                  {active && <Badge tone="good">● active</Badge>}
                </div>
                <p>{t.text}</p>
                <p><span className="plus">＋</span> {t.plus}</p>
                <p><span className="minus">－</span> {t.minus}</p>
                {t.curve && <MiniCurve points={t.curve} />}
              </button>
            )
          })}
          <div className={`mode-tile static ${mode === 'custom' ? 'active' : ''}`}>
            <div className="mode-head">
              <span className="mode-icon">✎</span><b>Custom</b>
              {mode === 'custom' && <Badge tone="good">● active</Badge>}
            </div>
            <p>{mode === 'custom'
              ? 'Your own curves from the editors below. They stick like the other modes.'
              : 'Drag the points in an editor below and press “Apply curve” to make your own.'}</p>
          </div>
        </div>
      </Card>

      {data.fans.map((f) => {
        const pts = draft[f.fan] ?? f.points
        const temp = f.fan === 1 ? now?.cpu.temp : now?.gpu.temp ?? now?.cpu.temp
        return (
          <Card
            key={f.fan} id={`fan-${f.fan}`}
            title={<>{f.label} {f.custom ? <Badge tone="info">custom curve</Badge> : <Badge>firmware auto</Badge>}</>}
            subtitle={`Now ${fmt.rpm(now?.fans[f.fan - 1])} · ${fmt.temp(temp)}`}
            actions={
              <label className="inline">
                Custom <Toggle checked={f.custom} onChange={(v) => send(f.fan, { custom: v }, v ? 'Custom curve on' : 'Back to firmware curve')} />
              </label>
            }
          >
            <FanCurveEditor points={pts} currentTemp={temp} onChange={(p) => setDraft((d) => ({ ...d, [f.fan]: p }))} />
            <div className="row wrap">
              <span className="muted">Start from:</span>
              {Object.entries(data.presets ?? {}).map(([name, p]) => (
                <Button key={name} kind="ghost" onClick={() => setDraft((d) => ({ ...d, [f.fan]: p }))}>{name}</Button>
              ))}
              <span className="spacer" />
              <Button onClick={() => send(f.fan, { reset: true }, 'Fan curve reset to factory')}>Reset</Button>
              <Button kind="primary" disabled={!draft[f.fan]} onClick={() => send(f.fan, { points: pts, custom: true }, 'Fan curve applied')}>
                Apply curve
              </Button>
            </div>
          </Card>
        )
      })}
    </div>
  )
}
