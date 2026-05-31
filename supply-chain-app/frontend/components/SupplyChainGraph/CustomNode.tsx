import { Handle, Position } from '@xyflow/react'

const RISK_COLORS: Record<string, string> = {
  low: 'border-green-500',
  medium: 'border-yellow-500',
  high: 'border-red-500',
}

interface NodeData {
  label: string
  country: string
  risk_level: string
  tier: number
  [key: string]: unknown
}

export default function CustomNode({ data }: { data: NodeData }) {
  const borderColor = RISK_COLORS[data.risk_level] ?? 'border-gray-400'

  return (
    <div className={`rounded border-2 bg-white px-3 py-2 shadow ${borderColor}`}>
      <Handle type="target" position={Position.Top} />
      <div className="font-semibold text-sm">{data.label}</div>
      <div className="text-xs text-gray-500">{data.country}</div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
}
