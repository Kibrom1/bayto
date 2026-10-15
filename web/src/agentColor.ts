// Each seat keeps one hue everywhere (avatar, table, transcript). A small curated palette
// beats an arbitrary hash hue: the colors stay pleasant and easy to tell apart.
const HUES = [232, 172, 348, 28, 125, 276]

export function agentHue(key: string): number {
  let h = 0
  for (const c of key) h = (h * 31 + c.charCodeAt(0)) >>> 0
  return HUES[h % HUES.length]
}

export function initials(name: string): string {
  return name.split(/[\s_-]+/).map((w) => w[0]?.toUpperCase() ?? '').slice(0, 2).join('')
}
