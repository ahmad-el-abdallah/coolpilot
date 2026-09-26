import { useState } from 'react'
import { api } from '../api'
import { Badge, Button, Card } from '../components/ui'
import { usePoll, useToast } from '../hooks'

type SystemInfo = {
  model: string | null; vendor: string | null; serial: string | null
  bios: { version: string | null; date: string | null }
  cpu: string | null; kernel: string; hostname: string; uptime_min: number
  cpu_driver: string | null; governor: string | null; gpu_state: string | null
  warranty: { start: string | null; end: string; territory: string | null; note: string | null } | null
  features: Record<string, boolean>
}

const CHECKER = 'https://www.asus.com/support/warranty-status-inquiry/'

function WarrantyCard({ s, onSaved }: { s: SystemInfo; onSaved: (v: SystemInfo) => void }) {
  const toast = useToast()
  const w = s.warranty
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({ start: w?.start ?? '', end: w?.end ?? '', territory: w?.territory ?? '', note: w?.note ?? '' })
  const save = async (body: object, msg: string) => {
    try {
      onSaved(await api.post<SystemInfo>('/system/warranty', body))
      setEditing(false)
      toast('ok', msg)
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }
  const daysLeft = w ? Math.ceil((new Date(w.end).getTime() - Date.now()) / 86400000) : 0
  return (
    <Card
      title="Warranty"
      subtitle={<>Copy the details from <a href={CHECKER} target="_blank" rel="noreferrer">ASUS's warranty checker</a> (serial: <span className="mono">{s.serial ?? 'see the sticker under the laptop'}</span>). Stored only on this laptop.</>}
      actions={!editing && <Button kind="ghost" onClick={() => setEditing(true)}>{w ? 'Edit' : 'Add'}</Button>}
    >
      {editing ? (
        <form className="warranty-form" onSubmit={(e) => { e.preventDefault(); save(form, 'Warranty saved') }}>
          <label>Start <input type="date" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} /></label>
          <label>End <input type="date" required value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} /></label>
          <label>Territory <input placeholder="e.g. International" maxLength={120} value={form.territory} onChange={(e) => setForm({ ...form, territory: e.target.value })} /></label>
          <label className="wide">Note <input placeholder="e.g. service center phone / email" maxLength={120} value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} /></label>
          <div className="row">
            <Button type="submit" kind="primary">Save</Button>
            <Button kind="ghost" onClick={() => setEditing(false)}>Cancel</Button>
            <span className="spacer" />
            {w && <Button kind="danger" onClick={() => save({ clear: true }, 'Warranty info removed')}>Remove</Button>}
          </div>
        </form>
      ) : w ? (
        <>
          <div className="row wrap">
            <Badge tone={daysLeft > 0 ? 'good' : 'bad'}>{daysLeft > 0 ? 'Active' : 'Expired'}</Badge>
            {w.territory && <Badge tone="info">{w.territory}</Badge>}
            <span>{w.start ?? '?'} → <b>{w.end}</b> ({daysLeft > 0 ? `${daysLeft} days left` : 'expired'})</span>
          </div>
          {w.note && <p className="muted">{w.note}</p>}
        </>
      ) : (
        <p className="muted">No warranty info yet. Look it up with your serial and press “Add”.</p>
      )}
    </Card>
  )
}

export function System() {
  const { data: s, setData } = usePoll<SystemInfo>('/system', 30000)
  if (!s) return <div className="page"><Card><p className="muted">Loading…</p></Card></div>
  const rows: [string, React.ReactNode][] = [
    ['Model', `${s.vendor ?? ''} ${s.model ?? ''}`],
    ['Serial', <span className="mono">{s.serial}</span>],
    ['BIOS', `${s.bios.version} (${s.bios.date})`],
    ['CPU', s.cpu],
    ['CPU driver / governor', `${s.cpu_driver} / ${s.governor}`],
    ['GPU power state', s.gpu_state],
    ['Kernel', s.kernel],
    ['Hostname', s.hostname],
    ['Uptime', s.uptime_min < 60 ? `${s.uptime_min} min` : `${(s.uptime_min / 60).toFixed(1)} h`],
  ]
  return (
    <div className="page">
      <WarrantyCard s={s} onSaved={setData} />
      <Card title="This laptop">
        <table className="table kv"><tbody>
          {rows.map(([k, v]) => <tr key={k}><th>{k}</th><td>{v ?? '—'}</td></tr>)}
        </tbody></table>
      </Card>
      <Card title="Features">
        <div className="row wrap">
          {Object.entries(s.features).map(([k, on]) => <Badge key={k} tone={on ? 'good' : undefined}>{k.replace(/_/g, ' ')}: {on ? 'yes' : 'no'}</Badge>)}
        </div>
      </Card>
    </div>
  )
}
