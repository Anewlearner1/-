'use client'

import { useEffect, useState } from 'react'
import HistoryList from '@/components/HistoryList'

interface HistoryItem {
  id: string
  product: string
  created_at: string
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export default function HistoryPage() {
  const [items, setItems] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/analyses/history`, {
      headers: { Authorization: `Bearer ${localStorage.getItem('token') ?? ''}` },
    })
      .then((r) => r.json())
      .then(setItems)
      .catch(() => setError('Failed to load history'))
      .finally(() => setLoading(false))
  }, [])

  const handleDelete = async (id: string) => {
    await fetch(`${API_BASE}/api/v1/analyses/${id}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${localStorage.getItem('token') ?? ''}` },
    })
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
