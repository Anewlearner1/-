export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export interface SupplyNode {
  id: string
  label: string
  tier: number
  country: string
  risk_level: 'low' | 'medium' | 'high'
  replaceability: 'low' | 'medium' | 'high'
  description: string
  role: string
}

export interface SupplyEdge {
  source: string
  target: string
}

export interface AnalysisResult {
  nodes: SupplyNode[]
  edges: SupplyEdge[]
}

export async function analyzeProduct(product: string): Promise<AnalysisResult> {
  const res = await fetch(`${API_BASE}/api/v1/analysis/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ product }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `Request failed: ${res.status}`)
  }
  return res.json()
}
