import { useEffect, type ReactNode } from 'react'

/** In-page dialog (no browser confirm()). Esc or clicking outside closes it. */
export function Modal({ title, children, actions, onClose }: {
  title: ReactNode; children: ReactNode; actions: ReactNode; onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div className="modal" role="dialog" aria-modal="true" onMouseDown={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
        <div className="modal-body">{children}</div>
        <div className="row modal-actions">{actions}</div>
      </div>
    </div>
  )
}
