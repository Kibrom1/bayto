import { describe, expect, it } from 'vitest'
import { claudeOnly, modelOptionText, type ModelInfo } from './models'

const base = { tier: 'small', usd_per_turn: 0, default: false }
const claude: ModelInfo = { ...base, id: 'claude-haiku-5-5', label: 'Haiku 5.5', provider: 'anthropic', provider_label: 'Anthropic', ready: true }
const gw = (over: Partial<ModelInfo> = {}): ModelInfo => ({ ...base, id: 'gateway/qwen3', label: 'Qwen3', provider: 'gateway', provider_label: 'Gateway', ready: true, needs: null, ...over })

describe('gateway models in the picker', () => {
  it('are named by their provider like the others', () => {
    expect(modelOptionText(claude)).toBe('Haiku 5.5')
    expect(modelOptionText(gw())).toBe('Qwen3 (Gateway)')
  })
  it('say what to set when the gateway is not ready', () => {
    expect(modelOptionText(gw({ ready: false, needs: 'BAYTO_GATEWAY_URL' }))).toBe('Qwen3 (Gateway) — set BAYTO_GATEWAY_URL first')
  })
  it('are left out of the Build and final write-up pickers', () => {
    expect(claudeOnly([claude, gw()]).map((m) => m.id)).toEqual(['claude-haiku-5-5'])
  })
})
