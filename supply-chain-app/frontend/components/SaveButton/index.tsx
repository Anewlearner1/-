'use client'

import { useEffect, useState } from 'react'

type Status = 'idle' | 'saving' | 'saved' | 'error'

interface Props {
  product: string
  treeJson: string
  onSaved?: (id: string) => void
}

export default function SaveButton({ product, treeJson, onSaved }: Props) {
  const [status, setStatus] = useState<Status>('idle')

  useEffect(() => {
    setStatus('idle')
  }, [product, treeJson])

  const handleSave = async () => {
    setStatus('saving')
    try {
      const res = await fetch('/api/analyses/save', {
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
      disabled={status === 'saving' || status === 'saved'}
      className="rounded border px-3 py-1 text-sm disabled:opacity-50"
    >
      {status === 'idle' && 'Save'}
      {status === 'saving' && 'Saving…'}
      {status === 'saved' && 'Saved ✓'}
      {status === 'error' && 'Error — retry'}
    </button>
  )
}
