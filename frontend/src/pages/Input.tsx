import { useRef, useState } from 'react'
import { api, type HyprInputLua, type KeyboardRgbMode, type KeyboardRgbState, type PointerState } from '../api'
import { ColorPicker } from '../components/ColorPicker'
import { SettingControl } from '../components/SettingControl'
import { Badge, Button, Card, Segmented, Slider } from '../components/ui'
import { usePoll, useToast } from '../hooks'

function downloadText(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

const DESKTOP_LABELS: Record<string, string> = {
  hyprland: 'Hyprland', sway: 'Sway', gnome: 'GNOME', kde: 'KDE Plasma', x11: 'X11',
}

const EFFECT_LABELS: Record<KeyboardRgbMode, string> = {
  static: 'Static', breathing: 'Breathing', color_cycle: 'Color cycle', strobe: 'Strobe',
}

function PointerRow({ id, label, side, onCommit }: {
  id: string; label: string
  side: { available: boolean; value: number | null; min: number; max: number } | undefined
  onCommit: (v: number) => unknown
}) {
  return (
    <div className="setting-row" id={id}>
      <div className="setting-text">
        <label>{label}</label>
        <p className="muted">Pointer sensitivity. Lower is slower.</p>
      </div>
      <div className="setting-control">
        {!side || !side.available
          ? <span className="muted">Not supported on this desktop</span>
          : <Slider value={side.value ?? 0} min={side.min} max={side.max} step={0.05} format={(v) => v.toFixed(2)} onCommit={onCommit} />}
      </div>
    </div>
  )
}

export function Input() {
  const { data: pointer, setData: setPointer, refresh: refreshPointer } = usePoll<PointerState>('/pointer', 15000)
  const { data: inputLua, setData: setInputLua } = usePoll<HyprInputLua>('/pointer/input-lua', 30000)
  const { data: kbd, setData: setKbd, refresh: refreshKbd } = usePoll<KeyboardRgbState>('/keyboard', 15000)
  const toast = useToast()
  const [draft, setDraft] = useState<{ mode: KeyboardRgbMode; rgb: [number, number, number]; speed: number } | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const applyInputLua = async (file: File) => {
    if (!window.confirm('Replace your current input.lua with this file? Your current one is backed up automatically.')) return
    try {
      const text = await file.text()
      const updated = await api.post<HyprInputLua>('/pointer/input-lua', { content: text })
      setInputLua(updated)
      toast('ok', updated.backup ? `Applied - previous file backed up to ${updated.backup}` : 'Applied')
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const setPointerValue = async (kind: 'touchpad' | 'mouse', value: number) => {
    try {
      setPointer(await api.post<PointerState>(`/pointer/${kind}`, { value }))
      return true
    } catch (e) {
      toast('error', (e as Error).message)
      refreshPointer()
      return false
    }
  }

  const applyKeyboard = async (next: { mode: KeyboardRgbMode; rgb: [number, number, number]; speed: number }) => {
    try {
      setKbd(await api.post<KeyboardRgbState>('/keyboard', next))
      setDraft(null)
      toast('ok', 'Keyboard lighting applied')
    } catch (e) {
      toast('error', (e as Error).message)
      refreshKbd()
    }
  }

  const draftState = draft ?? (kbd ? { mode: kbd.mode, rgb: kbd.rgb, speed: kbd.speed } : null)

  return (
    <div className="page">
      <Card
        title="Touchpad & mouse"
        subtitle="Pointer speed isn't firmware - it's set by your desktop session."
        actions={pointer?.desktop ? <Badge tone="info">{DESKTOP_LABELS[pointer.desktop] ?? pointer.desktop}</Badge> : undefined}
      >
        {!pointer ? (
          <p className="muted">Loading…</p>
        ) : !pointer.desktop ? (
          <p className="muted">No desktop session was found to apply this to.</p>
        ) : (
          <>
            <PointerRow id="pointer-touchpad" label="Touchpad speed" side={pointer.touchpad} onCommit={(v) => setPointerValue('touchpad', v)} />
            <PointerRow id="pointer-mouse" label="Mouse speed" side={pointer.mouse} onCommit={(v) => setPointerValue('mouse', v)} />
          </>
        )}
      </Card>

      {pointer?.desktop === 'hyprland' && (
        <Card
          title="Hyprland input.lua" id="hypr-input-lua"
          subtitle="Export yours to share with other Omarchy users, or apply one someone shared with you."
          actions={
            <>
              <input
                ref={fileInput} type="file" accept=".lua" style={{ display: 'none' }}
                onChange={(e) => { const f = e.target.files?.[0]; if (f) applyInputLua(f); e.target.value = '' }}
              />
              <Button onClick={() => fileInput.current?.click()}>Apply a file…</Button>
              {inputLua?.available && <Button onClick={() => downloadText('input.lua', inputLua.content ?? '')}>Download</Button>}
            </>
          }
        >
          {inputLua?.available ? <pre className="console tall">{inputLua.content}</pre> : <p className="muted">No input.lua yet.</p>}
        </Card>
      )}

      <Card title="Keyboard lighting" subtitle="Color, effect and speed for the keyboard backlight." id="keyboard-rgb">
        {!kbd ? (
          <p className="muted">Loading…</p>
        ) : !kbd.available ? (
          <p className="muted">Keyboard RGB isn't exposed by this kernel.</p>
        ) : draftState && (
          <>
            <div className="setting-row">
              <div className="setting-text"><label>Effect</label></div>
              <div className="setting-control">
                <Segmented
                  value={draftState.mode} options={Object.keys(EFFECT_LABELS) as KeyboardRgbMode[]}
                  labels={EFFECT_LABELS} onChange={(mode) => setDraft({ ...draftState, mode })}
                />
              </div>
            </div>
            <div className="setting-row">
              <div className="setting-text">
                <label>Color</label>
                <p className="muted">{draftState.mode === 'color_cycle' ? 'This effect cycles its own colors.' : ''}</p>
              </div>
              <div className="setting-control">
                <ColorPicker
                  value={draftState.rgb} disabled={draftState.mode === 'color_cycle'}
                  onChange={(rgb) => setDraft({ ...draftState, rgb })}
                />
              </div>
            </div>
            <div className="setting-row">
              <div className="setting-text"><label>Effect speed</label></div>
              <div className="setting-control">
                <Slider
                  value={draftState.speed} min={0} max={2} disabled={draftState.mode === 'static'}
                  format={(v) => ['Slow', 'Medium', 'Fast'][v]} onCommit={(speed) => setDraft({ ...draftState, speed })}
                />
              </div>
            </div>
            <SettingControl k="kbd_backlight" />
            <div className="row wrap">
              <span className="spacer" />
              <Button onClick={() => applyKeyboard({ mode: 'static', rgb: [255, 255, 255], speed: 0 })}>Static / off</Button>
              <Button kind="primary" disabled={!draft} onClick={() => applyKeyboard(draftState)}>Apply</Button>
            </div>
          </>
        )}
      </Card>
    </div>
  )
}
