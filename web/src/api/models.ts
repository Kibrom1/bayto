// Mirrors orchestrator/src/orchestrator/api/model_catalog.py (GET /models).
import { ApiError, parseError } from './errors'

export interface ModelInfo {
  id: string
  label: string
  tier: string
  usd_per_turn: number
  default: boolean
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
