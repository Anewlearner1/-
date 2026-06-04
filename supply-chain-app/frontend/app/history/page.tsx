'use client'

import { useEffect, useState } from 'react'
import HistoryList from '@/components/HistoryList'

interface HistoryItem {
  id: string
  product: string
  created_at: string
}

export default function HistoryPage() {
  const [items, setItems] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/analyses/history')
      .then((r) => {
        if (!r.ok) throw new Error(`Status ${r.status}`)
        return r.json()
      })
      .then(setItems)
      .catch(() => setError('Failed to load history'))
      .finally(() => setLoading(false))
  }, [])

  const handleDelete = async (id: string) => {
    await fetch(`/api/analyses/${id}`, { method: 'DELETE' })
    setItems((prev) => prev.filter((i) => i.id !== id))
  }

  return (
    <main className="min-h-screen bg-gray-50 p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold">My Analyses</h1>
        <a href="/analyze" className="rounded bg-blue-600 px-4 py-2 text-sm text-white">
          + New Analysis
        </a>
      </div>

      {loading && <p className="text-gray-400">Loading…</p>}
      {error && <p className="text-red-500">{error}</p>}
      {!loading && !error && <HistoryList items={items} onDelete={handleDelete} />}
    </main>
  )
}
