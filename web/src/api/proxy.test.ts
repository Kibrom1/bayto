import { describe, expect, it } from 'vitest'
import viteConfig from '../../vite.config.ts?raw'

const sources = import.meta.glob('./*.ts', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

// The dev server forwards only the paths listed in vite.config.ts. A call to any other path gets a bare
// "Not Found" from Vite (this is how "Check repository" broke), so every path the client calls must be listed.
describe('dev proxy', () => {
  it('forwards every API path the web client calls', () => {
    const used = new Set<string>()
    for (const [file, text] of Object.entries(sources)) {
      if (file.endsWith('.test.ts')) continue
      for (const m of text.matchAll(/fetch\(\s*[`'"]\/([a-z-]+)/g)) used.add(m[1])
    }
    for (const prefix of used) expect(viteConfig, `/${prefix} is not proxied`).toContain(`'/${prefix}'`)
    expect(used.size).toBeGreaterThan(4)
  })
})
