import { useCallback, useState } from 'react'
import type { SupplyNode } from '@/lib/api'

export default function useSelectedNode() {
  const [selectedNode, setSelectedNode] = useState<SupplyNode | null>(null)

  const selectNode = useCallback((node: SupplyNode) => setSelectedNode(node), [])
  const clearNode = useCallback(() => setSelectedNode(null), [])

  return { selectedNode, selectNode, clearNode }
}
