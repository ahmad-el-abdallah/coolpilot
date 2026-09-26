import { useEffect, useMemo, useRef, useState } from 'react'

export type SearchItem = {
  id: string
  title: string
  hint: string
  keywords: string
  run: () => void
}

/** Ctrl+K / "/" command palette over pages, settings and actions. */
export function SearchPalette({ items, open, onClose }: { items: SearchItem[]; open: boolean; onClose: () => void }) {
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setQ('')
      setSel(0)
      setTimeout(() => input.current?.focus(), 0)
    }
  }, [open])

  const results = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean)
    if (!words.length) return items.slice(0, 12)
    return items
      .map((it) => {
        const hay = `${it.title} ${it.hint} ${it.keywords}`.toLowerCase()
        if (!words.every((w) => hay.includes(w))) return null
        const score = words.reduce((s, w) => s + (it.title.toLowerCase().includes(w) ? 2 : 1), 0)
        return { it, score }
      })
      .filter((r): r is { it: SearchItem; score: number } => !!r)
      .sort((a, b) => b.score - a.score)
      .map((r) => r.it)
      .slice(0, 12)
  }, [q, items])

  if (!open) return null
  const choose = (it?: SearchItem) => {
    if (!it) return
    onClose()
    it.run()
  }
  return (
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div className="palette" onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label="Search">
        <input
          ref={input} value={q} placeholder="Search settings, pages, actions…  (e.g. fan, ghz, battery, crash)"
          onChange={(e) => { setQ(e.target.value); setSel(0) }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onClose()
            else if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, results.length - 1)) }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)) }
            else if (e.key === 'Enter') choose(results[sel])
          }}
        />
        <ul>
          {results.map((r, i) => (
            <li key={r.id} className={i === sel ? 'sel' : ''} onMouseEnter={() => setSel(i)} onClick={() => choose(r)}>
              <span>{r.title}</span>
              <small>{r.hint}</small>
            </li>
          ))}
          {!results.length && <li className="empty">Nothing found for “{q}”</li>}
        </ul>
      </div>
    </div>
  )
}
