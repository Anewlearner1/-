/**
 * @jest-environment node
 */

jest.mock('puppeteer', () => ({
  launch: jest.fn().mockResolvedValue({
    newPage: jest.fn().mockResolvedValue({
      goto: jest.fn().mockResolvedValue(null),
      pdf: jest.fn().mockResolvedValue(Buffer.from('%PDF-test')),
      setViewport: jest.fn().mockResolvedValue(null),
    }),
    close: jest.fn().mockResolvedValue(null),
  }),
}))

import { GET } from './route'

test('returns application/pdf content-type', async () => {
  const request = new Request('http://localhost/api/export?id=a1')
  const response = await GET(request)
  expect(response.headers.get('content-type')).toBe('application/pdf')
})

test('sets Content-Disposition attachment header', async () => {
  const request = new Request('http://localhost/api/export?id=a1')
  const response = await GET(request)
  const disposition = response.headers.get('content-disposition') ?? ''
  expect(disposition).toMatch(/attachment/)
  expect(disposition).toMatch(/\.pdf/)
})

test('returns 400 when id param is missing', async () => {
  const request = new Request('http://localhost/api/export')
  const response = await GET(request)
  expect(response.status).toBe(400)
})
