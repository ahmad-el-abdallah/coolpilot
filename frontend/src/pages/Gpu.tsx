import { Card, Stat } from '../components/ui'
import { SettingControl } from '../components/SettingControl'
import { fmt, tempTone, useSensorHistory } from '../hooks'

export function Gpu() {
  const { now } = useSensorHistory(2)
  const g = now?.gpu
  return (
    <div className="page">
      <Card title={g?.name ?? 'NVIDIA GPU'} subtitle={g?.driver ? `Driver ${g.driver} · ${g.state}` : g?.state ?? ''}>
        {g?.state === 'suspended' ? (
          <p className="muted">💤 The GPU is asleep. It wakes automatically when a game or app needs it.</p>
        ) : (
          <div className="stats">
            <Stat label="Temp" value={fmt.temp(g?.temp)} tone={tempTone(g?.temp)} />
            <Stat label="Power" value={fmt.w(g?.watts)} />
            <Stat label="Clock" value={g?.mhz != null ? `${g.mhz} MHz` : '—'} />
            <Stat label="Usage" value={fmt.pct(g?.usage)} />
            <Stat label="VRAM" value={g?.vram_used != null ? `${Math.round(g.vram_used)} / ${Math.round(g.vram_total ?? 0)} MB` : '—'} />
          </div>
        )}
      </Card>
      <Card title="GPU power & heat">
        <SettingControl k="gpu_dynamic_boost" />
        <SettingControl k="gpu_temp_target" />
      </Card>
      <Card title="GPU mode (Eco / MUX)">
        <p className="muted">
          Switching the NVIDIA GPU fully off or to discrete-only isn't available on this install (needs
          supergfxctl / asusctl, which aren't installed). The GPU already sleeps on its own when idle.
        </p>
      </Card>
    </div>
  )
}
