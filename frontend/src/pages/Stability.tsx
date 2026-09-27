import { useState, type ReactNode } from 'react'
import { api, type StabilityItem, type StabilityResult, type StabilityState } from '../api'
import { FanCurveEditor } from '../components/FanCurveEditor'
import { Badge, Button, Card, Segmented, Slider, Stat, Toggle } from '../components/ui'
import { fmt, summarize, tempTone, usePoll, useSensorHistory, useSettings, useToast } from '../hooks'

const MIN = '__min__'

type Info = { title: string; short: string; helps: string; costs: string }
const INFO: Record<string, Info> = {
  platform_profile: {
    title: 'Silent power mode', short: 'Mode',
    helps: 'Firmware lowers its power targets and runs the fans slower — the base of Stability mode.',
    costs: 'Lower sustained speed. Fans are quieter, so the laptop runs warmer under long heavy loads.',
  },
  cpu_boost: {
    title: 'No CPU boost', short: 'Boost',
    helps: 'Stops sudden jumps to ~4.5 GHz — the fastest way the chip heats up and cools down again.',
    costs: 'Single-core work (opening apps, browsing, most everyday tasks) roughly 25–35% slower.',
  },
  cpu_max_mhz: {
    title: 'CPU speed cap', short: 'Max speed',
    helps: 'Hard ceiling on the CPU clock: less heat and less current through the chip.',
    costs: 'The lower the cap, the slower. Below ~2.5 GHz everyday apps start to feel sluggish.',
  },
  epp: {
    title: 'Energy-saving CPU behaviour', short: 'Energy pref.',
    helps: 'The CPU only raises its clock when it really has to.',
    costs: 'A small delay when opening apps or switching tasks.',
  },
  ppt_pl1: {
    title: 'CPU sustained power', short: 'PL1',
    helps: 'Caps long-term CPU watts — the main source of heat during long jobs.',
    costs: 'Long multi-core jobs (compiling, video export, Docker builds) take longer.',
  },
  ppt_pl2: {
    title: 'CPU burst power', short: 'PL2',
    helps: 'Caps the ~2-minute bursts at the start of heavy work.',
    costs: 'Short heavy tasks finish a bit slower.',
  },
  ppt_pl3: {
    title: 'CPU peak power', short: 'PL3',
    helps: 'Caps split-second current spikes through the CPU power connections.',
    costs: 'Barely noticeable.',
  },
  gpu_dynamic_boost: {
    title: 'GPU Dynamic Boost', short: 'Dynamic Boost',
    helps: 'Stops the NVIDIA GPU borrowing extra watts from the CPU budget.',
    costs: 'A few percent lower FPS in games. (The firmware already turns it off on battery.)',
  },
  gpu_temp_target: {
    title: 'GPU temperature target', short: 'Temp target',
    helps: 'The GPU slows itself down earlier to stay cooler.',
    costs: 'Lower FPS during long gaming sessions.',
  },
  charge_limit: {
    title: 'Battery charge limit', short: 'Charge limit',
    helps: 'Less battery wear and less heat while plugged in (see battery health on the Battery page).',
    costs: 'About 20% less runtime off the charger. Switch it off before a long day away from power.',
  },
}
const SECTION_TITLES: Record<string, string> = {
  heat: 'Heat & power', cpu: 'CPU', limits: 'Power limits', gpu: 'GPU', battery: 'Battery',
}
const POWER_DEPENDENT = new Set(['ppt_pl1', 'ppt_pl2', 'ppt_pl3', 'gpu_dynamic_boost', 'gpu_temp_target'])
const PROFILE_LABELS: Record<string, string> = { quiet: '🌙 Silent', balanced: '⚖️ Balanced', performance: '🚀 Turbo' }

