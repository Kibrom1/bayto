// WCAG AA check for the room's "Jump to latest" pill, in both themes, straight from the CSS tokens.
// @ts-expect-error node types are not in this project's tsconfig; vitest runs in node
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync('src/index.css', 'utf8')
const lum = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const ratio = (a: string, b: string) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}
const token = (block: string, name: string) => new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`).exec(block)?.[1] as string
const dark = css.slice(css.indexOf('prefers-color-scheme: dark'))
const light = css.slice(0, css.indexOf('prefers-color-scheme: dark'))

describe('Jump to latest pill contrast', () => {
  it.each([['light', light], ['dark', dark]])('meets AA (4.5:1) text contrast in %s', (_n, block) => {
    expect(ratio(token(block, '--jump-bg'), token(block, '--jump-ink'))).toBeGreaterThanOrEqual(4.5)
  })
  it('stands out from the page background (3:1) in both themes', () => {
    expect(ratio(token(light, '--jump-bg'), token(light, '--bg'))).toBeGreaterThanOrEqual(3)
    expect(ratio(token(dark, '--jump-bg'), token(dark, '--bg'))).toBeGreaterThanOrEqual(3)
  })
})
