import { render, screen } from '@testing-library/react'
import SupplyChainGraph from './index'

// React Flow requires ResizeObserver
global.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const mockData = {
  nodes: [
    {
      id: '0',
      label: 'iPhone 15 Pro',
      tier: 0,
      country: 'USA',
      risk_level: 'low' as const,
      replaceability: 'high' as const,
      description: 'Finished device',
      role: 'End Product',
    },
    {
      id: '1',
      label: 'TSMC N3',
      tier: 1,
      country: 'Taiwan',
      risk_level: 'high' as const,
      replaceability: 'low' as const,
      description: '3nm foundry process',
      role: 'Foundry',
    },
  ],
  edges: [{ source: '0', target: '1' }],
}

test('renders a label for each node', () => {
  render(<SupplyChainGraph data={mockData} onNodeClick={jest.fn()} />)
  expect(screen.getByText('iPhone 15 Pro')).toBeInTheDocument()
  expect(screen.getByText('TSMC N3')).toBeInTheDocument()
})

test('renders country code on each node', () => {
  render(<SupplyChainGraph data={mockData} onNodeClick={jest.fn()} />)
  expect(screen.getByText('USA')).toBeInTheDocument()
  expect(screen.getByText('Taiwan')).toBeInTheDocument()
})

test('renders the react-flow wrapper', () => {
  const { container } = render(
    <SupplyChainGraph data={mockData} onNodeClick={jest.fn()} />
  )
  expect(container.querySelector('.react-flow')).toBeInTheDocument()
})
