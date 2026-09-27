import { useEffect, useState } from 'react'
import { api, type AlertItem, type AlertsState } from '../api'
import { Badge, Button, Card, Slider, Toggle } from '../components/ui'
import { usePoll, useToast } from '../hooks'

const when = (ts: number) => new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })

/** Number box that saves on Enter or when it loses focus. */
function NumberBox({ value, min, max, unit, disabled, onCommit }: {
  value: number; min: number; max: number; unit?: string; disabled?: boolean; onCommit: (v: number) => void
}) {
  const [text, setText] = useState(String(value))
  useEffect(() => setText(String(value)), [value])
  const commit = () => {
    const v = Math.round(Number(text))
    if (!Number.isFinite(v) || v < min || v > max) { setText(String(value)); return }
    if (v !== value) onCommit(v)
  }
  return (
    <span className="inline">
      <input type="number" min={min} max={max} value={text} disabled={disabled} style={{ width: 96 }}
        onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === 'Enter' && commit()} />
      {unit}
    </span>
  )
}

export function Alerts() {
  const { data, setData } = usePoll<AlertsState>('/alerts', 15000)
  const toast = useToast()
  const [testing, setTesting] = useState(false)

  const save = async (body: object, msg: string) => {
    try {
      setData(await api.post<AlertsState>('/alerts', body))
      toast('ok', msg)
      return true
    } catch (e) {
      toast('error', (e as Error).message)
      return false
    }
  }
  const setItem = (it: AlertItem, patch: Partial<AlertItem>, msg: string) => save({ items: { [it.kind]: patch } }, msg)

  const test = async () => {
    setTesting(true)
    try {
      const r = await api.post<{ sent: number; via: string[]; error?: string }>('/alerts/test')
      if (r.sent) toast('ok', 'Test alert sent — you should see it on your desktop now')
      else toast('error', `The test alert couldn't be shown: ${r.error}`)
    } catch (e) {
      toast('error', (e as Error).message)
    } finally {
      setTesting(false)
    }
  }

  if (!data) return <div className="page"><Card><p className="muted">Loading…</p></Card></div>
  return (
    <div className="page">
      <Card
        title="Desktop alerts"
        subtitle="CoolPilot watches the laptop in the background and shows a normal desktop notification when something needs your attention — even when this page is closed."
        actions={<Toggle checked={data.enabled} label="Desktop alerts" onChange={(v) => save({ enabled: v }, v ? 'Desktop alerts on' : 'Desktop alerts off')} />}
      >
        <div className="row wrap">
          <Button onClick={test} disabled={testing}>{testing ? 'Sending…' : 'Send a test alert'}</Button>
          <span className="muted">
            {data.desktops
              ? `${data.desktops === 1 ? 'Your desktop is' : `${data.desktops} desktops are`} ready to show alerts.`
              : 'No logged-in desktop found right now — alerts are kept below and shown once you log in.'}
          </span>
        </div>
        {!data.enabled && <p className="banner warn">All alerts are off. The switches below keep your choices for when you turn them back on.</p>}
      </Card>

      <Card title="What to alert about">
        {data.items.map((it) => (
          <div className="setting-row" key={it.kind} id={`alert-${it.kind}`}>
            <div className="setting-text">
              <label>{it.label}</label>
              <p className="muted">{it.help}</p>
            </div>
            <div className="setting-control">
              {it.kind === 'cpu_hot' && it.threshold !== undefined && (
                <div style={{ flex: 1, maxWidth: 320 }}>
                  <Slider value={it.threshold} min={it.min!} max={it.max!} marks={[85, 90, 95]} disabled={!data.enabled || !it.enabled}
                    format={(v) => `${v}°C`} onCommit={(v) => setItem(it, { threshold: v }, `Alert when the CPU reaches ${v}°C`)} />
                </div>
              )}
              {it.kind === 'pcie_burst' && it.threshold !== undefined && (
                <NumberBox value={it.threshold} min={it.min!} max={it.max!} unit={it.unit} disabled={!data.enabled || !it.enabled}
                  onCommit={(v) => setItem(it, { threshold: v }, `Alert at ${v} GPU link errors per minute`)} />
              )}
              <Toggle checked={it.enabled} disabled={!data.enabled} label={it.label}
                onChange={(v) => setItem(it, { enabled: v }, `${it.label}: alert ${v ? 'on' : 'off'}`)} />
            </div>
          </div>
        ))}
      </Card>

      <Card
        title="Recent alerts"
        actions={data.recent.length > 0 && (
          <Button kind="ghost" onClick={async () => { try { setData(await api.del<AlertsState>('/alerts/recent')) } catch (e) { toast('error', (e as Error).message) } }}>Clear</Button>
        )}
      >
        {data.recent.length === 0
          ? <p className="muted">No alerts yet.</p>
          : (
            <table className="table">
              <thead><tr><th>When</th><th>Alert</th><th>On the desktop</th></tr></thead>
              <tbody>
                {data.recent.map((a) => (
                  <tr key={a.id} className={a.critical ? 'hot' : ''}>
                    <td style={{ whiteSpace: 'nowrap' }}>{when(a.ts)}</td>
                    <td><b>{a.title}</b><br /><span className="muted">{a.body}</span></td>
                    <td>{a.sent ? <Badge tone="good">shown</Badge> : Date.now() / 1000 - a.ts < 3600 ? <Badge tone="info">waiting for a desktop</Badge> : <Badge>not shown</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </Card>
    </div>
  )
}
