import { useEffect, useState } from 'react'

export interface StockInfo {
  symbol: string
  name: string
  price: number
  market_cap: number
  exchange: string
  is_small_cap: boolean
}

interface State {
  data: StockInfo[] | null
  loading: boolean
  error: string | null
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export default function useStockInfo(nodeLabel: string, nodeDescription: string): State {
  const [state, setState] = useState<State>({ data: null, loading: false, error: null })

  useEffect(() => {
    if (!nodeLabel.trim()) return

    let cancelled = false
    setState({ data: null, loading: true, error: null })

    fetch(`${API_BASE}/api/v1/stocks/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ node_label: nodeLabel, node_description: nodeDescription }),
    })
      .then(async (res) => {
        if (!res.ok) throw new Error(`Request failed: ${res.status}`)
        return res.json()
      })
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null })
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ data: null, loading: false, error: err.message })
      })

    return () => { cancelled = true }
  }, [nodeLabel, nodeDescription])

  return state
}