type ItemPatch = Record<string, { enabled?: boolean; value?: unknown }>
const PRESETS: { id: string; title: string; text: string; items?: ItemPatch; fans?: { enabled: boolean; preset?: string } }[] = [
  {
    id: 'light', title: 'Light',
    text: 'Only silent mode, no boost and the charge limit. Smallest slowdown.',
    items: {
      platform_profile: { enabled: true, value: 'quiet' }, cpu_boost: { enabled: true, value: false },
      charge_limit: { enabled: true, value: 80 }, cpu_max_mhz: { enabled: false }, epp: { enabled: false },
      ppt_pl1: { enabled: false }, ppt_pl2: { enabled: false }, ppt_pl3: { enabled: false },
      gpu_dynamic_boost: { enabled: false }, gpu_temp_target: { enabled: false },
    },
    fans: { enabled: false },
  },
  { id: 'recommended', title: 'Recommended', text: 'Balanced protection for daily use until the repair.' },
  {
    id: 'max', title: 'Maximum protection',
    text: 'Coolest and slowest: 2.5 GHz cap, 25 W CPU, cooler fan curve.',
    items: {
      platform_profile: { enabled: true, value: 'quiet' }, cpu_boost: { enabled: true, value: false },
      cpu_max_mhz: { enabled: true, value: 2500 }, epp: { enabled: true, value: 'power' },
      ppt_pl1: { enabled: true, value: 25 }, ppt_pl2: { enabled: true, value: 35 }, ppt_pl3: { enabled: true, value: 35 },
      gpu_dynamic_boost: { enabled: true, value: MIN }, gpu_temp_target: { enabled: true, value: MIN },
      charge_limit: { enabled: true, value: 80 },
    },
    fans: { enabled: true, preset: 'cool' },
  },
]

function show(item: StabilityItem, v: unknown): string {
  const s = item.setting
  if (v === MIN) return item.target != null ? `lowest allowed (${show(item, item.target)})` : 'lowest allowed'
  if (typeof v === 'boolean') return v ? 'on' : 'off'
  if (s.unit === 'MHz') return `${(Number(v) / 1000).toFixed(2)} GHz`
  if (typeof v === 'number') return `${v}${s.unit ? ' ' + s.unit : ''}`
  return PROFILE_LABELS[String(v)] ?? String(v).replace(/_/g, ' ')
}

function StatusBadge({ item, on }: { item: StabilityItem; on: boolean }) {
  if (!on || !item.status) return null
  switch (item.status) {
    case 'applied': return <Badge tone="good">✓ applied</Badge>
    case 'different': return <Badge tone="warn">changed since — now {show(item, item.setting.value)}</Badge>
    case 'firmware': return <Badge tone="info">🔒 {item.skip}</Badge>
    case 'off': return <Badge>not managed</Badge>
    default: return null
  }
}

function Editor({ item, disabled, onValue }: { item: StabilityItem; disabled: boolean; onValue: (v: unknown) => void }) {
  const s = item.setting
  if (!s.available) return <span className="muted">Not supported on this laptop</span>
  if (s.kind === 'choice') {
    return <Segmented value={item.value as string} options={s.choices ?? []} disabled={disabled}
      labels={item.key === 'platform_profile' ? PROFILE_LABELS : undefined} onChange={onValue} />
  }
  if (s.kind === 'bool') {
    // for boost the protective value is "off"
    return <Segmented value={item.value ? 'on' : 'off'} options={['off', 'on']} disabled={disabled}
      labels={{ off: 'Off', on: 'On' }} onChange={(v) => onValue(v === 'on')} />
  }
  const lo = s.min ?? 0
  const hi = s.max ?? 100
  const isMin = item.value === MIN
  const format = s.unit === 'MHz' ? (v: number) => `${(v / 1000).toFixed(2)} GHz` : (v: number) => `${v}${s.unit ? ' ' + s.unit : ''}`
  const marks = item.key === 'cpu_max_mhz' ? [2000, 2500, 3000, 3500].filter((m) => m >= lo && m <= hi)
    : item.key === 'charge_limit' ? [60, 80, 100] : undefined
  return (
    <div className="stab-editor">
      {POWER_DEPENDENT.has(item.key) && (
        <label className="inline small">
          <Toggle checked={isMin} disabled={disabled || s.locked} label="Lowest allowed"
            onChange={(v) => onValue(v ? MIN : (typeof item.target === 'number' ? item.target : lo))} />
          Lowest allowed{isMin && item.target != null ? ` (${format(Number(item.target))} now)` : ''}
        </label>
      )}
      {s.locked ? (
        <span className="muted">🔒 Fixed at {format(Number(s.value))} by firmware right now</span>
      ) : !isMin && (
        <Slider value={Math.min(Math.max(Number(item.value), lo), hi)} min={lo} max={hi}
          step={s.unit === 'MHz' ? 100 : 1} format={format} marks={marks} disabled={disabled} onCommit={onValue} />
      )}
    </div>
  )
}

