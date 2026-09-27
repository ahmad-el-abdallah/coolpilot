import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type Setting } from './api'
import { SearchPalette, type SearchItem } from './components/SearchPalette'
import { SettingsContext, ToastContext, type Toast } from './hooks'
import { Battery } from './pages/Battery'
import { Dashboard } from './pages/Dashboard'
import { Diagnostics } from './pages/Diagnostics'
import { Fans } from './pages/Fans'
import { Gpu } from './pages/Gpu'
import { History } from './pages/History'
import { Pcie } from './pages/Pcie'
import { Power } from './pages/Power'
import { Profiles } from './pages/Profiles'
import { Report } from './pages/Report'
import { Stability } from './pages/Stability'
import { System } from './pages/System'

// monochrome bolt: the ⚡ character is drawn as a colour emoji by most fonts
const Bolt = () => (
  <svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true">
    <path fill="currentColor" d="M13.5 2 4 13.5h6.5L9.5 22 20 9.5h-6.8L13.5 2z" />
  </svg>
)

const PAGES = [
  { id: 'dashboard', label: 'Dashboard', icon: '◉', keywords: 'home overview sensors temperature live stability' },
  { id: 'stability', label: 'Stability mode', icon: '⛨', keywords: 'stability safe workaround freeze crash protect configure preset cool' },
  { id: 'power', label: 'Power & CPU', icon: <Bolt />, keywords: 'silent quiet mode ghz frequency boost watts tdp' },
  { id: 'fans', label: 'Fans', icon: '✱', keywords: 'fan curve rpm noise cooling' },
  { id: 'gpu', label: 'GPU', icon: '▣', keywords: 'nvidia rtx graphics' },
  { id: 'battery', label: 'Battery & Display', icon: '▭', keywords: 'charge limit brightness keyboard backlight screen' },
  { id: 'profiles', label: 'Profiles', icon: '☰', keywords: 'save preset boot startup' },
  { id: 'history', label: 'History & black box', icon: '◷', keywords: 'history chart graph black box recorder crash timeline temperature over time' },
  { id: 'report', label: 'Repair report', icon: '▤', keywords: 'repair report warranty service center pdf print export evidence' },
  { id: 'diagnostics', label: 'Crash diagnostics', icon: '⚠', keywords: 'crash freeze test stress log report history' },
  { id: 'pcie', label: 'PCIe link health', icon: '⇄', keywords: 'pcie gpu link errors aer badtlp lanes solder balls bga press test watch' },
  { id: 'system', label: 'System & warranty', icon: 'ⓘ', keywords: 'serial bios warranty info model' },
] as const
type PageId = (typeof PAGES)[number]['id']

const GROUP_PAGE: Record<Setting['group'], PageId> = {
  power: 'power', cpu: 'power', gpu: 'gpu', battery: 'battery', display: 'battery',
}

const pageFromHash = (): PageId => {
  const h = location.hash.replace('#/', '').split('?')[0]
  return (PAGES.some((p) => p.id === h) ? h : 'dashboard') as PageId
}

