import { SignJWT } from 'jose'
import { getServerSession } from 'next-auth'
import { authOptions } from '@/lib/auth'

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const secret = new TextEncoder().encode(process.env.NEXTAUTH_SECRET ?? 'change-me')

async function makeBackendToken(userId: string): Promise<string> {
  return new SignJWT({ sub: userId })
    .setProtectedHeader({ alg: 'HS256' })
    .setExpirationTime('5m')
    .sign(secret)
}

export async function GET(
  _request: Request,
  { params }: { params: { id: string } }
): Promise<Response> {
  const session = await getServerSession(authOptions)
  if (!session?.user?.email) {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'content-type': 'application/json' },
    })
  }
  const userId = (session as { userId?: string }).userId ?? session.user.email
  const token = await makeBackendToken(userId)
  const backendRes = await fetch(`${BACKEND_URL}/api/v1/analyses/${params.id}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  const data = await backendRes.json().catch(() => ({}))
  return new Response(JSON.stringify(data), {
    status: backendRes.status,
    headers: { 'content-type': 'application/json' },
  })
}

export async function DELETE(
  _request: Request,
  { params }: { params: { id: string } }
): Promise<Response> {
  const session = await getServerSession(authOptions)
  if (!session?.user?.email) {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'content-type': 'application/json' },
    })
  }
  const userId = (session as { userId?: string }).userId ?? session.user.email
  const token = await makeBackendToken(userId)
  const backendRes = await fetch(`${BACKEND_URL}/api/v1/analyses/${params.id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
  })
  return new Response(null, { status: backendRes.status })
}
