import { useState } from 'react'
import { api, type ProfilesState } from '../api'
import { Badge, Button, Card, Toggle } from '../components/ui'
import { summarize, usePoll, useSettings, useToast } from '../hooks'

export function Profiles() {
  const { data, setData, refresh } = usePoll<ProfilesState>('/profiles', 10000)
  const { refresh: refreshSettings, byKey } = useSettings()
  const toast = useToast()
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')

  const run = async (fn: () => Promise<unknown>, msg: string) => {
    try {
      const r = await fn()
      const results = (r as { results?: Record<string, string> })?.results
      const { failed, notes } = results ? summarize(results) : { failed: [], notes: [] }
      if (failed.length) toast('error', `Partly applied — ${failed.join('; ')}`)
      else toast('ok', notes.length ? `${msg} (${notes.join('; ')})` : msg)
      await Promise.all([refresh(), refreshSettings()])
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const label = (k: string, v: unknown) => {
    const s = byKey[k]
    if (v === '__default__') return `${s?.label ?? k}: default`
    if (v === '__max__') return `${s?.label ?? k}: highest allowed`
    if (v === '__min__') return `${s?.label ?? k}: lowest allowed`
    if (s?.unit === 'MHz') return `${s.label}: ${(Number(v) / 1000).toFixed(1)} GHz`
    if (typeof v === 'boolean') return `${s?.label ?? k}: ${v ? 'on' : 'off'}`
    return `${s?.label ?? k}: ${v}${s?.unit ? ' ' + s.unit : ''}`
  }

  return (
    <div className="page">
      <Card title="Save current settings as a profile" subtitle="Captures every setting on every page, plus fan curves.">
        <form
          className="row wrap"
          onSubmit={(e) => {
            e.preventDefault()
            run(async () => { setData(await api.post<ProfilesState>('/profiles', { name, description: desc })); setName(''); setDesc('') }, `Saved “${name}”`)
          }}
        >
          <input placeholder="Name (e.g. Uni lectures)" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} required />
          <input placeholder="Description (optional)" value={desc} maxLength={200} onChange={(e) => setDesc(e.target.value)} className="grow" />
          <Button type="submit" kind="primary" disabled={!name.trim()}>Save</Button>
        </form>
      </Card>

      <div className="profile-grid">
        {data?.profiles.map((p) => (
          <Card
            key={p.name}
            className={p.active ? 'active-profile' : ''}
            title={<>{p.name} {p.builtin && <Badge>built-in</Badge>} {p.active && <Badge tone="good">active</Badge>}</>}
            subtitle={p.description}
          >
            <ul className="profile-values">
              {Object.entries(p.settings ?? {}).slice(0, 8).map(([k, v]) => <li key={k}>{label(k, v)}</li>)}
              {p.fans ? <li>Fan curves: {p.fans === 'reset' ? 'factory' : 'saved'}</li> : null}
              {p.fans_preset ? <li>Fan curves: {p.fans_preset} preset</li> : null}
            </ul>
            <div className="row wrap">
              <Button kind="primary" onClick={() => run(() => api.post(`/profiles/${encodeURIComponent(p.name)}/apply`), `${p.name} applied`)}>Apply</Button>
              <label className="inline" title="Re-apply this profile every time the laptop boots">
                Apply at boot
                <Toggle
                  checked={p.boot}
                  onChange={(v) => run(async () => setData(await api.post<ProfilesState>(`/profiles/${encodeURIComponent(p.name)}/boot`, { enabled: v })),
                    v ? `${p.name} will apply at every boot` : 'No profile at boot')}
                />
              </label>
              {!p.builtin && (
                <Button kind="danger" onClick={() => {
                  if (window.confirm(`Delete profile “${p.name}”?`)) run(async () => setData(await api.del<ProfilesState>(`/profiles/${encodeURIComponent(p.name)}`)), 'Deleted')
                }}>Delete</Button>
              )}
            </div>
          </Card>
        ))}
      </div>
    </div>
  )
}
