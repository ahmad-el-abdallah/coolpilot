import { useState } from 'react'
import { api, type CrashEvent, type ProfilesState } from '../api'
import { Modal } from '../components/Modal'
import { Badge, Button, Card, Sparkline, Stat } from '../components/ui'
import { fmt, summarize, tempTone, usePoll, useSensorHistory, useSettings, useToast } from '../hooks'

export function Dashboard({ go }: { go: (page: string, anchor?: string) => void }) {
  const { now, history, error } = useSensorHistory()
  const { data: prof, refresh: refreshProf } = usePoll<ProfilesState>('/profiles', 5000)
  const { data: sys } = usePoll<{ conflicts: { unit: string; what: string }[] }>('/system', 60000)
  const { data: cr, refresh: refreshCrashes } = usePoll<{ crashes: CrashEvent[]; seen: string | null }>('/blackbox/crashes', 120000)
  const lastCrash = cr?.crashes[0]
  const newCrash = lastCrash && lastCrash.boot !== cr?.seen && Date.now() / 1000 - lastCrash.end < 7 * 86400 ? lastCrash : null
  const { refresh } = useSettings()
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const [deleteProfiles, setDeleteProfiles] = useState(false)

  const factoryReset = async () => {
    setBusy(true)
    try {
      const r = await api.post<{ results: Record<string, string> }>('/reset', { delete_profiles: deleteProfiles })
      const { failed } = summarize(r.results)
      if (failed.length) toast('error', `Reset, but some settings failed: ${failed.join('; ')}`)
      else toast('ok', 'Everything is back to factory default')
      setResetOpen(false)
      setDeleteProfiles(false)
      await Promise.all([refresh(), refreshProf()])
    } catch (e) {
      toast('error', (e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const stability = !!prof?.config.stability_on

  const toggleStability = async () => {
    setBusy(true)
    try {
      const r = await api.post<{ results: Record<string, string> }>('/stability', { enabled: !stability })
      const { failed, notes } = summarize(r.results)
      const done = stability ? 'Stability mode off — previous settings restored' : 'Stability mode on'
      if (failed.length) toast('error', `Some settings failed: ${failed.join('; ')}`)
      else toast('ok', notes.length ? `${done} (${notes.join('; ')})` : done)
      await Promise.all([refresh(), refreshProf()])
    } catch (e) {
      toast('error', (e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const col = <K,>(f: (s: NonNullable<typeof now>) => K) => history.map(f)
  const s = now
  return (
    <div className="page">
      {error && <div className="banner bad">Backend unreachable: {error}</div>}
      {newCrash && (
        <div className="banner crash-banner row wrap">
          <span className="crash-icon" aria-hidden>⚠</span>
          <span>
            <b>The laptop froze or restarted</b> on {new Date(newCrash.end * 1000).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}
            {' '}after {newCrash.minutes < 60 ? `${Math.round(newCrash.minutes)} min` : `${(newCrash.minutes / 60).toFixed(1)} h`}.
            {newCrash.recorded ? ' The black box recorded the last 2 minutes.' : ''}
          </span>
          <span className="spacer" />
          <Button kind="primary" onClick={() => go('history', `crash-${newCrash.boot}`)}>See what happened</Button>
          <Button kind="ghost" onClick={async () => { await api.post('/blackbox/seen', { boot: newCrash.boot }).catch(() => {}); refreshCrashes() }}>Dismiss</Button>
        </div>
      )}
      {!!sys?.conflicts.length && (
        <div className="banner warn">
          <b>Another tool is managing the same settings</b> and may undo changes made here:
          <ul className="plain-list">
            {sys.conflicts.map((c) => <li key={c.unit}><span className="mono">{c.unit}</span> — {c.what}</li>)}
          </ul>
          Consider disabling it (<span className="mono">sudo systemctl disable --now {sys.conflicts.map((c) => c.unit).join(' ')}</span>) or
          only using one tool for these settings.
        </div>
      )}

      <Card className={`hero ${stability ? 'on' : ''}`}>
        <div className="hero-body">
          <div>
            <h2>Stability mode {stability ? <Badge tone="good">ON</Badge> : <Badge>OFF</Badge>}</h2>
            <p className="muted">
              Keeps the laptop cool and steady — less heat and current means less stress on the board,
              which helps laptops that freeze or reset under load.{' '}
              <button className="link" onClick={() => go('stability')}>Choose what it changes →</button>
            </p>
          </div>
          <Button kind={stability ? 'default' : 'primary'} onClick={toggleStability} disabled={busy}>
            {busy ? 'Applying…' : stability ? 'Turn off' : 'Turn on'}
          </Button>
        </div>
      </Card>

      <div className="grid4">
        <Card title="CPU" actions={<button className="link" onClick={() => go('power')}>tune →</button>}>
          <div className="stats">
            <Stat label="Temp" value={fmt.temp(s?.cpu.temp)} tone={tempTone(s?.cpu.temp)} />
            <Stat label="Clock" value={fmt.ghz(s?.cpu.mhz)} sub={`peak ${fmt.ghz(s?.cpu.mhz_max)}`} />
            <Stat label="Usage" value={fmt.pct(s?.cpu.usage)} sub={`load ${s?.cpu.load[0]?.toFixed(2) ?? '—'}`} />
          </div>
          <Sparkline values={col((x) => x.cpu.temp)} max={100} color="var(--orange)" />
        </Card>

        <Card title="GPU" subtitle={s?.gpu.name ?? 'Discrete GPU'} actions={<button className="link" onClick={() => go('gpu')}>tune →</button>}>
          {s?.gpu.state === 'suspended' ? (
            <p className="muted">💤 Sleeping (saving power) — not polled so it stays asleep.</p>
          ) : (
            <>
              <div className="stats">
                <Stat label="Temp" value={fmt.temp(s?.gpu.temp)} tone={tempTone(s?.gpu.temp)} />
                <Stat label="Power" value={fmt.w(s?.gpu.watts)} />
                <Stat label="Usage" value={fmt.pct(s?.gpu.usage)} sub={s?.gpu.pstate} />
              </div>
              <Sparkline values={col((x) => x.gpu.watts ?? 0)} color="var(--green)" />
            </>
          )}
        </Card>

        <Card title="Battery" actions={<button className="link" onClick={() => go('battery')}>tune →</button>}>
          <div className="stats">
            <Stat label="Charge" value={fmt.pct(s?.battery.percent)} sub={s?.battery.ac ? '🔌 on charger' : '🔋 on battery'} />
            <Stat label="Draw" value={fmt.w(s?.battery.watts)} sub={s?.battery.volts ? `${s.battery.volts} V` : undefined} />
            <Stat label="Health" value={fmt.pct(s?.battery.health)} tone={s?.battery.health != null && s.battery.health < 80 ? 'warn' : undefined} />
          </div>
          <Sparkline values={col((x) => x.battery.watts ?? 0)} color="var(--blue)" />
        </Card>

        <Card title="Cooling & memory" actions={<button className="link" onClick={() => go('fans')}>fans →</button>}>
          <div className="stats">
            <Stat label="CPU fan" value={fmt.rpm(s?.fans[0])} />
            <Stat label="GPU fan" value={fmt.rpm(s?.fans[1])} />
            <Stat label="SSD" value={fmt.temp(s?.ssd.temp)} tone={s?.ssd.temp != null && s.ssd.temp > 70 ? 'warn' : undefined} />
          </div>
          <div className="stats">
            <Stat label="RAM" value={s?.ram.used_gb != null ? `${s.ram.used_gb} / ${s.ram.total_gb} GB` : '—'} />
            <Stat label="RAM temps" value={s?.ram.temps.map((t) => fmt.temp(t)).join(' · ') || '—'} />
          </div>
        </Card>
      </div>

      <Card title="Current mode" subtitle="Quick switch — same as Fn+F5">
        <div className="quick-modes">
          {(['Stability', 'Quiet', 'Balanced', 'Performance'] as const).map((name) => (
            <Button
              key={name} kind={(name === 'Stability' ? stability : prof?.config.active_profile === name) ? 'primary' : 'default'}
              onClick={async () => {
                try {
                  await api.post(`/profiles/${encodeURIComponent(name)}/apply`)
                  toast('ok', `${name} applied`)
                  await Promise.all([refresh(), refreshProf()])
                } catch (e) { toast('error', (e as Error).message) }
              }}
            >
              {name}
            </Button>
          ))}
          <span className="muted">Platform profile now: <b>{s?.profile ?? '—'}</b></span>
        </div>
      </Card>

      <Card
        title="Reset everything to default" id="factory-reset"
        subtitle="Put the laptop back exactly how it was on first boot — undo everything this app changed."
        actions={<Button kind="danger" onClick={() => setResetOpen(true)} disabled={busy}>Reset everything…</Button>}
      >
        <p className="muted">Useful if something feels wrong or before handing the laptop to the service center.</p>
      </Card>

      {resetOpen && (
        <Modal
          title="Reset everything to default?"
          onClose={() => !busy && setResetOpen(false)}
          actions={<>
            <span className="spacer" />
            <Button kind="ghost" onClick={() => setResetOpen(false)} disabled={busy}>Cancel</Button>
            <Button kind="danger" onClick={factoryReset} disabled={busy}>{busy ? 'Resetting…' : 'Reset everything'}</Button>
          </>}
        >
          <p>This puts everything back to how it was the first time you booted:</p>
          <ul className="plain-list">
            <li><b>Power & CPU:</b> Balanced mode, CPU boost on, full speed (up to 4.55 GHz), factory power limits</li>
            <li><b>GPU:</b> factory Dynamic Boost and temperature target</li>
            <li><b>Fans:</b> factory automatic curves (Default fan mode)</li>
            <li><b>Battery:</b> charges to 100% again</li>
            <li><b>Stability mode:</b> turned off, its setup back to recommended</li>
            <li><b>Boot:</b> nothing is applied automatically at startup anymore</li>
          </ul>
          <p className="muted">Kept: crash-test logs, black-box recordings and history, screen brightness, keyboard light{deleteProfiles ? '' : ', and your saved profiles'}.</p>
          <label className="inline check">
            <input type="checkbox" checked={deleteProfiles} onChange={(e) => setDeleteProfiles(e.target.checked)} />
            Also delete my saved profiles
          </label>
        </Modal>
      )}
    </div>
  )
}
