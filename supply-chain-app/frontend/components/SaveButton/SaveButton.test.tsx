import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import SaveButton from './index'

global.fetch = jest.fn()

beforeEach(() => jest.clearAllMocks())

test('calls save API when clicked', async () => {
  ;(fetch as jest.Mock).mockResolvedValueOnce({
    ok: true,
    json: async () => ({ id: 'abc', product: 'HBM' }),
  })
  render(<SaveButton product="HBM" treeJson="{}" />)
  fireEvent.click(screen.getByRole('button', { name: /save/i }))
  await waitFor(() => expect(fetch).toHaveBeenCalled())
  expect((fetch as jest.Mock).mock.calls[0][0]).toContain('/analyses/save')
})

test('shows saving state while in flight', async () => {
  ;(fetch as jest.Mock).mockReturnValueOnce(new Promise(() => {}))
  render(<SaveButton product="HBM" treeJson="{}" />)
  fireEvent.click(screen.getByRole('button', { name: /save/i }))
  expect(screen.getByText(/saving/i)).toBeInTheDocument()
})

test('shows saved confirmation on success', async () => {
  ;(fetch as jest.Mock).mockResolvedValueOnce({
    ok: true,
    json: async () => ({ id: 'abc', product: 'HBM' }),
  })
  render(<SaveButton product="HBM" treeJson="{}" />)
  fireEvent.click(screen.getByRole('button', { name: /save/i }))
  await waitFor(() => expect(screen.getByText(/saved/i)).toBeInTheDocument())
})
