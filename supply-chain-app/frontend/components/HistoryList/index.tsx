interface HistoryItem {
  id: string
  product: string
  created_at: string
}

interface Props {
  items: HistoryItem[]
  onDelete: (id: string) => void
}

export default function HistoryList({ items, onDelete }: Props) {
  if (items.length === 0) {
    return (
      <div className="rounded border border-dashed p-8 text-center text-gray-400">
        No analyses yet — go to <a href="/analyze" className="underline">Analyze</a> to get started.
      </div>
    )
  }

  return (
    <ul className="space-y-3">
      {items.map((item) => (
        <li
          key={item.id}
          className="flex items-center justify-between rounded border bg-white px-4 py-3 shadow-sm"
        >
          <div>
            <span className="font-medium">{item.product}</span>
            <span className="ml-3 text-xs text-gray-400">
              {new Date(item.created_at).toLocaleDateString()}
            </span>
          </div>
          <div className="flex gap-2">
            <a
              href={`/analyze?id=${item.id}`}
              className="rounded bg-blue-50 px-3 py-1 text-sm text-blue-600 hover:bg-blue-100"
            >
              View
            </a>
            <button
              onClick={() => onDelete(item.id)}
              aria-label="Delete"
              className="rounded bg-red-50 px-3 py-1 text-sm text-red-500 hover:bg-red-100"
            >
              Delete
            </button>
          </div>
        </li>
      ))}
    </ul>
  )
}
