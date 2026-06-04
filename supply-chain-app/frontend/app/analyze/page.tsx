'use client'

import { useState } from 'react'
import NodeDetailPanel from '@/components/NodeDetailPanel'
import SearchBar from '@/components/SearchBar'
import SupplyChainGraph from '@/components/SupplyChainGraph'
import ExportButton from '@/components/ExportButton'
import SaveButton from '@/components/SaveButton'
import useSelectedNode from '@/hooks/useSelectedNode'
import { analyzeProduct, type AnalysisResult } from '@/lib/api'

export default function AnalyzePage() {
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [analysisId, setAnalysisId] = useState<string | null>(null)
  const [currentProduct, setCurrentProduct] = useState('')
  const [error, setError] = useState<string | null>(null)
  const { selectedNode, selectNode, clearNode } = useSelectedNode()

  const handleSearch = async (product: string) => {
    setLoading(true)
    setError(null)
    setAnalysisId(null)
    setCurrentProduct(product)
    clearNode()
    try {
      const data = await analyzeProduct(product)
      setResult(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="min-h-screen bg-gray-50 p-6">
      <h1 className="mb-6 text-2xl font-bold">Supply Chain Intelligence</h1>
      <SearchBar onSearch={handleSearch} loading={loading} />

      {error && (
        <div className="mt-4 rounded bg-red-100 px-4 py-3 text-red-700">{error}</div>
      )}

      {result && (
        <>
          <div className="mb-3 flex gap-2 print:hidden">
            <SaveButton
              product={currentProduct}
              treeJson={JSON.stringify(result)}
              onSaved={setAnalysisId}
            />
            {analysisId && (
              <ExportButton analysisId={analysisId} product={currentProduct} />
            )}
          </div>
          <div className="mt-6 flex gap-4">
            <div className="flex-1">
              <SupplyChainGraph data={result} onNodeClick={selectNode} />
            </div>
            <NodeDetailPanel node={selectedNode} onClose={clearNode} />
          </div>
        </>
      )}
    </main>
  )
}
