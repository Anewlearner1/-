import type { StockInfo } from '@/hooks/useStockInfo'

interface Props {
  stocks: StockInfo[] | null
  loading: boolean
  error: string | null
}

const fmt = (n: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(n)

const fmtCap = (n: number) => {
  if (n >= 1e12) return `$${(n / 1e12).toFixed(1)}T`
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`
  return `$${(n / 1e6).toFixed(0)}M`
}

export default function StockSection({ stocks, loading, error }: Props) {
  if (loading) return <p className="text-xs text-gray-400 mt-3">Loading stocks…</p>
  if (error) return <p className="text-xs text-red-500 mt-3">{error}</p>
  if (!stocks) return null

  return (
    <div className="mt-3 border-t pt-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
        Listed Companies
      </h3>
      <ul className="space-y-2">
        {stocks.map((s) => (
          <li key={s.symbol} className="flex items-center justify-between text-sm">
            <div>
              <a
                href={`https://finance.yahoo.com/quote/${s.symbol}`}
                target="_blank"
                rel="noreferrer"
                className="font-mono font-semibold text-blue-600 hover:underline"
              >
                {s.symbol}
              </a>
              <span className="ml-1 text-xs text-gray-500">{s.exchange}</span>
              {s.is_small_cap && (
                <span className="ml-1 rounded bg-amber-100 px-1 text-xs text-amber-700">
                  ⭐ Small Cap
                </span>
              )}
            </div>
            <div className="text-right">
              <div>{fmt(s.price)}</div>
              <div className="text-xs text-gray-400">{fmtCap(s.market_cap)}</div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
