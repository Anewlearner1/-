import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import ExportButton from './index'

beforeEach(() => {
  jest.clearAllMocks()
  global.fetch = jest.fn()
  // Mock URL.createObjectURL / revokeObjectURL
  global.URL.createObjectURL = jest.fn(() => 'blob:mock')
  global.URL.revokeObjectURL = jest.fn()
})

test('shows Export PDF label initially', () => {
  render(<ExportButton analysisId="a1" product="HBM" />)
  expect(screen.getByRole('button', { name: /export pdf/i })).toBeInTheDocument()
})

test('calls /api/export when clicked', async () => {
  ;(fetch as jest.Mock).mockResolvedValueOnce({
    ok: true,
    blob: async () => new Blob(['%PDF'], { type: 'application/pdf' }),
  })
  render(<ExportButton analysisId="a1" product="HBM" />)
  fireEvent.click(screen.getByRole('button', { name: /export pdf/i }))
  await waitFor(() => expect(fetch).toHaveBeenCalled())
  expect((fetch as jest.Mock).mock.calls[0][0]).toContain('/api/export?id=a1')
})

test('shows Exporting… while in flight', async () => {
  ;(fetch as jest.Mock).mockReturnValueOnce(new Promise(() => {}))
  render(<ExportButton analysisId="a1" product="HBM" />)
  fireEvent.click(screen.getByRole('button', { name: /export pdf/i }))
  expect(screen.getByText(/exporting/i)).toBeInTheDocument()
})

test('shows error on fetch failure', async () => {
  ;(fetch as jest.Mock).mockResolvedValueOnce({ ok: false, status: 500 })
  render(<ExportButton analysisId="a1" product="HBM" />)
  fireEvent.click(screen.getByRole('button', { name: /export pdf/i }))
  await waitFor(() => expect(screen.getByText(/failed/i)).toBeInTheDocument())
})

test('button is disabled while exporting', async () => {
  ;(fetch as jest.Mock).mockReturnValueOnce(new Promise(() => {}))
  render(<ExportButton analysisId="a1" product="HBM" />)
  fireEvent.click(screen.getByRole('button', { name: /export pdf/i }))
  expect(screen.getByRole('button')).toBeDisabled()
})
