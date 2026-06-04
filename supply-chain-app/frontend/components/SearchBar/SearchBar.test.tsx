import { fireEvent, render, screen } from '@testing-library/react'
import SearchBar from './index'

test('calls onSearch with trimmed product name on submit', () => {
  const onSearch = jest.fn()
  render(<SearchBar onSearch={onSearch} loading={false} />)
  fireEvent.change(screen.getByRole('textbox'), {
    target: { value: '  iPhone 15 Pro  ' },
  })
  fireEvent.click(screen.getByRole('button', { name: /analyze/i }))
  expect(onSearch).toHaveBeenCalledWith('iPhone 15 Pro')
})

test('does not call onSearch when input is empty', () => {
  const onSearch = jest.fn()
  render(<SearchBar onSearch={onSearch} loading={false} />)
  fireEvent.click(screen.getByRole('button', { name: /analyze/i }))
  expect(onSearch).not.toHaveBeenCalled()
})

test('does not call onSearch when input is whitespace only', () => {
  const onSearch = jest.fn()
  render(<SearchBar onSearch={onSearch} loading={false} />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '   ' } })
  fireEvent.click(screen.getByRole('button', { name: /analyze/i }))
  expect(onSearch).not.toHaveBeenCalled()
})

test('disables button and shows loading text while loading', () => {
  render(<SearchBar onSearch={jest.fn()} loading={true} />)
  const button = screen.getByRole('button')
  expect(button).toBeDisabled()
  expect(button).toHaveTextContent(/analyzing/i)
})
