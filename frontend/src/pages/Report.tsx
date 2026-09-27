import { useEffect, useRef, useState } from 'react'
import { api, type CrashEvent, type PcieLink } from '../api'
import { fmtDateTime } from '../components/Charts'
import { Button, Card, Segmented } from '../components/ui'
import { usePoll, useToast } from '../hooks'

type ReportData = {
  generated: number
  window_days: number
  device: { name: string; vendor: string | null; model: string | null; serial: string | null; bios: string; cpu: string | null; gpu: string | null; os: string | null; kernel: string }
  warranty: { start: string | null; end: string; territory: string | null; note: string | null } | null
  symptoms: string
  summary: { sessions: number; crashes: number; excluded: number; first_crash: number | null; last_crash: number | null; recorded_crashes: number; stability_on: boolean; blackbox_since: number | null }
  crashes: CrashEvent[]
  excluded: { boot: string; end: number; minutes: number }[]
  pcie: { gpu_link: PcieLink | null; other_links: { label: string; correctable: number; fatal: number }[]; per_session: { start: number; ending: string; errors: number }[] }
  machine_checks: { checked: boolean; count: number; examples: string[] }
  tests: { test: string; start: number; end: number; seconds: number; marks: number; result: 'crashed' | 'survived' | 'stopped' }[]
}

const date = (t: number) => new Date(t * 1000).toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' })
const dur = (min: number) => (min < 60 ? `${Math.round(min)} min` : `${(min / 60).toFixed(1)} h`)

