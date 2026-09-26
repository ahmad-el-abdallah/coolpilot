import { Card } from '../components/ui'
import { SettingControl } from '../components/SettingControl'
import { fmt, useSensorHistory, useSettings } from '../hooks'

export function Power() {
  const { now } = useSensorHistory(2)
  const { byKey } = useSettings()
  const boostOff = byKey.cpu_boost?.value === false
  const maxMhz = Number(byKey.cpu_max_mhz?.value ?? 0)
  return (
    <div className="page">
      <Card title="Performance mode" subtitle="Sets fan behaviour and power targets in firmware.">
        <SettingControl k="platform_profile" />
      </Card>

      <Card
        title="CPU frequency"
        subtitle={`Now: ${fmt.ghz(now?.cpu.mhz)} average, ${fmt.ghz(now?.cpu.mhz_max)} peak · ${fmt.temp(now?.cpu.temp)}`}
      >
        <SettingControl k="cpu_boost" />
        <SettingControl k="cpu_max_mhz" />
        {boostOff && maxMhz > 3100 && (
          <p className="banner warn">Boost is off, so the CPU can't go above ~3.1 GHz whatever this limit says.</p>
        )}
        <SettingControl k="cpu_min_mhz" />
        <SettingControl k="epp" />
      </Card>

      <Card title="CPU power limits" subtitle="Lower watts = less heat, quieter fans, lower performance. 80 W is the factory value.">
        <SettingControl k="ppt_pl1" />
        <SettingControl k="ppt_pl2" />
        <SettingControl k="ppt_pl3" />
      </Card>
    </div>
  )
}
