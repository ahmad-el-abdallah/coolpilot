import { Card, Stat } from '../components/ui'
import { SettingControl } from '../components/SettingControl'
import { fmt, useSensorHistory } from '../hooks'

export function Battery() {
  const { now } = useSensorHistory(2)
  const b = now?.battery
  return (
    <div className="page">
      <Card title="Battery" subtitle={b?.status ?? ''}>
        <div className="stats">
          <Stat label="Charge" value={fmt.pct(b?.percent)} />
          <Stat label="Power" value={fmt.w(b?.watts)} sub={b?.ac ? '🔌 charger connected' : '🔋 on battery'} />
          <Stat label="Voltage" value={b?.volts ? `${b.volts} V` : '—'} />
          <Stat label="Health" value={fmt.pct(b?.health)} sub="of original capacity" tone={b?.health != null && b.health < 80 ? 'warn' : undefined} />
        </div>
        <SettingControl k="charge_limit" />
      </Card>
      <Card title="Display & keyboard">
        <SettingControl k="screen_brightness" />
        <SettingControl k="panel_overdrive" />
        <SettingControl k="kbd_backlight" />
      </Card>
    </div>
  )
}
