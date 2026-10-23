// Mirrors orchestrator/src/orchestrator/api/model_catalog.py (GET /models).
import { ApiError, parseError } from './errors'

export interface ModelInfo {
  id: string
  label: string
  tier: string
  usd_per_turn: number
  default: boolean
  provider?: string // 'anthropic' | 'openai' | 'gemini' | 'ollama' | 'gateway'
  provider_label?: string
  ready?: boolean // false when the provider's key / host is not set on the server
  needs?: string | null // what to set when not ready, e.g. OPENAI_API_KEY or BAYTO_GATEWAY_URL
  build_ok?: boolean // may a Build seat use it: Claude always; a gateway model only when the server has BAYTO_ALLOW_GATEWAY_BUILD=1
}

export interface ModelCatalog {
  models: ModelInfo[]
  default: string
}

export async function getModels(): Promise<ModelCatalog> {
  const res = await fetch('/models')
  if (!res.ok) throw new ApiError(res.status, await parseError(res))
  const data = await res.json()
  if (!data || !Array.isArray(data.models)) throw new ApiError(res.status, 'unexpected /models response')
  return data
}

/** How much more or less than the default model a seat costs, from the catalogue's planning figures. 1 when unknown. */
export function costFactor(catalog: ModelCatalog | null, modelId: string | null | undefined): number {
  if (!catalog || !modelId) return 1
  const base = catalog.models.find((m) => m.id === catalog.default)?.usd_per_turn
  const mine = catalog.models.find((m) => m.id === modelId)?.usd_per_turn
  return base && mine !== undefined ? mine / base : 1
}

export const modelLabel = (catalog: ModelCatalog | null, id: string | null | undefined) =>
  id ? catalog?.models.find((m) => m.id === id)?.label ?? id : null

/** Text for a model in a select: other providers are named, and an unconfigured one says what it needs. */
export function modelOptionText(m: ModelInfo): string {
  const from = m.provider && m.provider !== 'anthropic' ? ` (${m.provider_label ?? m.provider})` : ''
  return m.ready === false ? `${m.label}${from} — set ${m.needs ?? 'its key'} first` : `${m.label}${from}`
}

/** Build seats and the final write-up run on Claude only; every other provider, a gateway included, is for discussion seats. */
export const claudeOnly = (models: ModelInfo[]): ModelInfo[] => models.filter((m) => !m.provider || m.provider === 'anthropic')

/** Models a Build seat may use: Claude, plus gateway models only when the server says so (`build_ok`). Never another provider. */
export const buildModels = (models: ModelInfo[]): ModelInfo[] =>
  models.filter((m) => !m.provider || m.provider === 'anthropic' || (m.provider === 'gateway' && m.build_ok === true))
