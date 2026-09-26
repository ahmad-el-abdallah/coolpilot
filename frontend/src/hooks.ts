import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api, type Sensors, type Setting } from './api'

/** Poll an endpoint every `ms` while the tab is visible. */
export function usePoll<T>(path: string, ms: number) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const refresh = useCallback(async () => {
    try {
      setData(await api.get<T>(path))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [path])
  useEffect(() => {
    refresh()
    const id = setInterval(() => {
      if (document.visibilityState === 'visible') refresh()
    }, ms)
    return () => clearInterval(id)
  }, [refresh, ms])
  return { data, error, refresh, setData }
}

/** Keep the last `n` sensor snapshots for sparklines. */
export function useSensorHistory(n = 90) {
  const { data, error } = usePoll<Sensors>('/sensors', 1500)
  const hist = useRef<Sensors[]>([])
  const [, force] = useState(0)
  useEffect(() => {
    if (!data) return
    hist.current = [...hist.current.slice(-(n - 1)), data]
    force((x) => x + 1)
  }, [data, n])
  return { now: data, history: hist.current, error }
}

// ------------------------------------------------------------------ settings store
export type SettingsCtx = {
  settings: Setting[]
  byKey: Record<string, Setting>
  refresh: () => Promise<void>
  set: (key: string, value: unknown) => Promise<boolean>
}
export const SettingsContext = createContext<SettingsCtx | null>(null)
export const useSettings = () => useContext(SettingsContext)!

// ------------------------------------------------------------------ toasts
export type Toast = { id: number; kind: 'ok' | 'error'; text: string }
export const ToastContext = createContext<(kind: Toast['kind'], text: string) => void>(() => {})
export const useToast = () => useContext(ToastContext)

// ------------------------------------------------------------------ formatting
export const fmt = {
  temp: (v?: number | null) => (v == null ? '—' : `${Math.round(v)}°C`),
  ghz: (mhz?: number | null) => (mhz == null ? '—' : `${(mhz / 1000).toFixed(2)} GHz`),
  w: (v?: number | null) => (v == null ? '—' : `${v.toFixed(1)} W`),
  pct: (v?: number | null) => (v == null ? '—' : `${Math.round(v)}%`),
  rpm: (v?: number | null) => (v == null ? '—' : `${v} rpm`),
}

export function tempTone(t?: number | null): 'good' | 'warn' | 'bad' | undefined {
  if (t == null) return undefined
  return t >= 90 ? 'bad' : t >= 80 ? 'warn' : 'good'
}

/** Split profile-apply results into real failures and informational notes
 *  ("ok: limited to 65W on battery", "skipped: fixed at 0W by firmware on battery"). */
export function summarize(results: Record<string, string>) {
  const failed: string[] = []
  const notes: string[] = []
  for (const [k, v] of Object.entries(results)) {
    if (v === 'ok' || v === 'not supported') continue
    if (v.startsWith('ok:') || v.startsWith('skipped')) notes.push(`${k} ${v.replace(/^ok: /, '').replace(/^skipped: /, '')}`)
    else failed.push(`${k}: ${v}`)
  }
  return { failed, notes }
}
