'use client'

import { useState } from 'react'
import SearchBar from '@/components/SearchBar'
import SupplyChainGraph from '@/components/SupplyChainGraph'
import { analyzeProduct, type AnalysisResult, type SupplyNode } from '@/lib/api'

export default function AnalyzePage() {
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedNode, setSelectedNode] = useState<SupplyNode | null>(null)

  const handleSearch = async (product: string) => {
    setLoading(true)
    setError(null)
    setSelectedNode(null)
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
        <div className="mt-6 flex gap-4">
          <div className="flex-1">
            <SupplyChainGraph data={result} onNodeClick={setSelectedNode} />
          </div>
          {selectedNode && (
            <aside className="w-80 rounded border bg-white p-4 shadow">
              <div className="mb-2 flex items-center justify-between">
                <h2 className="font-bold">{selectedNode.label}</h2>
                <button onClick={() => setSelectedNode(null)} aria-label="Close">✕</button>
              </div>
              <p className="text-sm text-gray-600">{selectedNode.description}</p>
            </aside>
          )}
        </div>
      )}
    </main>
  )
}
