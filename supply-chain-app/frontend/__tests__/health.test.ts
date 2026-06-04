/**
 * @jest-environment node
 */
import { GET } from '../app/api/health/route'

test('health route returns status ok', async () => {
  const res = GET()
  const data = await res.json()
  expect(data.status).toBe('ok')
})
