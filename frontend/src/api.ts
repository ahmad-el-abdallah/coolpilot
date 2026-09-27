export type Setting = {
  key: string
  label: string
  group: 'power' | 'cpu' | 'gpu' | 'battery' | 'display'
  kind: 'choice' | 'int' | 'bool'
  unit: string
  help: string
  keywords: string[]
  available: boolean
  value: string | number | boolean | null
  choices?: string[]
  min?: number
  max?: number
  default?: string | number | boolean
  locked?: boolean
}

export type Sensors = {
  time: number
  cpu: { temp: number | null; mhz: number | null; mhz_max: number | null; usage: number | null; load: number[]; cores: number }
  ram: { temps: (number | null)[]; used_gb: number | null; total_gb: number | null }
  ssd: { temp: number | null }
  fans: (number | null)[]
  battery: { percent: number | null; status: string | null; volts: number | null; watts: number | null; ac: boolean; health: number | null }
  gpu: {
    present: boolean; state: string | null; name?: string; temp?: number | null; watts?: number | null
    mhz?: number | null; usage?: number | null; vram_used?: number | null; vram_total?: number | null
    pstate?: string; driver?: string; error?: string
  }
  profile: string | null
}

export type Fan = { fan: number; label: string; custom: boolean; points: [number, number][] }
export type FansState = {
  available: boolean; fans: Fan[]; presets?: Record<string, [number, number][]>
  mode?: 'default' | 'stability' | 'custom'; controlled_by?: 'default' | 'stability' | 'custom' | 'stability_mode'
  stability_curve?: [number, number][]
}

export type Profile = {
  name: string; builtin: boolean; description?: string; boot: boolean; active: boolean
  settings: Record<string, unknown>; fans?: unknown
}
export type ProfilesState = { profiles: Profile[]; config: { boot_profile?: string | null; active_profile?: string | null; stability_on?: boolean } }

export type DiagStatus = {
  available: boolean; running: boolean; test: string | null; minutes: number | null; started: number | null
  output: string[]; tests: string[]; areas: Record<string, string>
}
export type DiagLog = { name: string; test: string; lines: number; marks: number; first: string | null; last: string | null }
export type Boot = { index: number; boot_id: string; start: number; end: number; ending: 'running' | 'clean' | 'crash' | 'unknown'; minutes: number }

const token =
  document.querySelector<HTMLMetaElement>('meta[name="coolpilot-token"]')?.content ||
  (import.meta.env.VITE_COOLPILOT_TOKEN as string | undefined) ||
  ''

export class ApiError extends Error {}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: { 'X-CoolPilot-Token': token, ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError((data as { error?: string }).error || `${res.status} ${res.statusText}`)
  return data as T
}

export const api = {
  get: <T,>(path: string) => call<T>('GET', path),
  post: <T,>(path: string, body: unknown = {}) => call<T>('POST', path, body),
  del: <T,>(path: string) => call<T>('DELETE', path),
}

export type PcieLink = {
  bdf: string; label: string; name: string; parent: string | null
  speed: number | null; max_speed: number | null; gen: number | null; max_gen: number | null
  width: number | null; max_width: number | null; lanes_lost: boolean
  correctable: number; nonfatal: number; fatal: number; breakdown: Record<string, number>
}
export type PcieSample = { t: number; area: string; speed: number | null; width: number | null; total: number; delta: number; root_delta: number }
export type PcieArea = { area: string; seconds: number; errors: number; bursts: number; per_min: number }
export type PcieWatch = {
  running: boolean; load: boolean; started: number | null; ends: number | null; area: string | null
  bdf: string | null; log: string | null; errors_since_start: number; errors_last_min: number
  samples: PcieSample[]; marks: { t: number; area: string }[]; per_area: PcieArea[]; note?: string | null
}
export type PcieState = { available: boolean; links: PcieLink[]; watch: PcieWatch }
export type PcieBoot = Boot & { gpu_pcie_errors: number }

export type StabilityItem = {
  key: string; enabled: boolean; value: string | number | boolean; recommended: string | number | boolean
  customized: boolean; target: string | number | boolean | null; note: string | null; skip: string | null
  status: 'applied' | 'different' | 'off' | 'firmware' | 'unsupported' | null
  restore: string | number | boolean | null; setting: Setting
}
export type StabilityState = {
  on: boolean; boot: boolean; power_source: string; items: StabilityItem[]
  sections: { id: string; keys: string[]; mode: 'default' | 'stability' | 'mixed' | 'unsupported' }[]
  fans: {
    enabled: boolean; preset: string; recommended: { enabled: boolean; preset: string }
    presets: Record<string, [number, number][]>; available: boolean; status: 'applied' | 'different' | null
  }
  enabled_count: number; total_count: number
}
export type StabilityResult = { results: Record<string, string>; state: StabilityState }

export type HistoryPoint = {
  t: number; cpu: number | null; cpu_max: number | null; gpu: number | null; gpu_max: number | null
  usage: number | null; bat_w: number | null; fan: number | null; pcie: number | null
  ac: number | null; stab: number | null; n: number
}
export type HistoryData = {
  range: string; bucket: number; from: number; to: number; oldest: number | null
  points: HistoryPoint[]; crashes: { t: number; boot: string; minutes: number }[]
}
export type CrashSummary = {
  seconds?: number; last_ts?: number; gap_to_end?: number | null
  cpu_temp_last?: number | null; cpu_temp_max?: number | null; cpu_usage_avg?: number | null; cpu_mhz_last?: number | null
  gpu_temp_max?: number | null; gpu_state?: string | null; ssd_temp_max?: number | null; ram_temp_max?: number | null
  bat_w_last?: number | null; ac?: boolean; profile?: string | null; stability?: boolean; pcie_err_delta?: number
}
export type CrashEvent = { boot: string; start: number; end: number; minutes: number; recorded: boolean; summary: CrashSummary }
export type BlackboxSample = {
  ts: number; cpu_temp: number | null; cpu_mhz: number | null; cpu_usage: number | null; load1: number | null
  gpu_temp: number | null; gpu_w: number | null; fan1: number | null; fan2: number | null
  bat_w: number | null; ac: number; pcie_err: number | null; pcie_new: number | null; stability: number
}
export type BlackboxStatus = {
  enabled: boolean; interval: number; running: boolean; error: string | null; db_bytes: number
  last: (BlackboxSample & { profile?: string }) | null; oldest: number | null; samples: number; crashes_kept: number
}

export type FullOnce = {
  available: boolean; active: boolean; since: number | null; until: number | null
  back_to: number | null; percent: number | null; last: { reason: string; at: number } | null
}
