'use client'

import { signIn, signOut, useSession } from 'next-auth/react'

export default function AuthButton() {
  const { data: session, status } = useSession()

  if (status === 'loading') return <span className="text-sm text-gray-500">Loading…</span>

  if (session?.user) {
    return (
      <div className="flex items-center gap-3">
        <span className="text-sm">{session.user.name}</span>
        <button
          onClick={() => signOut()}
          className="rounded border px-3 py-1 text-sm hover:bg-gray-100"
        >
          Sign out
        </button>
      </div>
    )
  }

  return (
    <button
      onClick={() => signIn('google')}
      className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700"
    >
      Sign in
    </button>
  )
}
