import { describe, expect, it } from 'vitest'
import { isConversational } from './messageKinds'

describe('isConversational', () => {
  it('shows what people and agents say', () => {
    for (const kind of ['answer', 'note', 'question', 'proposal', 'critique', 'objection', 'agree', 'decision', 'summary', 'decision-request']) {
      expect(isConversational({ kind })).toBe(true)
    }
  })
  it('hides protocol traffic, including extension kinds', () => {
    for (const kind of ['assignment', 'hand-raise', 'resume', 'report', 'x-stop', 'x-hand-raise']) {
      expect(isConversational({ kind })).toBe(false)
    }
  })
  it('keeps a message with no kind rather than dropping it', () => {
    expect(isConversational({})).toBe(true)
  })
})