function Row({ item, on, busy, onPatch, onReset }: {
  item: StabilityItem; on: boolean; busy: boolean
  onPatch: (p: { enabled?: boolean; value?: unknown }) => void; onReset: () => void
}) {
  const info = INFO[item.key]
  const off = !item.enabled
  return (
    <div className={`stab-row ${off ? 'excluded' : ''}`} id={`stab-${item.key}`}>
      <div className="stab-switch">
        <Toggle checked={item.enabled} disabled={busy || !item.setting.available} label={`Include ${info.title}`}
          onChange={(v) => onPatch({ enabled: v })} />
      </div>
      <div className="stab-text">
        <div className="stab-title">
          <b>{info.title}</b> <StatusBadge item={item} on={on} />
          {item.note && on && item.enabled && <Badge tone="info">{item.note}</Badge>}
        </div>
        <p><span className="plus">＋</span> {info.helps}</p>
        <p><span className="minus">－</span> {info.costs}</p>
        <div className="stab-meta">
          {item.setting.default !== undefined && <>Factory: <b>{show(item, item.setting.default)}</b>{' · '}</>}
          Recommended: <b>{show(item, item.recommended)}</b>
          {item.customized && <button type="button" className="link" disabled={busy} onClick={onReset}>reset</button>}
          {on && item.restore != null && <span className="muted"> · turning Stability off restores {show(item, item.restore)}</span>}
        </div>
      </div>
      <div className="stab-control">
        <Editor item={item} disabled={busy || off} onValue={(v) => onPatch({ value: v })} />
      </div>
    </div>
  )
}

type Mode = 'default' | 'stability' | 'mixed' | 'unsupported'

