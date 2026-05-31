import { render, screen } from '@testing-library/react'
import { useSession } from 'next-auth/react'
import AuthButton from './index'

jest.mock('next-auth/react')
const mockUseSession = useSession as jest.Mock

test('shows Sign In when unauthenticated', () => {
  mockUseSession.mockReturnValue({ data: null, status: 'unauthenticated' })
  render(<AuthButton />)
  expect(screen.getByText(/sign in/i)).toBeInTheDocument()
})

test('shows user name when authenticated', () => {
  mockUseSession.mockReturnValue({
    data: { user: { name: 'Alice', email: 'alice@example.com' } },
    status: 'authenticated',
  })
  render(<AuthButton />)
  expect(screen.getByText('Alice')).toBeInTheDocument()
})

test('shows loading state while session loading', () => {
  mockUseSession.mockReturnValue({ data: null, status: 'loading' })
  render(<AuthButton />)
  expect(screen.getByText(/loading/i)).toBeInTheDocument()
})
