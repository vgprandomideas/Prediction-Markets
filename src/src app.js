import React, { useEffect, useMemo, useState } from 'react'
import {
  fetchPolymarketMarkets,
  polymarketToBube,
  pdPnl,
  computeEquity,
  isLiquidation
} from './pdEngine'

function App() {
  const [markets, setMarkets] = useState([])
  const [loadingMarkets, setLoadingMarkets] = useState(false)
  const [marketError, setMarketError] = useState('')
  const [selectedMarketId, setSelectedMarketId] = useState(null)

  const [positions, setPositions] = useState([])

  const [side, setSide] = useState('LONG')
  const [notional, setNotional] = useState(1_000_000)
  const [imPct, setImPct] = useState(10)

  const [adminMarketId, setAdminMarketId] = useState(null)
  const [manualProb, setManualProb] = useState(0.5)
  const [settleOutcome, setSettleOutcome] = useState(1)

  // ---- Load Polymarket markets on mount ----
  useEffect(() => {
    async function load() {
      setLoadingMarkets(true)
      setMarketError('')
      try {
        const raw = await fetchPolymarketMarkets(15)
        const mapped = raw.map(polymarketToBube)
        setMarkets(mapped)
        if (mapped.length > 0) {
          setSelectedMarketId(mapped[0].id)
          setAdminMarketId(mapped[0].id)
          setManualProb(mapped[0].currentP)
        }
      } catch (e) {
        console.error(e)
        setMarketError(
          'Could not load Polymarket markets. Check connection or CORS. You can still test manual mode.'
        )
      } finally {
        setLoadingMarkets(false)
      }
    }
    load()
  }, [])

  const selectedMarket = useMemo(
    () => markets.find(m => m.id === selectedMarketId) || null,
    [markets, selectedMarketId]
  )

  const adminMarket = useMemo(
    () => markets.find(m => m.id === adminMarketId) || null,
    [markets, adminMarketId]
  )

  // ---- recompute positions whenever market probabilities change ----
  useEffect(() => {
    if (!markets.length) return

    setPositions(prev =>
      prev.map(pos => {
        const m = markets.find(mm => mm.id === pos.marketId)
        if (!m) return pos
        if (pos.status !== 'OPEN') return pos

        const pnl = pdPnl(pos.notional, pos.entryP, m.currentP, pos.side)
        const equity = computeEquity(pos.imAmount, pnl)
        const updated = { ...pos, currentP: m.currentP, pnl, equity }
        if (isLiquidation(equity, pos.imAmount)) {
          updated.status = 'LIQUIDATED'
        }
        return updated
      })
    )
  }, [markets])

  function formatPct(p) {
    return `${(p * 100).toFixed(2)}%`
  }

  function handleOpenPosition() {
    if (!selectedMarket) return
    if (notional <= 0) return

    const imAmount = (notional * imPct) / 100
    const entryP = selectedMarket.currentP ?? 0
    const newPos = {
      id: crypto.randomUUID(),
      marketId: selectedMarket.id,
      marketName: selectedMarket.question,
      side,
      notional,
      imPct,
      imAmount,
      entryP,
      currentP: entryP,
      pnl: 0,
      equity: imAmount,
      status: 'OPEN'
    }
    setPositions(prev => [...prev, newPos])
  }

  function handleManualProbApply() {
    if (!adminMarket) return
    const p = Math.min(1, Math.max(0, Number(manualProb)))

    setMarkets(prev =>
      prev.map(m => (m.id === adminMarket.id ? { ...m, currentP: p } : m))
    )
  }

  function handleSettle() {
    if (!adminMarket) return
    const outcome = settleOutcome === 1 ? 1 : 0

    setMarkets(prev =>
      prev.map(m =>
        m.id === adminMarket.id
          ? { ...m, status: 'SETTLED', outcome }
          : m
      )
    )

    // finalize positions
    setPositions(prev =>
      prev.map(pos => {
        if (pos.marketId !== adminMarket.id) return pos
        if (pos.status === 'SETTLED') return pos

        if (pos.status === 'LIQUIDATED') {
          return { ...pos, status: 'SETTLED' }
        }

        const finalPnl = pdPnl(pos.notional, pos.entryP, outcome, pos.side)
        const equity = computeEquity(pos.imAmount, finalPnl)
        return {
          ...pos,
          currentP: outcome,
          pnl: finalPnl,
          equity,
          status: 'SETTLED'
        }
      })
    )
  }

  function handleRefreshFromPolymarket() {
    if (!adminMarket || adminMarket.source !== 'polymarket') return

    // simple re-fetch + replace this market by polyId
    fetchPolymarketMarkets(50)
      .then(raw => {
        const byId = Object.fromEntries(raw.map(m => [m.id, m]))
        const pm = byId[adminMarket.polyId]
        if (!pm) {
          alert('Matching market not found in latest Polymarket response')
          return
        }
        const mapped = polymarketToBube(pm)
        setMarkets(prev =>
          prev.map(m =>
            m.id === adminMarket.id
              ? { ...m, currentP: mapped.currentP, question: mapped.question, description: mapped.description }
              : m
          )
        )
        setManualProb(mapped.currentP)
      })
      .catch(err => {
        console.error(err)
        alert('Error refreshing from Polymarket – see console')
      })
  }

  const openPositions = positions.filter(p => p.status === 'OPEN')
  const liquidatedPositions = positions.filter(p => p.status === 'LIQUIDATED')
  const settledPositions = positions.filter(p => p.status === 'SETTLED')

  return (
    <div className="app-root">
      <header className="app-header">
        <h1>BUBE Protocol – PD Derivatives on Polymarket</h1>
        <p className="subtitle">
          Zero-sum prediction market derivatives using Probability Difference (PD).
        </p>
      </header>

      <main className="app-main">
        <section className="panel markets-panel">
          <h2>Markets (Polymarket)</h2>
          {loadingMarkets && <div className="info">Loading markets…</div>}
          {marketError && <div className="error">{marketError}</div>}
          {!loadingMarkets && !markets.length && !marketError && (
            <div className="info">No markets loaded.</div>
          )}
          <ul className="market-list">
            {markets.map(m => (
              <li
                key={m.id}
                className={
                  'market-item' +
                  (selectedMarketId === m.id ? ' selected' : '')
                }
                onClick={() => setSelectedMarketId(m.id)}
              >
                <div className="market-title">{m.question}</div>
                <div className="market-meta">
                  <span>YES: {formatPct(m.currentP ?? 0)}</span>
                  <span>Status: {m.status}</span>
                </div>
                {m.slug && (
                  <div className="market-link">
                    <a
                      href={`https://polymarket.com/event/${m.slug}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      View on Polymarket ↗
                    </a>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>

        <section className="panel trade-panel">
          <h2>Open PD Position</h2>
          {selectedMarket ? (
            <>
              <div className="selected-market">
                <div className="market-title">{selectedMarket.question}</div>
                <div className="market-meta">
                  Current YES probability:{' '}
                  <strong>{formatPct(selectedMarket.currentP ?? 0)}</strong>
                </div>
              </div>

              <div className="form-row">
                <label>Side</label>
                <div className="side-buttons">
                  <button
                    className={side === 'LONG' ? 'btn primary' : 'btn'}
                    onClick={() => setSide('LONG')}
                  >
                    LONG (YES)
                  </button>
                  <button
                    className={side === 'SHORT' ? 'btn primary' : 'btn'}
                    onClick={() => setSide('SHORT')}
                  >
                    SHORT (NO)
                  </button>
                </div>
              </div>

              <div className="form-row">
                <label>Notional</label>
                <input
                  type="number"
                  value={notional}
                  onChange={e => setNotional(Number(e.target.value))}
                  min={0}
                />
              </div>

              <div className="form-row">
                <label>Initial Margin %</label>
                <input
                  type="number"
                  value={imPct}
                  min={2}
                  max={100}
                  onChange={e => setImPct(Number(e.target.value))}
                />
              </div>

              <button className="btn primary full" onClick={handleOpenPosition}>
                Open Position
              </button>
            </>
          ) : (
            <div className="info">Select a market from the left.</div>
          )}

          <hr />

          <h3>Positions</h3>
          {!positions.length && <div className="info">No positions yet.</div>}

          {positions.length > 0 && (
            <>
              <PositionsTable
                title="Open"
                positions={openPositions}
                formatPct={formatPct}
              />
              {liquidatedPositions.length > 0 && (
                <PositionsTable
                  title="Liquidated"
                  positions={liquidatedPositions}
                  formatPct={formatPct}
                />
              )}
              {settledPositions.length > 0 && (
                <PositionsTable
                  title="Settled"
                  positions={settledPositions}
                  formatPct={formatPct}
                />
              )}
            </>
          )}
        </section>

        <section className="panel admin-panel">
          <h2>Admin / Oracle Simulation</h2>
          <p className="small">
            For demo only. Simulate probability moves and final outcomes.
          </p>

          <div className="form-row">
            <label>Admin Market</label>
            <select
              value={adminMarketId || ''}
              onChange={e => setAdminMarketId(e.target.value || null)}
            >
              <option value="">-- select --</option>
              {markets.map(m => (
                <option key={m.id} value={m.id}>
                  {m.question.slice(0, 60)}…
                </option>
              ))}
            </select>
          </div>

          {adminMarket && (
            <>
              <div className="admin-current">
                <div>
                  <strong>Current P(YES):</strong>{' '}
                  {formatPct(adminMarket.currentP ?? 0)}
                </div>
                <div>
                  <strong>Status:</strong> {adminMarket.status}
                </div>
              </div>

              {adminMarket.source === 'polymarket' && (
                <button
                  className="btn small"
                  onClick={handleRefreshFromPolymarket}
                >
                  Refresh from Polymarket API
                </button>
              )}

              <div className="form-row">
                <label>Manual probability (what-if)</label>
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  value={manualProb}
                  onChange={e => setManualProb(e.target.value)}
                />
                <button className="btn" onClick={handleManualProbApply}>
                  Apply and recompute P&amp;L
                </button>
              </div>

              <div className="form-row">
                <label>Settle Outcome</label>
                <select
                  value={settleOutcome}
                  onChange={e => setSettleOutcome(Number(e.target.value))}
                >
                  <option value={1}>YES (1)</option>
                  <option value={0}>NO (0)</option>
                </select>
                <button className="btn danger" onClick={handleSettle}>
                  Settle Market
                </button>
              </div>
            </>
          )}
        </section>
      </main>

      <footer className="app-footer">
        BUBE Protocol · PD = Notional × ΔProbability · Testnet MVP – no real
        funds
      </footer>
    </div>
  )
}

function PositionsTable({ title, positions, formatPct }) {
  if (!positions.length) return null
  return (
    <div className="positions-block">
      <h4>{title} positions</h4>
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Market</th>
              <th>Side</th>
              <th>Status</th>
              <th>Entry P</th>
              <th>Current P</th>
              <th>Notional</th>
              <th>IM</th>
              <th>P&amp;L</th>
              <th>Equity</th>
            </tr>
          </thead>
          <tbody>
            {positions.map(p => (
              <tr key={p.id}>
                <td>{p.marketName.slice(0, 40)}…</td>
                <td>{p.side}</td>
                <td>{p.status}</td>
                <td>{formatPct(p.entryP)}</td>
                <td>{formatPct(p.currentP ?? 0)}</td>
                <td>{p.notional.toLocaleString()}</td>
                <td>{p.imAmount.toLocaleString()}</td>
                <td
                  className={
                    p.pnl > 0 ? 'green' : p.pnl < 0 ? 'red' : undefined
                  }
                >
                  {Math.round(p.pnl).toLocaleString()}
                </td>
                <td>{Math.round(p.equity).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>      </div>
    </div>
  )
}

export default App
