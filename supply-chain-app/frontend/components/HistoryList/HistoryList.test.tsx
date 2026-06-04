import { fireEvent, render, screen } from '@testing-library/react'
import HistoryList from './index'

const mockItems = [
  { id: 'a1', product: 'iPhone 15 Pro', created_at: '2026-01-01T00:00:00Z' },
  { id: 'a2', product: 'HBM Memory', created_at: '2026-01-02T00:00:00Z' },
]

test('renders all history items', () => {
  render(<HistoryList items={mockItems} onDelete={jest.fn()} />)
  expect(screen.getByText('iPhone 15 Pro')).toBeInTheDocument()
  expect(screen.getByText('HBM Memory')).toBeInTheDocument()
})

test('shows empty state message when no items', () => {
  render(<HistoryList items={[]} onDelete={jest.fn()} />)
  expect(screen.getByText(/no analyses yet/i)).toBeInTheDocument()
})

test('each item has a View link', () => {
  render(<HistoryList items={mockItems} onDelete={jest.fn()} />)
  const links = screen.getAllByRole('link', { name: /view/i })
  expect(links).toHaveLength(2)
  expect(links[0]).toHaveAttribute('href', '/analyze?id=a1')
})

test('calls onDelete with item id when delete clicked', () => {
  const onDelete = jest.fn()
  render(<HistoryList items={mockItems} onDelete={onDelete} />)
  fireEvent.click(screen.getAllByRole('button', { name: /delete/i })[0])
  expect(onDelete).toHaveBeenCalledWith('a1')
})
