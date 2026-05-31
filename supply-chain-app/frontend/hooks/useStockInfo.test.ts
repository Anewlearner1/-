import { renderHook, waitFor } from '@testing-library/react'
import useStockInfo from './useStockInfo'

const MOCK_STOCKS = [
  { symbol: 'TSM', name: 'Taiwan Semiconductor', price: 150.0, market_cap: 700e9, exchange: 'NYSE', is_small_cap: false },
]

beforeEach(() => {
  jest.clearAllMocks()
  global.fetch = jest.fn()
})

test('starts in loading state', () => {
  ;(fetch as jest.Mock).mockReturnValueOnce(new Promise(() => {}))
  const { result } = renderHook(() => useStockInfo('TSMC N3', 'foundry'))
  expect(result.current.loading).toBe(true)
  expect(result.current.data).toBeNull()
})

test('returns stock data on success', async () => {
  ;(fetch as jest.Mock).mockResolvedValueOnce({
    ok: true,
    json: async () => MOCK_STOCKS,
  })
  const { result } = renderHook(() => useStockInfo('TSMC N3', 'foundry'))
  await waitFor(() => expect(result.current.loading).toBe(false))
  expect(result.current.data).toHaveLength(1)
  expect(result.current.data![0].symbol).toBe('TSM')
  expect(result.current.error).toBeNull()
})

test('sets error when fetch fails', async () => {
  ;(fetch as jest.Mock).mockResolvedValueOnce({ ok: false, status: 502 })
  const { result } = renderHook(() => useStockInfo('TSMC N3', 'foundry'))
  await waitFor(() => expect(result.current.loading).toBe(false))
  expect(result.current.error).not.toBeNull()
  expect(result.current.data).toBeNull()
})

test('sets error on network exception', async () => {
  ;(fetch as jest.Mock).mockRejectedValueOnce(new Error('network error'))
  const { result } = renderHook(() => useStockInfo('TSMC N3', 'foundry'))
  await waitFor(() => expect(result.current.loading).toBe(false))
  expect(result.current.error).toBe('network error')
})

test('does not fetch when label is empty', () => {
  const { result } = renderHook(() => useStockInfo('', ''))
  expect(fetch).not.toHaveBeenCalled()
  expect(result.current.loading).toBe(false)
})
