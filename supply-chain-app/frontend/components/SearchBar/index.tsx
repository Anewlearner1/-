'use client'

import { useState } from 'react'

interface Props {
  onSearch: (product: string) => void
  loading: boolean
}

export default function SearchBar({ onSearch, loading }: Props) {
  const [value, setValue] = useState('')

  const handleSubmit = () => {
    const trimmed = value.trim()
    if (!trimmed) return
    onSearch(trimmed)
  }

  return (
    <div className="flex gap-2">
      <input
        type="text"
        className="flex-1 rounded border px-3 py-2"
        placeholder="Enter a product (e.g. HBM memory, iPhone 15 Pro)"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
      />
      <button
        onClick={handleSubmit}
        disabled={loading}
        className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50"
      >
        {loading ? 'Analyzing…' : 'Analyze'}
      </button>
    </div>
  )
}