export default function App() {
  const [page, setPage] = useState<PageId>(pageFromHash)
  const [settings, setSettings] = useState<Setting[]>([])
  const [toasts, setToasts] = useState<Toast[]>([])
  const [search, setSearch] = useState(false)
  const [navOpen, setNavOpen] = useState(false)
  const [device, setDevice] = useState('')

  const go = useCallback((p: string, anchor?: string) => {
    location.hash = `#/${p}`
    setPage(p as PageId)
    setNavOpen(false)
    if (anchor) {
      // the target may only appear once the page has loaded its data - keep looking for a few seconds
      let tries = 0
      const find = () => {
        const el = document.getElementById(anchor)
        if (!el) { if (++tries < 25) setTimeout(find, 150); return }
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        el.classList.add('flash')
        setTimeout(() => el.classList.remove('flash'), 1600)
      }
      setTimeout(find, 120)
    } else window.scrollTo({ top: 0 })
  }, [])

  useEffect(() => {
    const onHash = () => setPage(pageFromHash())
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.matches?.('input, textarea, select')
      if ((e.key === 'k' && (e.ctrlKey || e.metaKey)) || (e.key === '/' && !typing)) {
        e.preventDefault()
        setSearch(true)
      }
    }
    addEventListener('hashchange', onHash)
    addEventListener('keydown', onKey)
    return () => { removeEventListener('hashchange', onHash); removeEventListener('keydown', onKey) }
  }, [])

  const toast = useCallback((kind: Toast['kind'], text: string) => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, kind, text }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === 'error' ? 7000 : 3000)
  }, [])

  const refresh = useCallback(async () => {
    try { setSettings(await api.get<Setting[]>('/settings')) } catch (e) { toast('error', (e as Error).message) }
  }, [toast])
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    api.get<{ device: string }>('/system').then((d) => setDevice(d.device)).catch(() => {})
  }, [])

  const set = useCallback(async (key: string, value: unknown) => {
    try {
      const updated = await api.post<Setting>(`/settings/${key}`, { value })
      setSettings((all) => all.map((s) => (s.key === key ? updated : s)))
      toast('ok', `${updated.label} updated`)
      if (key === 'platform_profile' || key === 'cpu_boost') refresh() // firmware changes related values
      return true
    } catch (e) {
      toast('error', (e as Error).message)
      refresh()
      return false
    }
  }, [refresh, toast])

  const ctx = useMemo(() => ({
    settings, byKey: Object.fromEntries(settings.map((s) => [s.key, s])), refresh, set,
  }), [settings, refresh, set])

  const searchItems = useMemo<SearchItem[]>(() => {
    const items: SearchItem[] = PAGES.map((p) => ({
      id: `page-${p.id}`, title: p.label, hint: 'Page', keywords: p.keywords, run: () => go(p.id),
    }))
    for (const s of settings) {
      items.push({
        id: `set-${s.key}`, title: s.label, hint: `Setting · ${PAGES.find((p) => p.id === GROUP_PAGE[s.group])?.label}`,
        keywords: `${s.help} ${s.keywords.join(' ')} ${s.choices?.join(' ') ?? ''}`,
        run: () => go(GROUP_PAGE[s.group], `setting-${s.key}`),
      })
    }
    const action = (id: string, title: string, keywords: string, fn: () => Promise<unknown>, done: string) =>
      items.push({ id, title, hint: 'Action', keywords, run: () => { fn().then(() => { toast('ok', done); refresh() }).catch((e) => toast('error', e.message)) } })
    action('a-stab-on', 'Turn Stability mode on', 'crash freeze workaround safe cool', () => api.post('/stability', { enabled: true }), 'Stability mode on')
    action('a-stab-off', 'Turn Stability mode off', 'restore normal', () => api.post('/stability', { enabled: false }), 'Stability mode off')
    for (const p of ['Quiet', 'Balanced', 'Performance', 'Factory defaults'])
      action(`a-prof-${p}`, `Apply ${p} profile`, `profile preset mode ${p === 'Quiet' ? 'silent' : ''} ${p === 'Performance' ? 'turbo' : ''} reset`,
        () => api.post(`/profiles/${encodeURIComponent(p)}/apply`), `${p} applied`)
    items.push({ id: 'fan-mode', title: 'Fan mode: Default / Stability', hint: 'Fans', keywords: 'fan default factory stability curve noise freeze', run: () => go('fans') })
    items.push({ id: 'factory-reset', title: 'Reset everything to default', hint: 'Dashboard', keywords: 'factory reset default first boot undo restore', run: () => go('dashboard', 'factory-reset') })
    items.push({ id: 'fan-1', title: 'CPU fan curve', hint: 'Fans', keywords: 'fan curve noise rpm', run: () => go('fans', 'fan-1') })
    items.push({ id: 'fan-2', title: 'GPU fan curve', hint: 'Fans', keywords: 'fan curve noise rpm', run: () => go('fans', 'fan-2') })
    action('a-full-once', 'Charge to 100% once', 'battery full trip charge limit 100', () => api.post('/battery/full-once', { enabled: true }), 'Charging to 100% once')
    items.push({ id: 'last-crash', title: 'What happened in the last crash', hint: 'History', keywords: 'black box freeze crash reset recording', run: () => go('history') })
    items.push({ id: 'repair-report', title: 'Make a repair report (PDF)', hint: 'Repair report', keywords: 'asus warranty service center print pdf', run: () => go('report') })
    items.push({ id: 'crash-test', title: 'Run a crash test', hint: 'Diagnostics', keywords: 'stress test freeze cpu ram gpu', run: () => go('diagnostics') })
    items.push({ id: 'pcie-watch', title: 'GPU link press test (PCIe errors)', hint: 'PCIe link health', keywords: 'solder balls bga weak spot press pcie errors', run: () => go('pcie') })
    return items
  }, [settings, go, toast, refresh])

  const current = PAGES.find((p) => p.id === page)!
  return (
    <SettingsContext.Provider value={ctx}>
      <ToastContext.Provider value={toast}>
        <div className={`app ${navOpen ? 'nav-open' : ''}`}>
          <aside className="nav">
            <div className="brand"><span className="logo">C</span><div><b>CoolPilot</b><small title={device}>{device || '…'}</small></div></div>
            <button className="search-btn" onClick={() => setSearch(true)}>⌕ Search <kbd>Ctrl K</kbd></button>
            <nav>
              {PAGES.map((p) => (
                <a key={p.id} href={`#/${p.id}`} className={p.id === page ? 'active' : ''} onClick={(e) => { e.preventDefault(); go(p.id) }}>
                  <span className="icon">{p.icon}</span>{p.label}
                </a>
              ))}
            </nav>
          </aside>
          <main>
            <header className="topbar">
              <button className="hamburger" onClick={() => setNavOpen((o) => !o)} aria-label="Menu">☰</button>
              <h1>{current.label}</h1>
              <button className="search-btn small" onClick={() => setSearch(true)} aria-label="Search">⌕</button>
            </header>
            {page === 'dashboard' && <Dashboard go={go} />}
            {page === 'stability' && <Stability />}
            {page === 'power' && <Power />}
            {page === 'fans' && <Fans />}
            {page === 'gpu' && <Gpu />}
            {page === 'battery' && <Battery />}
            {page === 'profiles' && <Profiles />}
            {page === 'history' && <History />}
            {page === 'report' && <Report />}
            {page === 'diagnostics' && <Diagnostics />}
            {page === 'pcie' && <Pcie />}
            {page === 'system' && <System />}
          </main>
        </div>
        <SearchPalette items={searchItems} open={search} onClose={() => setSearch(false)} />
        <div className="toasts" aria-live="polite">
          {toasts.map((t) => <div key={t.id} className={`toast ${t.kind}`}>{t.text}</div>)}
        </div>
      </ToastContext.Provider>
    </SettingsContext.Provider>
  )
}
