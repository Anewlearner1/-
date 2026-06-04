'use client'

import { useCallback, useMemo } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  type Node as RFNode,
  type Edge as RFEdge,
  type NodeMouseHandler,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import CustomNode from './CustomNode'

const nodeTypes = { custom: CustomNode }

interface SupplyNode {
  id: string
  label: string
  tier: number
  country: string
  risk_level: 'low' | 'medium' | 'high'
  replaceability: 'low' | 'medium' | 'high'
  description: string
  role: string
}

interface SupplyEdge {
  source: string
  target: string
}

interface Props {
  data: { nodes: SupplyNode[]; edges: SupplyEdge[] }
  onNodeClick: (node: SupplyNode) => void
}

const TIER_Y = 150

export default function SupplyChainGraph({ data, onNodeClick }: Props) {
  const rfNodes: RFNode[] = useMemo(
    () =>
      data.nodes.map((n, i) => ({
        id: n.id,
        type: 'custom',
        position: { x: i * 220, y: n.tier * TIER_Y },
        data: { ...n },
      })),
    [data.nodes]
  )

  const rfEdges: RFEdge[] = useMemo(
    () =>
      data.edges.map((e, i) => ({
        id: `e-${i}`,
        source: e.source,
        target: e.target,
        animated: true,
      })),
    [data.edges]
  )

  const handleNodeClick: NodeMouseHandler = useCallback(
    (_, node) => {
      const original = data.nodes.find((n) => n.id === node.id)
      if (original) onNodeClick(original)
    },
    [data.nodes, onNodeClick]
  )

  return (
    <div style={{ width: '100%', height: '600px' }}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onNodeClick={handleNodeClick}
        fitView
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  )
}