/** Two big choices at the top of a section: factory Default or Stability. */
function SectionModes({ mode, on, busy, factory, stable, onPick }: {
  mode: Mode; on: boolean; busy: boolean; factory: string; stable: string
  onPick: (m: 'default' | 'stability') => void
}) {
  const tiles = [
    { id: 'default' as const, icon: '🏭', title: 'Default', text: 'Factory settings, like the first boot', values: factory },
    { id: 'stability' as const, icon: '🛡', title: 'Stability', text: on ? 'Protective settings, active now' : 'Protective settings, applied when Stability mode is on', values: stable },
  ]
  return (
    <div className="section-modes">
      {tiles.map((t) => {
        const active = mode === t.id
        return (
          <button key={t.id} type="button" disabled={busy} className={`section-mode ${active ? 'active' : ''}`}
            onClick={() => !active && onPick(t.id)}>
            <span className="mode-head">
              <span className="mode-icon small">{t.icon}</span><b>{t.title}</b>
              {active && <Badge tone="good">● selected</Badge>}
            </span>
            <small>{t.text}</small>
            <small className="values">{t.values}</small>
          </button>
        )
      })}
      {mode === 'mixed' && <p className="muted small mixed-note">Mixed — some items below are in Stability mode and some aren't.</p>}
    </div>
  )
}

export function Stability() {
  const { data: st, setData, refresh } = usePoll<StabilityState>('/stability', 5000)
  const { now } = useSensorHistory(2)
  const { refresh: refreshSettings } = useSettings()
  const toast = useToast()
  const [busy, setBusy] = useState(false)

  const run = async (fn: () => Promise<StabilityResult | StabilityState>, done: string) => {
    setBusy(true)
    try {
      const r = await fn()
      if ('state' in r) {
        setData(r.state)
        const { failed, notes } = summarize(r.results)
        if (failed.length) toast('error', `Some settings failed: ${failed.join('; ')}`)
        else toast('ok', notes.length ? `${done} (${notes.join('; ')})` : done)
      } else {
        setData(r)
        toast('ok', done)
      }
      refreshSettings()
    } catch (e) {
      toast('error', (e as Error).message)
      refresh()
    } finally {
      setBusy(false)
    }
  }
  const patch = (key: string, p: { enabled?: boolean; value?: unknown }) =>
    run(() => api.post<StabilityResult>('/stability/config', { items: { [key]: p } }),
      st?.on ? 'Saved and applied' : 'Saved — applies when Stability mode is on')

  if (!st) return <div className="page"><Card><p className="muted">Loading…</p></Card></div>

  const differs = st.on && (st.items.some((i) => i.status === 'different') || st.fans.status === 'different')
  const level = st.total_count ? st.enabled_count / st.total_count : 0
  const pickSection = (id: string, title: string, mode: 'default' | 'stability') =>
    run(() => api.post<StabilityResult>('/stability/section', { section: id, mode }),
      mode === 'default' ? `${title}: factory settings restored` : `${title}: ${st.on ? 'Stability settings applied' : 'part of Stability mode'}`)

  const section = (sec: StabilityState['sections'][number]): ReactNode => {
    const items = st.items.filter((i) => sec.keys.includes(i.key))
    const avail = items.filter((i) => i.setting.available)
    if (!avail.length) return null
    const title = SECTION_TITLES[sec.id] ?? sec.id
    const summary = (pick: (i: StabilityItem) => unknown) =>
      avail.map((i) => `${INFO[i.key].short} ${show(i, pick(i))}`).join(' · ')
    return (
      <Card key={sec.id} title={title}>
        <SectionModes
          mode={sec.mode as Mode} on={st.on} busy={busy}
          factory={summary((i) => i.setting.default)} stable={summary((i) => i.value)}
          onPick={(m) => pickSection(sec.id, title, m)}
        />
        {items.map((i) => (
          <Row key={i.key} item={i} on={st.on} busy={busy} onPatch={(p) => patch(i.key, p)}
            onReset={() => run(() => api.post<StabilityResult>('/stability/reset', { key: i.key }), `${INFO[i.key].title} reset`)} />
        ))}
      </Card>
    )
  }

  return (
    <div className="page">
      <Card className={`hero stab-hero ${st.on ? 'on' : ''}`}>
        <div className="hero-body">
          <div>
            <h2>
              🛡 Stability mode {st.on ? <Badge tone="good">ON</Badge> : <Badge>OFF</Badge>}
              <Badge tone="info">{st.power_source}</Badge>
            </h2>
            <p className="muted">
              Less heat and less current means less flexing of the board and its solder joints, so fewer freezes
              until the laptop is repaired. Choose below exactly which protections you want.
            </p>
            <div className="meter" title={`${st.enabled_count} of ${st.total_count} protections included`}>
              <span style={{ width: `${level * 100}%` }} />
            </div>
            <small className="muted">{st.enabled_count} of {st.total_count} protections included</small>
          </div>
          <div className="stab-hero-side">
            <Button kind={st.on ? 'default' : 'primary'} disabled={busy}
              onClick={() => run(() => api.post<StabilityResult>('/stability', { enabled: !st.on }),
                st.on ? 'Stability mode off — previous values restored' : 'Stability mode on')}>
              {busy ? 'Applying…' : st.on ? 'Turn off' : 'Turn on'}
            </Button>
            <label className="inline small" title="Re-apply Stability mode every time the laptop starts">
              <Toggle checked={st.boot} disabled={busy}
                onChange={(v) => run(() => api.post<StabilityState>('/stability/boot', { enabled: v }),
                  v ? 'Stability mode will turn on at every boot' : 'Not applied at boot')} />
              Turn on at every boot
            </label>
          </div>
        </div>
        <div className="stats">
          <Stat label="CPU" value={fmt.temp(now?.cpu.temp)} tone={tempTone(now?.cpu.temp)} sub={`${fmt.ghz(now?.cpu.mhz)} avg`} />
          <Stat label="GPU" value={now?.gpu.state === 'suspended' ? 'asleep' : fmt.temp(now?.gpu.temp)} tone={tempTone(now?.gpu.temp)} />
          <Stat label="Battery draw" value={fmt.w(now?.battery.watts)} sub={now?.battery.ac ? 'on charger' : 'on battery'} />
          <Stat label="Fans" value={now?.fans.map((f) => f ?? '—').join(' / ') ?? '—'} sub="rpm" />
        </div>
        {differs && (
          <p className="banner warn row">
            Some values were changed since Stability mode was applied.
            <span className="spacer" />
            <Button disabled={busy} onClick={() => run(() => api.post<StabilityResult>('/stability', { enabled: true }), 'Re-applied')}>Re-apply</Button>
          </p>
        )}
      </Card>

      <Card title="Quick setups" subtitle="Start from a preset, then fine-tune any item below.">
        <div className="preset-grid">
          {PRESETS.map((p) => (
            <button key={p.id} type="button" className="test-tile" disabled={busy}
              onClick={() => run(
                () => p.items ? api.post<StabilityResult>('/stability/config', { items: p.items, fans: p.fans })
                  : api.post<StabilityResult>('/stability/reset', {}),
                `${p.title} setup ${st.on ? 'applied' : 'saved'}`)}>
              <b>{p.title}</b><small>{p.text}</small>
            </button>
          ))}
        </div>
      </Card>

      {st.sections.map(section)}

      {st.fans.available && (
        <Card
          title={<>Fans {st.on && st.fans.enabled && st.fans.status && (
            st.fans.status === 'applied' ? <Badge tone="good">✓ applied</Badge> : <Badge tone="warn">changed since</Badge>)}</>}
        >
          <SectionModes
            mode={st.fans.enabled ? 'stability' : 'default'} on={st.on} busy={busy}
            factory="Factory automatic curves (also sets the Fans page to Default)"
            stable={`${st.fans.preset} curve — fans start early and ${st.fans.preset === 'stability' ? 'never fully stop' : 'follow this preset'}`}
            onPick={(m) => pickSection('fans', 'Fans', m)}
          />
          <div className={st.fans.enabled ? '' : 'excluded'}>
            <div className="row wrap">
              <span className="muted">Stability curve:</span>
              <Segmented value={st.fans.preset} options={Object.keys(st.fans.presets)} disabled={busy || !st.fans.enabled}
                onChange={(v) => run(() => api.post<StabilityResult>('/stability/config', { fans: { preset: v } }), `Fan curve: ${v}`)} />
              {st.fans.preset !== st.fans.recommended.preset && (
                <button type="button" className="link" onClick={() => run(() => api.post<StabilityResult>('/stability/reset', { key: 'fans' }), 'Fan option reset')}>reset</button>
              )}
            </div>
            <FanCurveEditor points={st.fans.presets[st.fans.preset]} onChange={() => {}} currentTemp={now?.cpu.temp} disabled />
            <p className="muted">
              <span className="plus">＋</span> Cooler, steadier chips under load. <span className="minus">－</span> Louder.
              Fine-tune curves on the Fans page (Stability mode's curve wins while it's on).
            </p>
          </div>
        </Card>
      )}

      <Card title="Good to know">
        <ul className="plain-list">
          <li>Stability mode is a <b>workaround</b>, not a fix — moving or pressing the laptop can still freeze it.</li>
          <li>Changes on this page apply <b>immediately</b> while it's on. Switching a single item off restores that item's value from before Stability mode.</li>
          <li><b>Default</b> on a section puts that section's factory (first-boot) values back right away and leaves it out of Stability mode. <b>Stability</b> puts it back in.</li>
          <li>It re-applies automatically after sleep and when you plug in or unplug the charger (the firmware changes its limits then).</li>
          <li>Need full power for a game or a big job? Turn it off, keep the laptop still on a desk, and turn it back on afterwards.</li>
          <li>
            <Button kind="ghost" disabled={busy} onClick={() => run(() => api.post<StabilityResult>('/stability/reset', {}), 'Everything reset to recommended')}>
              Reset everything to recommended
            </Button>
          </li>
        </ul>
      </Card>
    </div>
  )
}
