/**
 * session.ts
 *
 * Shared session bootstrap logic for tenant-aware auth.
 *
 * Flow:
 * 1. Check memory for accessToken (getAccessToken)
 * 2. If missing, call /api/auth/refresh (same-origin proxy) to get a new token via HttpOnly cookie
 * 3. If refresh succeeds, store the new accessToken in memory
 * 4. Call /api/auth/session with the accessToken to get user data
 *
 * This module is the single source of truth for session resolution.
 * Both the header and minha-conta pages use this instead of duplicating logic.
 */

import { getAccessToken, setAccessToken } from '@/lib/api/client'

export interface SessionUser {
  id: string
  email: string
  role: string
  siteId: string
}

export interface SessionArtist {
  id: string
  slug: string
  name: string
}

export interface SessionData {
  authenticated: true
  user: SessionUser
  artist: SessionArtist | null
}

export type SessionResult =
  | { status: 'authenticated'; data: SessionData }
  | { status: 'unauthenticated' }

/**
 * Bootstrap the session for a given tenant.
 *
 * 1. Checks in-memory accessToken
 * 2. If missing, attempts refresh via same-origin proxy (uses HttpOnly cookie)
 * 3. If token available, fetches session data from /api/auth/session
 *
 * Tenant-aware: only the cookie for the specified siteId is used.
 */
export async function bootstrapSession(siteId: string): Promise<SessionResult> {
  let token = getAccessToken()

  // Step 1: If no token in memory, try refresh via proxy
  if (!token) {
    token = await tryRefreshViaProxy(siteId)
  }

  // Step 2: If still no token, user is unauthenticated
  if (!token) {
    return { status: 'unauthenticated' }
  }

  // Step 3: Fetch session data
  try {
    const res = await fetch('/api/auth/session', {
      headers: {
        'Authorization': `Bearer ${token}`,
        'X-Site-Id': siteId,
      },
    })

    if (!res.ok) {
      // Token might be expired — try refresh once
      if (res.status === 401) {
        const refreshedToken = await tryRefreshViaProxy(siteId)
        if (!refreshedToken) {
          setAccessToken(null)
          return { status: 'unauthenticated' }
        }

        // Retry session with new token
        const retryRes = await fetch('/api/auth/session', {
          headers: {
            'Authorization': `Bearer ${refreshedToken}`,
            'X-Site-Id': siteId,
          },
        })

        if (!retryRes.ok) {
          setAccessToken(null)
          return { status: 'unauthenticated' }
        }

        const retryData = await retryRes.json() as SessionData & { authenticated?: boolean }
        if (retryData.authenticated && retryData.user) {
          return { status: 'authenticated', data: retryData as SessionData }
        }
      }

      setAccessToken(null)
      return { status: 'unauthenticated' }
    }

    const data = await res.json() as SessionData & { authenticated?: boolean }
    if (data.authenticated && data.user) {
      return { status: 'authenticated', data: data as SessionData }
    }

    return { status: 'unauthenticated' }
  } catch {
    return { status: 'unauthenticated' }
  }
}

/**
 * Attempts to get a new accessToken via the refresh proxy.
 * The proxy forwards the HttpOnly refresh cookie to the Fastify API.
 * Returns the new accessToken or null if refresh failed.
 */
async function tryRefreshViaProxy(siteId: string): Promise<string | null> {
  try {
    const res = await fetch('/api/auth/refresh', {
      method: 'POST',
      credentials: 'include', // sends HttpOnly cookies
      headers: {
        'Content-Type': 'application/json',
        'X-Site-Id': siteId,
      },
    })

    if (!res.ok) return null

    const data = await res.json() as { accessToken?: string }
    if (data.accessToken) {
      setAccessToken(data.accessToken)
      return data.accessToken
    }

    return null
  } catch {
    return null
  }
}
