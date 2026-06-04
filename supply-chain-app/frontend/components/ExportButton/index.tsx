'use client'

import { useState } from 'react'

type Status = 'idle' | 'exporting' | 'error'

interface Props {
  analysisId: string
  product: string
}

export default function ExportButton({ analysisId, product }: Props) {
  const [status, setStatus] = useState<Status>('idle')

  const handleExport = async () => {
    setStatus('exporting')
    try {
      const res = await fetch(`/api/export?id=${analysisId}`)
      if (!res.ok) throw new Error(`Export failed: ${res.status}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${product.replace(/\s+/g, '-')}-supply-chain.pdf`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      setStatus('idle')
    } catch {
      setStatus('error')
    }
  }

  return (
    <button
      onClick={handleExport}
      disabled={status === 'exporting'}
      className="rounded border px-3 py-1 text-sm disabled:opacity-50"
    >
      {status === 'idle' && 'Export PDF'}
      {status === 'exporting' && 'Exporting…'}
      {status === 'error' && 'Export Failed — retry'}
    </button>
  )
}
