const POLYMARKET_GAMMA_URL = 'https://gamma-api.polymarket.com/markets'

// ---- PD math ----

export function pdPnl(notional, pEntry, pCurrent, side) {
  const s = side === 'LONG' ? 1 : -1
  const deltaP = pCurrent - pEntry
  return notional * deltaP * s
}

export function computeEquity(imAmount, pnl) {
  return imAmount + pnl
}

export const LIQ_THRESHOLD_BETA = 0.05

export function isLiquidation(equity, imAmount) {
  return equity <= LIQ_THRESHOLD_BETA * imAmount
}

// ---- Polymarket fetchers ----
// Instead of hitting Polymarket directly, call Netlify Function
export async function fetchPolymarketMarkets(maxMarkets = 20) {
  // ⬇ replace direct Polymarket URL with internal function endpoint
  const res = await fetch("/api/markets")

  if (!res.ok) {
    throw new Error(`Polymarket API error: ${res.status}`)
  }

  const data = await res.json()
  const selected = []

  for (const m of data) {
    if (!m.active || m.closed) continue

    let outs
    try {
      outs = JSON.parse(m.outcomes ?? '[]')
    } catch {
      continue
    }
    if (!Array.isArray(outs) || outs.length !== 2) continue

    selected.push(m)
    if (selected.length >= maxMarkets) break
  }

  return selected
}

export function polymarketToBube(m) {
  let pYes = 0
  try {
    const prices = JSON.parse(m.outcomePrices ?? '[]')
    if (Array.isArray(prices) && prices[0] != null) {
      pYes = Number(prices[0])
    }
  } catch {
    pYes = 0
  }

  return {
    id: `poly-${m.id}`,
    source: 'polymarket',
    polyId: m.id,
    slug: m.slug,
    question: m.question,
    description: m.description || '',
    currentP: pYes,
    status: 'OPEN',
    outcome: null
  }
}
