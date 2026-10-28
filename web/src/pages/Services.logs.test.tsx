import { describe, expect, it } from 'vitest'
import { logTail } from './Services'

describe('logTail', () => {
  it('returns the last meaningful lines and drops telemetry noise', () => {
    const log = [
      '--- starting npm run dev',
      '> vite',
      "Transient error ('Connection aborted.', BadStatusLine('x')) encountered while exporting spans batch, retrying in 1s",
      'sh: vite: command not found',
      'Failed to export spans batch due to timeout, max retries or shutdown.',
      '--- exited with code 127',
      '',
    ].join('\n')
    expect(logTail(log, 3)).toEqual(['> vite', 'sh: vite: command not found', '--- exited with code 127'])
  })
  it('is empty for an empty log', () => {
    expect(logTail('')).toEqual([])
  })
})
