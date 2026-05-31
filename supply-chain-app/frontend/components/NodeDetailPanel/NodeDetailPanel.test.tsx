import { fireEvent, render, screen } from '@testing-library/react'
import NodeDetailPanel from './index'

const mockNode = {
  id: '1',
  label: 'TSMC N3',
  tier: 1,
  country: 'Taiwan',
  risk_level: 'high' as const,
  replaceability: 'low' as const,
  description: 'TSMC 3nm foundry process used by Apple and NVIDIA',
  role: 'Foundry',
}

test('renders null when no node is selected', () => {
  const { container } = render(<NodeDetailPanel node={null} onClose={jest.fn()} />)
  expect(container.firstChild).toBeNull()
})

test('renders node label as heading', () => {
  render(<NodeDetailPanel node={mockNode} onClose={jest.fn()} />)
  expect(screen.getByRole('heading', { name: 'TSMC N3' })).toBeInTheDocument()
})

test('displays country', () => {
  render(<NodeDetailPanel node={mockNode} onClose={jest.fn()} />)
  expect(screen.getByText('Taiwan')).toBeInTheDocument()
})

test('displays risk_level badge', () => {
  render(<NodeDetailPanel node={mockNode} onClose={jest.fn()} />)
  expect(screen.getByText(/high/i)).toBeInTheDocument()
})

test('displays replaceability', () => {
  render(<NodeDetailPanel node={mockNode} onClose={jest.fn()} />)
  expect(screen.getByText(/low/i)).toBeInTheDocument()
})

test('displays description text', () => {
  render(<NodeDetailPanel node={mockNode} onClose={jest.fn()} />)
  expect(screen.getByText(/3nm foundry/i)).toBeInTheDocument()
})

test('calls onClose when close button is clicked', () => {
  const onClose = jest.fn()
  render(<NodeDetailPanel node={mockNode} onClose={onClose} />)
  fireEvent.click(screen.getByRole('button', { name: /close/i }))
  expect(onClose).toHaveBeenCalledTimes(1)
})
