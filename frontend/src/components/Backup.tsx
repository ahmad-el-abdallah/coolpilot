import { useRef, useState } from 'react'
import { api, type BackupInfo } from '../api'
import { summarize, useSettings, useToast } from '../hooks'
import { Modal } from './Modal'
import { Button, Card, Toggle } from './ui'

const PARTS = [
  { id: 'profiles', label: 'Saved profiles' },
  { id: 'stability', label: 'Stability mode setup' },
  { id: 'preferences', label: 'Preferences', hint: 'Stability on/off, boot profile, fan mode, alerts, black box, warranty and repair-report notes' },
] as const

/** Export all settings to a file, or restore them from one. */
export function Backup({ onRestored }: { onRestored?: () => void }) {
  const toast = useToast()
  const { refresh } = useSettings()
  const file = useRef<HTMLInputElement>(null)
  const [pending, setPending] = useState<{ data: unknown; info: BackupInfo } | null>(null)
  const [parts, setParts] = useState<string[]>(PARTS.map((p) => p.id))
  const [busy, setBusy] = useState(false)

  const exportFile = async () => {
    try {
      const data = await api.get<object>('/backup')
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + '\n'], { type: 'application/json' }))
      const a = document.createElement('a')
      a.href = url
      a.download = `coolpilot-settings-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      toast('ok', 'Settings exported')
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const pick = async (f: File | undefined) => {
    if (!f) return
    try {
      const data = JSON.parse(await f.text())
      const info = await api.post<BackupInfo>('/backup/inspect', { data })
      setParts(PARTS.map((p) => p.id))
      setPending({ data, info })
    } catch (e) {
      toast('error', e instanceof SyntaxError ? "That file isn't a CoolPilot settings file" : (e as Error).message)
    } finally {
      if (file.current) file.current.value = ''
    }
  }

  const restore = async () => {
    if (!pending) return
    setBusy(true)
    try {
      const r = await api.post<{ warnings: string[]; results: Record<string, string> }>('/backup/restore', { data: pending.data, parts })
      const { failed } = summarize(r.results, { only: [] })
      if (failed.length) toast('error', `Restored, but some settings failed — ${failed.join('; ')}`)
      else toast('ok', r.warnings.length ? `Settings restored (${r.warnings.length} note${r.warnings.length > 1 ? 's' : ''} — see below)` : 'Settings restored')
      if (r.warnings.length) setPending({ ...pending, info: { ...pending.info, warnings: r.warnings } })
      else setPending(null)
      refresh()
      onRestored?.()
    } catch (e) {
      toast('error', (e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const info = pending?.info
  return (
    <Card id="backup" title="Backup & restore" subtitle="Save all your CoolPilot settings to one file — for after a reinstall, on another Linux distro, or on another laptop.">
      <div className="row wrap">
        <Button kind="primary" onClick={exportFile}>Export settings</Button>
        <Button onClick={() => file.current?.click()}>Import from a file…</Button>
        <input ref={file} type="file" accept="application/json,.json" hidden onChange={(e) => pick(e.target.files?.[0])} />
      </div>
      <p className="muted" style={{ marginTop: 10, fontSize: 12 }}>
        The file includes your warranty details and repair-report notes, so keep it private. Values are fitted to what the firmware allows when they're applied, so a file from another laptop is safe to import.
      </p>
      {info && (
        <Modal
          title="Restore settings from this file?"
          onClose={() => setPending(null)}
          actions={<>
            <Button onClick={() => setPending(null)}>Cancel</Button>
            <Button kind="primary" onClick={restore} disabled={busy || parts.length === 0}>{busy ? 'Restoring…' : 'Restore and apply'}</Button>
          </>}
        >
            <p className="muted">
              Exported {info.exported ? new Date(info.exported * 1000).toLocaleString() : 'at an unknown time'}
              {info.device ? ` on ${info.device}` : ''}.
              {info.device && info.device !== info.this_device && <> This laptop is <b>{info.this_device}</b> — values will be fitted to its limits.</>}
            </p>
            {PARTS.map((p) => {
              const detail = p.id === 'profiles' ? (info.profiles.length ? info.profiles.join(', ') : 'none in the file')
                : p.id === 'stability' ? (info.stability_items ? `${info.stability_items} changed item${info.stability_items > 1 ? 's' : ''}` : 'recommended values')
                  : `${p.hint}${info.stability_on !== undefined ? ` · Stability mode ${info.stability_on ? 'on' : 'off'}` : ''}`
              return (
                <div className="setting-row" key={p.id}>
                  <div className="setting-text"><label>{p.label}</label><p className="muted">{detail}</p></div>
                  <div className="setting-control">
                    <Toggle checked={parts.includes(p.id)} label={p.label}
                      onChange={(v) => setParts((cur) => (v ? [...cur, p.id] : cur.filter((x) => x !== p.id)))} />
                  </div>
                </div>
              )
            })}
            {info.warnings.length > 0 && (
              <div className="banner warn">
                <b>Notes</b>
                <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{info.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
              </div>
            )}
        </Modal>
      )}
    </Card>
  )
}
