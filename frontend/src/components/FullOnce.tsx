import { api, type FullOnce as FullOnceState } from '../api'
import { usePoll, useSettings, useToast } from '../hooks'
import { Badge, Button } from './ui'

/** "Charge to 100% once": lift the charge limit for one full charge. */
export function FullOnce({ compact = false }: { compact?: boolean }) {
  const { data: st, setData } = usePoll<FullOnceState>('/battery/full-once', 20000)
  const { refresh } = useSettings()
  const toast = useToast()
  if (!st?.available) return null
  const set = async (enabled: boolean) => {
    try {
      const r = await api.post<FullOnceState>('/battery/full-once', { enabled })
      setData(r)
      toast('ok', enabled ? `Charging to 100% — goes back to ${r.back_to}% when full` : 'Back to the normal charge limit')
      refresh()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }
  if (compact) {
    return st.active
      ? <Button kind="ghost" onClick={() => set(false)} title="Cancel and go back to the normal limit">Charging to 100% · cancel</Button>
      : <Button kind="ghost" onClick={() => set(true)} title="Lift the charge limit for one full charge">Charge to 100% once</Button>
  }
  return (
    <div className="setting-row" id="setting-full-once">
      <div className="setting-text">
        <label>Charge to 100% once {st.active && <Badge tone="info">active</Badge>}</label>
        <p className="muted">
          {st.active
            ? `Charging to 100% now (${st.percent ?? '?'}%). Goes back to ${st.back_to}% automatically when the battery is full, or by ${new Date((st.until ?? 0) * 1000).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })} at the latest.`
            : 'Need the whole battery for a trip? Lift the limit for one full charge — your normal limit comes back automatically when the battery is full.'}
        </p>
      </div>
      <div className="setting-control">
        {st.active
          ? <Button onClick={() => set(false)}>Cancel</Button>
          : <Button kind="primary" onClick={() => set(true)}>Charge to 100% once</Button>}
      </div>
    </div>
  )
}
