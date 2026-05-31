'use client'

import { useState } from 'react'

type Status = 'idle' | 'saving' | 'saved' | 'error'

interface Props {
  product: string
  treeJson: string
  onSaved?: (id: string) => void
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export default function SaveButton({ product, treeJson, onSaved }: Props) {
  const [status, setStatus] = useState<Status>('idle')

  const handleSave = async () => {
    setStatus('saving')
    try {
      const res = await fetch(`${API_BASE}/api/v1/analyses/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product, tree_json: treeJson }),
      })
      if (!res.ok) throw new Error('Save failed')
      const data = await res.json()
      setStatus('saved')
      onSaved?.(data.id)
    } catch {
      setStatus('error')
    }
  }

  return (
    <button
      onClick={handleSave}
      disabled={status === 'saving'}
      className="rounded border px-3 py-1 text-sm disabled:opacity-50"
    >
      {status === 'idle' && 'Save'}
      {status === 'saving' && 'Saving…'}
      {status === 'saved' && 'Saved ✓'}
      {status === 'error' && 'Error — retry'}
    </button>
  )
}