/** Standalone copy of the report (light, print-friendly) for sending by email. */
function exportHtml(node: HTMLElement, title: string) {
  const clone = node.cloneNode(true) as HTMLElement
  clone.querySelectorAll('.no-print').forEach((n) => n.remove())
  const css = `body{font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#111;background:#fff;max-width:900px;margin:32px auto;padding:0 20px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 8px;border-bottom:1px solid #ddd;padding-bottom:4px}
table{width:100%;border-collapse:collapse;font-size:13px;margin:6px 0}th,td{text-align:left;padding:5px 8px;border-bottom:1px solid #e5e5e5;vertical-align:top}
th{color:#555;font-weight:600}.muted{color:#666}.bad{color:#b42318;font-weight:600}.good{color:#067647;font-weight:600}
.kpis{display:flex;flex-wrap:wrap;gap:10px}.kpi{border:1px solid #ddd;border-radius:8px;padding:8px 12px;min-width:120px}.kpi b{display:block;font-size:20px}
.symptoms{white-space:pre-wrap;border-left:3px solid #999;padding:6px 12px;background:#f7f7f7}.mono{font-family:ui-monospace,monospace;font-size:12px}`
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${title}</title><style>${css}</style></head><body>${clone.innerHTML}</body></html>`
  const url = URL.createObjectURL(new Blob([html], { type: 'text/html' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `${title.replace(/[^\w-]+/g, '-').toLowerCase()}.html`
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

export function Report() {
  const [days, setDays] = useState<'14' | '30' | '90'>('30')
  const { data: r, refresh } = usePoll<ReportData>(`/report?days=${days}`, 120000)
  const toast = useToast()
  const [symptoms, setSymptoms] = useState('')
  const [dirty, setDirty] = useState(false)
  const doc = useRef<HTMLDivElement>(null)
  useEffect(() => { if (r && !dirty) setSymptoms(r.symptoms) }, [r, dirty])

  const exclude = async (boot: string, excluded: boolean) => {
    try {
      await api.post('/report/exclude', { boot, excluded })
      toast('ok', excluded ? 'Left out of the report' : 'Back in the report')
      refresh()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const saveSymptoms = async () => {
    try {
      await api.post('/report/symptoms', { symptoms })
      setDirty(false)
      toast('ok', 'Symptoms saved')
      refresh()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  if (!r) return <div className="page"><Card><p className="muted">Collecting the report…</p></Card></div>
  const s = r.summary
  const g = r.pcie.gpu_link
  const title = `Repair report - ${r.device.name}`
  const errSessions = r.pcie.per_session.filter((b) => b.errors > 0)
  const recorded = r.crashes.filter((c) => c.recorded)
  const coolAtCrash = recorded.length > 0 &&
    recorded.every((c) => (c.summary.cpu_temp_max ?? 0) < 90 && (c.summary.gpu_temp_max ?? 0) < 87)

  return (
    <div className="page">
      <Card className="no-print" title="Repair report" subtitle="Everything a service center needs about the fault, in one document. Review it, add your own words, then print or download it.">
        <div className="row wrap">
          <Segmented value={days} options={['14', '30', '90']} onChange={setDays}
            labels={{ '14': 'Last 14 days', '30': 'Last 30 days', '90': 'Last 90 days' }} />
        </div>
        <div className="row wrap">
          <Button kind="primary" onClick={() => window.print()}>Print / Save as PDF</Button>
          <Button onClick={() => doc.current && exportHtml(doc.current, title)}>Download as HTML</Button>
          <span className="muted small">Includes your serial number — only share it with the repair center.</span>
        </div>
      </Card>

      <div className="report" ref={doc}>
        <h1>{title}</h1>
        <p className="muted">Generated {fmtDateTime(r.generated)} by CoolPilot · covers the last {r.window_days} days</p>

        <h2>Device</h2>
        <table><tbody>
          <tr><th>Model</th><td>{r.device.name}{r.device.model && r.device.model !== r.device.name ? <span className="muted"> ({r.device.model})</span> : null}</td></tr>
          <tr><th>Serial number</th><td className="mono">{r.device.serial ?? '—'}</td></tr>
          <tr><th>BIOS</th><td>{r.device.bios}</td></tr>
          <tr><th>CPU</th><td>{r.device.cpu ?? '—'}</td></tr>
          {r.device.gpu && <tr><th>GPU</th><td>{r.device.gpu}</td></tr>}
          <tr><th>Operating system</th><td>{r.device.os ?? 'Linux'} (kernel {r.device.kernel})</td></tr>
          {r.warranty && (
            <tr><th>Warranty</th><td>{r.warranty.territory ? `${r.warranty.territory}, ` : ''}{r.warranty.start ? `${r.warranty.start} → ` : 'until '}{r.warranty.end}{r.warranty.note ? ` · ${r.warranty.note}` : ''}</td></tr>
          )}
        </tbody></table>

        <h2>Symptoms</h2>
        <div className="no-print">
          <textarea className="symptoms-edit" rows={4} value={symptoms}
            placeholder="Describe the problem in your own words, e.g. “The laptop freezes or restarts by itself under load, especially when it is moved or picked up. It started last month.”"
            onChange={(e) => { setSymptoms(e.target.value); setDirty(true) }} />
          <div className="row"><Button onClick={saveSymptoms} disabled={!dirty}>Save</Button>{dirty && <span className="muted small">unsaved changes</span>}</div>
        </div>
        <p className="symptoms print-only">{symptoms || 'Not described.'}</p>

        <h2>Summary</h2>
        <div className="kpis">
          <div className="kpi"><small className="muted">Sessions</small><b>{s.sessions}</b></div>
          <div className="kpi"><small className="muted">Ended in a crash</small><b className={s.crashes ? 'bad' : 'good'}>{s.crashes}{s.sessions ? ` (${Math.round((100 * s.crashes) / s.sessions)}%)` : ''}</b></div>
          <div className="kpi"><small className="muted">First crash</small><b>{s.first_crash ? date(s.first_crash) : '—'}</b></div>
          <div className="kpi"><small className="muted">Last crash</small><b>{s.last_crash ? date(s.last_crash) : '—'}</b></div>
          <div className="kpi"><small className="muted">CPU hardware errors (MCE)</small><b>{r.machine_checks.checked ? r.machine_checks.count : '?'}</b></div>
          {g && <div className="kpi"><small className="muted">GPU link errors (this boot)</small><b className={g.correctable > 100 ? 'bad' : undefined}>{g.correctable.toLocaleString()}</b></div>}
        </div>
        <p>
          A “crash” is a session that ended without a normal shutdown: the system log stops abruptly (freeze
          or spontaneous restart). {s.stability_on ? 'A low-power, low-temperature “Stability mode” is currently in use as a workaround to reduce the freezes.' : ''}
        </p>

        <h2>Crash timeline</h2>
        {r.crashes.length ? (
          <table>
            <thead><tr><th>When</th><th>After</th><th>CPU peak</th><th>CPU load</th><th>GPU peak</th><th>Power</th><th>New GPU link errors</th><th className="no-print" /></tr></thead>
            <tbody>
              {r.crashes.map((c) => (
                <tr key={c.boot}>
                  <td>{fmtDateTime(c.end)}</td>
                  <td>{dur(c.minutes)}</td>
                  {c.recorded ? (
                    <>
                      <td>{c.summary.cpu_temp_max != null ? `${Math.round(c.summary.cpu_temp_max)}°C` : '—'}</td>
                      <td>{c.summary.cpu_usage_avg != null ? `${Math.round(c.summary.cpu_usage_avg)}%` : '—'}</td>
                      <td>{c.summary.gpu_temp_max != null ? `${Math.round(c.summary.gpu_temp_max)}°C` : '—'}</td>
                      <td>{c.summary.ac ? 'charger' : 'battery'}</td>
                      <td>{c.summary.pcie_err_delta ?? 0}</td>
                    </>
                  ) : <td colSpan={5} className="muted">not recorded (before monitoring started)</td>}
                  <td className="no-print">
                    <button type="button" className="link" title="I turned it off on purpose (held the power button, etc.)"
                      onClick={() => exclude(c.boot, true)}>Not a fault</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="muted">No crashes in this period.</p>}
        {s.excluded > 0 && (
          <p className="muted">
            {s.excluded === 1
              ? '1 other session without a normal shutdown was marked by the owner as an intentional power-off and is not counted.'
              : `${s.excluded} other sessions without a normal shutdown were marked by the owner as intentional power-offs and are not counted.`}
          </p>
        )}
        {r.excluded.length > 0 && (
          <details className="no-print">
            <summary className="muted small">Sessions you left out ({r.excluded.length})</summary>
            <ul className="plain-list">
              {r.excluded.map((x) => (
                <li key={x.boot}>{fmtDateTime(x.end)} · after {dur(x.minutes)} · <button type="button" className="link" onClick={() => exclude(x.boot, false)}>put back</button></li>
              ))}
            </ul>
          </details>
        )}
        {s.recorded_crashes > 0 && (
          <p className="muted">
            For recorded crashes the values are from the last two minutes before the freeze (sampled every 2 s and
            written to disk).{coolAtCrash && ' In every recorded crash the CPU stayed below 90 °C and the GPU below 87 °C, i.e. the machine was not overheating when it stopped.'}
          </p>
        )}

        <h2>CPU ↔ GPU PCIe link</h2>
        {g ? (
          <>
            <p>
              Link: PCIe Gen{g.gen ?? '?'} ×{g.width} (max Gen{g.max_gen ?? '?'} ×{g.max_width}){g.lanes_lost ? <b className="bad"> — lanes lost</b> : ''}.
              Corrected errors since this boot: <b className={g.correctable > 100 ? 'bad' : undefined}>{g.correctable.toLocaleString()}</b>
              {Object.keys(g.breakdown).length ? ` (${Object.entries(g.breakdown).map(([k, v]) => `${k} ${v}`).join(', ')})` : ''};
              non-fatal {g.nonfatal}, fatal {g.fatal}.
            </p>
            <p className="muted">
              Other PCIe links for comparison: {r.pcie.other_links.map((l) => `${l.label} ${l.correctable}`).join(' · ') || 'none'}.
              Corrected errors mean data arrived corrupted and had to be resent — a healthy link shows close to zero.
            </p>
            {errSessions.length > 0 && (
              <table>
                <thead><tr><th>Session start</th><th>GPU link errors logged</th><th>Ended</th></tr></thead>
                <tbody>{errSessions.map((b) => <tr key={b.start}><td>{fmtDateTime(b.start)}</td><td>{b.errors}</td><td>{b.ending}</td></tr>)}</tbody>
              </table>
            )}
          </>
        ) : <p className="muted">No discrete GPU link found.</p>}

        <h2>CPU hardware errors (machine checks)</h2>
        <p>
          {!r.machine_checks.checked ? 'Could not read the kernel log.'
            : r.machine_checks.count === 0 ? `None logged in the last ${r.window_days} days — the CPU did not report any internal hardware error before the freezes.`
              : `${r.machine_checks.count} logged:`}
        </p>
        {r.machine_checks.examples.length > 0 && <pre className="mono">{r.machine_checks.examples.join('\n')}</pre>}

        <h2>Stress tests performed</h2>
        {r.tests.length ? (
          <table>
            <thead><tr><th>When</th><th>Test</th><th>Ran for</th><th>Result</th></tr></thead>
            <tbody>
              {r.tests.map((t) => (
                <tr key={t.start}>
                  <td>{fmtDateTime(t.start)}</td><td>{t.test}</td>
                  <td>{t.seconds < 90 ? `${t.seconds} s` : `${Math.round(t.seconds / 60)} min`}</td>
                  <td className={t.result === 'crashed' ? 'bad' : undefined}>{t.result === 'crashed' ? 'machine crashed' : t.result}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="muted">No stress tests recorded.</p>}

        <p className="muted report-foot">Generated by CoolPilot (github.com/ahmad-el-abdallah/coolpilot) from the laptop's own system log and sensor recordings.</p>
      </div>
    </div>
  )
}
