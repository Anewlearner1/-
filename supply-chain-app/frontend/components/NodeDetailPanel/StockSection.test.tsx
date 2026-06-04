import { render, screen } from '@testing-library/react'
import StockSection from './StockSection'

const mockStocks = [
  {
    symbol: 'TSM',
    name: 'Taiwan Semiconductor Manufacturing',
    price: 150.25,
    market_cap: 700_000_000_000,
    exchange: 'NYSE',
    is_small_cap: false,
  },
  {
    symbol: 'ASML',
    name: 'ASML Holding',
    price: 820.0,
    market_cap: 1_500_000_000,
    exchange: 'NASDAQ',
    is_small_cap: true,
  },
]

test('renders nothing when stocks is null', () => {
  const { container } = render(<StockSection stocks={null} loading={false} error={null} />)
  expect(container.firstChild).toBeNull()
})

test('renders loading skeleton when loading', () => {
  render(<StockSection stocks={null} loading={true} error={null} />)
  expect(screen.getByText(/loading/i)).toBeInTheDocument()
})

test('renders error message when error set', () => {
  render(<StockSection stocks={null} loading={false} error="fetch failed" />)
  expect(screen.getByText(/fetch failed/i)).toBeInTheDocument()
})

test('renders each stock symbol', () => {
  render(<StockSection stocks={mockStocks} loading={false} error={null} />)
  expect(screen.getByText('TSM')).toBeInTheDocument()
  expect(screen.getByText('ASML')).toBeInTheDocument()
})

test('renders formatted price', () => {
  render(<StockSection stocks={mockStocks} loading={false} error={null} />)
  expect(screen.getByText(/150\.25/)).toBeInTheDocument()
})

test('renders small cap badge on qualifying stocks', () => {
  render(<StockSection stocks={mockStocks} loading={false} error={null} />)
  expect(screen.getByText(/small cap/i)).toBeInTheDocument()
})

test('renders exchange name', () => {
  render(<StockSection stocks={mockStocks} loading={false} error={null} />)
  expect(screen.getAllByText('NYSE')[0]).toBeInTheDocument()
})
