import useStockInfo from '@/hooks/useStockInfo'
import type { SupplyNode } from '@/lib/api'
import StockSection from './StockSection'

const RISK_BADGE: Record<string, string> = {
  low: 'bg-green-100 text-green-800',
  medium: 'bg-yellow-100 text-yellow-800',
  high: 'bg-red-100 text-red-800',
}

interface Props {
  node: SupplyNode | null
  onClose: () => void
}

export default function NodeDetailPanel({ node, onClose }: Props) {
  const { data: stocks, loading: stocksLoading, error: stocksError } = useStockInfo(
    node?.label ?? '',
    node?.description ?? ''
  )

  if (!node) return null

  return (
    <aside className="w-80 rounded border bg-white shadow transition-all">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <h2 className="font-bold text-base">{node.label}</h2>
        <button
          onClick={onClose}
          aria-label="Close"
          className="text-gray-400 hover:text-gray-700"
        >
          ✕
        </button>
      </div>

      <div className="space-y-3 px-4 py-3 text-sm">
        <Row label="Country" value={node.country} />
        <Row label="Role" value={node.role} />

        <div className="flex items-center gap-2">
          <span className="text-gray-500 w-32">Risk Level</span>
          <span className={`rounded px-2 py-0.5 text-xs font-medium ${RISK_BADGE[node.risk_level]}`}>
            {node.risk_level}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-gray-500 w-32">Replaceability</span>
          <span className={`rounded px-2 py-0.5 text-xs font-medium ${RISK_BADGE[node.replaceability]}`}>
            {node.replaceability}
          </span>
        </div>

        <p className="text-gray-700">{node.description}</p>

        <StockSection stocks={stocks} loading={stocksLoading} error={stocksError} />
      </div>
    </aside>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <span className="w-32 text-gray-500">{label}</span>
      <span>{value}</span>
    </div>
  )
}
