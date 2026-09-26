import { useEffect, useState } from 'react'
import { api, type Boot, type DiagLog, type DiagStatus } from '../api'
import { AreaPad } from '../components/AreaPad'
import { Badge, Button, Card } from '../components/ui'
import { usePoll, useToast } from '../hooks'

const TEST_HELP: Record<string, string> = {
  idle: 'No load — does moving alone crash it?',
  cpu: 'All CPU cores at 100%',
  ram: 'Memory stress with verification (also loads CPU)',
  gpu: 'RTX 4060 3D load (glmark2)',
  disk: 'SSD read/write stress',
  all: 'CPU + GPU + SSD together',
}

export function Diagnostics() {
  const { data: st, refresh } = usePoll<DiagStatus>('/diag', 2000)
  const { data: logs, refresh: refreshLogs } = usePoll<{ logs: DiagLog[]; results: string[] }>('/diag/logs', 15000)
  const { data: crashes } = usePoll<{ boots: Boot[] }>('/diag/crashes', 60000)
  const toast = useToast()
  const [test, setTest] = useState('cpu')
  const [minutes, setMinutes] = useState(10)
  const [report, setReport] = useState<string | null>(null)
  const [openLog, setOpenLog] = useState<{ name: string; lines: string[]; kernel: string[] } | null>(null)
  const [lastMark, setLastMark] = useState<string | null>(null)

  // refresh the log list whenever a test finishes
  const running = st?.running
  useEffect(() => { if (running === false) refreshLogs() }, [running, refreshLogs])

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    try { await fn(); if (ok) toast('ok', ok); await refresh() } catch (e) { toast('error', (e as Error).message) }
  }
  const mark = (k: string) => act(async () => { await api.post('/diag/mark', { area: k }); setLastMark(st?.areas[k] ?? k) })

  const crashCount = crashes?.boots.filter((b) => b.ending === 'crash').length ?? 0
  const elapsed = st?.started ? Math.floor((Date.now() / 1000 - st.started) / 60) : 0

  return (
    <div className="page">
      <Card
        title={<>Crash test {st?.running && <Badge tone="warn">running: {st.test} · {elapsed}/{st.minutes} min</Badge>}</>}
        subtitle="Loads one component while logging sensors to disk every 0.5 s. If it freezes, reboot and open “Crash report”."
      >
        {!st?.available && <p className="banner bad">~/crashdiag/crashdiag.sh not found.</p>}
        {!st?.running ? (
          <div className="test-picker">
            {st?.tests.map((t) => (
              <button key={t} type="button" className={`test-tile ${test === t ? 'active' : ''}`} onClick={() => setTest(t)}>
                <b>{t}</b><small>{TEST_HELP[t]}</small>
              </button>
            ))}
            <div className="row">
              <label className="inline">Minutes
                <input type="number" min={1} max={120} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} style={{ width: 70 }} />
              </label>
              <Button kind="primary" disabled={!st?.available} onClick={() => act(() => api.post('/diag/start', { test, minutes }), `Started ${test} test`)}>Start test</Button>
            </div>
          </div>
        ) : (
          <>
            <p className="muted">
              Wait ~2 min, then press on an area of the laptop and <b>click the same area here first</b>.
              After a crash, the last mark in the log tells where you were pressing. {lastMark && <>Last mark: <b>{lastMark}</b></>}
            </p>
            <AreaPad
              areas={st.areas} onMark={mark} active={lastMark}
              extra={<Button kind="danger" onClick={() => act(() => api.post('/diag/stop'), 'Test stopped — survived')}>Stop test</Button>}
            />
          </>
        )}
        {!!st?.output.length && <pre className="console">{st.output.slice(-12).join('\n')}</pre>}
      </Card>

      <Card
        title="Crash report" subtitle="What the machine was doing right before the last test crashed"
        actions={<Button onClick={() => act(async () => setReport((await api.get<{ report: string }>('/diag/report')).report))}>Load report</Button>}
      >
        {report ? <pre className="console tall">{report}</pre> : <p className="muted">Click “Load report” after a crash + reboot.</p>}
      </Card>

      <div className="grid2">
        <Card title="Crash history" subtitle={`${crashCount} of the last ${crashes?.boots.length ?? 0} sessions ended without a shutdown`}>
          <table className="table">
            <thead><tr><th>Started</th><th>Length</th><th>Ended</th></tr></thead>
            <tbody>
              {crashes?.boots.map((b) => (
                <tr key={b.boot_id}>
                  <td>{new Date(b.start * 1000).toLocaleString()}</td>
                  <td>{b.minutes < 60 ? `${b.minutes} min` : `${(b.minutes / 60).toFixed(1)} h`}</td>
                  <td>{b.ending === 'crash' ? <Badge tone="bad">crash / freeze</Badge> : b.ending === 'running' ? <Badge tone="info">now</Badge> : <Badge tone="good">{b.ending}</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card title="Test logs" subtitle={logs?.results.length ? `${logs.results.length} tests survived` : undefined}>
          <table className="table">
            <thead><tr><th>Log</th><th>Test</th><th>Time</th><th>Marks</th><th /></tr></thead>
            <tbody>
              {logs?.logs.map((l) => (
                <tr key={l.name}>
                  <td className="mono">{l.name.slice(0, 15)}</td>
                  <td>{l.test}</td>
                  <td>{l.first}–{l.last}</td>
                  <td>{l.marks || ''}</td>
                  <td><button className="link" onClick={() => act(async () => setOpenLog(await api.get(`/diag/logs/${l.name}`)))}>view</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!!logs?.results.length && (
            <details><summary>Survived tests</summary><pre className="console">{logs.results.join('\n')}</pre></details>
          )}
        </Card>
      </div>

      {openLog && (
        <Card title={openLog.name} actions={<Button kind="ghost" onClick={() => setOpenLog(null)}>Close</Button>}>
          <pre className="console tall">{openLog.lines.slice(-60).join('\n')}</pre>
          {!!openLog.kernel.length && (<><h3>Kernel messages</h3><pre className="console">{openLog.kernel.join('\n')}</pre></>)}
        </Card>
      )}
    </div>
  )
}
