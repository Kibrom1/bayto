// Each seat keeps one hue everywhere (avatar, table, transcript). A small curated palette
// beats an arbitrary hash hue: the colors stay pleasant and easy to tell apart.
const HUES = [232, 172, 348, 28, 125, 276]

export function agentHue(key: string): number {
  let h = 0
  for (const c of key) h = (h * 31 + c.charCodeAt(0)) >>> 0
  return HUES[h % HUES.length]
}

export function initials(name: string): string {
  const words = name.trim().split(/[\s_-]+/).filter(Boolean).map((w) => Array.from(w))
  // One word: its first two letters, so "Customer" and "CFO" do not both read "C".
  const letters = words.length === 1 ? words[0].slice(0, 2) : words.map((w) => w[0]).slice(0, 2)
  return letters.join('').toUpperCase()
}
